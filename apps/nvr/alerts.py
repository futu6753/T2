# -*- coding: utf-8 -*-
"""
@file    alerts.py
@brief   告警引擎(L04 §7):每设备每 scope 至多一条活动告警(部分唯一索引
         兜底);活动期间子状态变化不重复告警;恢复立即解除+恢复通知(带故障
         总时长);手动检测同样驱动;通道告警 scope=channel 与设备本体并存,
         正文点名 ≤5 路,NVR 不可达时通道置 unknown 不误触发;
         通知投递交 dispatch(失败仅记日志)。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
from datetime import datetime, timezone

from gd_common.jsonlog import get_logger
from apps.nvr.debounce import (
    DebouncePolicy, flap_rate_from_timeline, next_ewma,
)

_log = get_logger("nvr.alerts")

SCOPE_DEVICE = "device"
SCOPE_CHANNEL = "channel"


def _now() -> str:
    """@brief UTC ISO 时间"""
    return datetime.now(timezone.utc).isoformat()


class AlertEngine:
    """去抖驱动的告警生命周期。"""

    def __init__(self, db, devices, policy: DebouncePolicy,
                 dispatcher=None, recovery_notice: bool = True,
                 channel_alerts: bool = True):
        """@brief 注入存储/设备服务/去抖策略/派发器"""
        self._db = db
        self._devices = devices
        self._policy = policy
        self._dispatcher = dispatcher
        self._recovery_notice = recovery_notice
        self._channel_alerts = channel_alerts

    # ---- 主入口:每次检测后驱动 -----------------------------------------
    def on_check(self, device: dict, check_outcome: dict,
                 offline_channels: list = None):
        """
        @brief  一次检测结果驱动设备/通道两条告警线
        @param  check_outcome record_check 返回(to_status 等)
        """
        snapshot = self._build_snapshot(device["id"], check_outcome)
        active = self.active_alert(device["id"], SCOPE_DEVICE)
        if snapshot["status"] == "online":
            if active and self._policy.should_resolve(snapshot):
                self._resolve(active, device)
        else:
            if active is None and self._policy.should_fire(snapshot):
                self._fire(device, SCOPE_DEVICE, snapshot["status"],
                           check_outcome.get("detail", ""))
            # 活动期间子状态变化不重复告警(契约):仅更新 trigger detail
        if self._channel_alerts:
            self._drive_channel_alerts(device, snapshot["status"],
                                       offline_channels or [])

    def _build_snapshot(self, device_id: int, outcome: dict) -> dict:
        """@brief 组装去抖快照(EWMA 递推落库,重启续算)"""
        state = self._devices.state_of(device_id)
        rows = self._db.query(
            "SELECT ewma FROM nvr_device_state WHERE device_id = ?",
            (device_id,))
        previous_ewma = rows[0][0] if rows else 0.0
        is_failure = outcome["to_status"] != "online"
        ewma = next_ewma(previous_ewma, is_failure)
        self._db.execute(
            "UPDATE nvr_device_state SET ewma = ? WHERE device_id = ?",
            (ewma, device_id))
        consecutive_ok = 0 if is_failure else 1
        if not is_failure and outcome.get("from_status") == "online":
            consecutive_ok = 2               # 连续在线(滞回恢复满足)
        return {"status": outcome["to_status"],
                "consecutive_fails": outcome["consecutive_fails"],
                "offline_seconds": self._devices.offline_seconds(device_id),
                "ewma": ewma, "consecutive_ok": consecutive_ok,
                "flap_rate": flap_rate_from_timeline(self._db, device_id)}

    # ---- 生命周期 -------------------------------------------------------
    def _fire(self, device: dict, scope: str, trigger_status: str,
              detail: str):
        """@brief 建活动告警 + 投递(部分唯一索引防并发双开)"""
        try:
            self._db.execute(
                "INSERT INTO nvr_alerts(device_id, scope, state,"
                " trigger_status, detail, started_at)"
                " VALUES(?, ?, 'firing', ?, ?, ?)",
                (device["id"], scope, trigger_status, detail, _now()))
        except Exception:                    # 唯一索引兜底:并发双开忽略
            _log.info("活动告警已存在,跳过重复触发",
                      extra={"ctx": {"device": device["id"], "scope": scope}})
            return
        alert = self.active_alert(device["id"], scope)
        _log.warning("告警触发", extra={"ctx": {
            "device": device["name"], "scope": scope,
            "status": trigger_status}})
        if self._dispatcher:
            self._dispatcher.enqueue(alert, device, kind="firing")

    def _resolve(self, alert: dict, device: dict):
        """@brief 解除:计故障总时长 + 恢复通知"""
        started = datetime.fromisoformat(alert["started_at"])
        duration = int((datetime.now(timezone.utc) - started).total_seconds())
        self._db.execute(
            "UPDATE nvr_alerts SET state = 'resolved', resolved_at = ?,"
            " duration_seconds = ? WHERE id = ?",
            (_now(), duration, alert["id"]))
        _log.info("告警解除", extra={"ctx": {
            "device": device["name"], "duration_seconds": duration}})
        if self._recovery_notice and self._dispatcher:
            alert = dict(alert)
            alert["duration_seconds"] = duration
            self._dispatcher.enqueue(alert, device, kind="resolved")

    def resolve_now(self, device: dict, scope: str = SCOPE_DEVICE):
        """@brief 立即解除(推送设备恢复上报当场解除,L04 §7)"""
        active = self.active_alert(device["id"], scope)
        if active:
            self._resolve(active, device)

    # ---- 通道线 ---------------------------------------------------------
    def _drive_channel_alerts(self, device: dict, device_status: str,
                              offline_channels: list):
        """@brief 通道独立告警(NVR 不可达 → unknown 不误触发)"""
        active = self.active_alert(device["id"], SCOPE_CHANNEL)
        if device_status != "online":
            return                          # 本体不可达:通道 unknown,不动作
        if offline_channels:
            if active is None:
                names = ", ".join(f"通道{no}" for no in offline_channels[:5])
                extra = "" if len(offline_channels) <= 5 \
                    else f" 等共 {len(offline_channels)} 路"
                self._fire(device, SCOPE_CHANNEL, "channel_offline",
                           f"离线录像通道: {names}{extra}")
        elif active is not None:
            self._resolve(active, device)

    # ---- 查询 -----------------------------------------------------------
    def active_alert(self, device_id: int, scope: str) -> dict:
        """@brief 该设备该 scope 的活动告警"""
        rows = self._db.query(
            "SELECT id, device_id, scope, state, trigger_status, detail,"
            " started_at, resolved_at, duration_seconds FROM nvr_alerts"
            " WHERE device_id = ? AND scope = ? AND state = 'firing'",
            (device_id, scope))
        return self._row(rows[0]) if rows else None

    def list_alerts(self, state: str = None, scope: str = None,
                    device_id: int = None, limit: int = 100) -> list:
        """@brief 告警列表(过滤 state/scope/device)"""
        conditions, params = [], []
        if state:
            conditions.append("state = ?")
            params.append(state)
        if scope:
            conditions.append("scope = ?")
            params.append(scope)
        if device_id:
            conditions.append("device_id = ?")
            params.append(device_id)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(min(limit, 500))
        rows = self._db.query(
            "SELECT id, device_id, scope, state, trigger_status, detail,"
            f" started_at, resolved_at, duration_seconds FROM nvr_alerts{where}"
            " ORDER BY id DESC LIMIT ?", tuple(params))
        return [self._row(row) for row in rows]

    def _row(self, row: tuple) -> dict:
        """@brief 行 → 告警字典(附实时持续秒)"""
        alert = dict(zip(("id", "device_id", "scope", "state",
                          "trigger_status", "detail", "started_at",
                          "resolved_at", "duration_seconds"), row))
        if alert["state"] == "firing":
            started = datetime.fromisoformat(alert["started_at"])
            alert["duration_seconds"] = int(
                (datetime.now(timezone.utc) - started).total_seconds())
        return alert
