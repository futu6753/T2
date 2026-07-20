# -*- coding: utf-8 -*-
"""
@file    ingest.py
@brief   通道台账 + 推送设备接入(L04 §7):
         通道自动发现(检测结果同步;删除标记 removed 保留历史;
         IP 0.0.0.0/空忽略;跃迁入统一时间线 channel_change)。
         推送:仅状态变化或非 online 上报才落库(source=push 防心跳灌爆);
         超阈值时长判「推送设备离线」同套去抖;恢复推送当场解除告警;
         新建有宽限时长首联宽限(显示未检测);TCP 行协议三种格式
         (纯 token / token+空格+JSON / 整行 JSON),每行一答。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import json
from datetime import datetime, timezone

from gd_common.jsonlog import get_logger
from apps.nvr.checker import STATUS_UNCHECKED

_log = get_logger("nvr.ingest")

PUSH_STATUSES = ("online", "offline", "abnormal")


def _now() -> str:
    """@brief UTC ISO"""
    return datetime.now(timezone.utc).isoformat()


class ChannelService:
    """录像通道台账。"""

    def __init__(self, db):
        """@brief 注入存储"""
        self._db = db

    def sync_from_check(self, device_id: int, check: dict):
        """
        @brief  检测结果同步通道:离线清单标 offline、其余在册标 online;
                本体不可达时置 unknown(不误触发,契约)
        """
        now = _now()
        if check["status"] != "online":
            self._db.execute(
                "UPDATE nvr_channels SET status = 'unknown', last_seen = ?"
                " WHERE device_id = ? AND removed = 0", (now, device_id))
            return
        offline = set(check.get("offline_channels") or [])
        rows = self._db.query(
            "SELECT channel_no, status FROM nvr_channels"
            " WHERE device_id = ? AND removed = 0", (device_id,))
        known = {row[0]: row[1] for row in rows}
        for channel_no in offline:
            self.upsert(device_id, channel_no, status="offline")
        for channel_no, previous in known.items():
            if channel_no not in offline:
                self.upsert(device_id, channel_no, status="online")

    def upsert(self, device_id: int, channel_no: int, name: str = "",
               ip: str = "", status: str = "unknown"):
        """@brief 发现/更新通道(IP 0.0.0.0 或空槽位忽略;跃迁入时间线)"""
        if ip == "0.0.0.0":
            return
        now = _now()
        rows = self._db.query(
            "SELECT status FROM nvr_channels WHERE device_id = ?"
            " AND channel_no = ?", (device_id, channel_no))
        previous = rows[0][0] if rows else None
        self._db.execute(
            "INSERT INTO nvr_channels(device_id, channel_no, name, ip,"
            " status, first_seen, last_seen) VALUES(?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(device_id, channel_no) DO UPDATE SET"
            " status = excluded.status, last_seen = excluded.last_seen,"
            " removed = 0",
            (device_id, channel_no, name, ip, status, now, now))
        if previous is not None and previous != status:
            self._db.execute(
                "INSERT INTO nvr_timeline(device_id, event_type, channel_no,"
                " from_status, to_status, occurred_at)"
                " VALUES(?, 'channel_change', ?, ?, ?, ?)",
                (device_id, channel_no, previous, status, now))

    def mark_removed(self, device_id: int, present_channels: list):
        """@brief 本轮未见的在册通道标记 removed(保留历史)"""
        rows = self._db.query(
            "SELECT channel_no FROM nvr_channels WHERE device_id = ?"
            " AND removed = 0", (device_id,))
        for (channel_no,) in rows:
            if channel_no not in present_channels:
                self._db.execute(
                    "UPDATE nvr_channels SET removed = 1 WHERE device_id = ?"
                    " AND channel_no = ?", (device_id, channel_no))

    def list(self, device_id: int = None, status: str = None,
             include_removed: bool = False) -> dict:
        """@brief 通道列表+summary"""
        conditions, params = [], []
        if device_id:
            conditions.append("device_id = ?")
            params.append(device_id)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if not include_removed:
            conditions.append("removed = 0")
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._db.query(
            "SELECT id, device_id, channel_no, name, ip, status, removed,"
            f" first_seen, last_seen FROM nvr_channels{where}"
            " ORDER BY device_id, channel_no", tuple(params))
        channels = [dict(zip(("id", "device_id", "channel_no", "name", "ip",
                              "status", "removed", "first_seen", "last_seen"),
                             row)) for row in rows]
        summary = {}
        for channel in channels:
            summary[channel["status"]] = summary.get(channel["status"], 0) + 1
        return {"channels": channels, "summary": summary,
                "total": len(channels)}


class PushIngest:
    """推送设备上报处理(HTTP 与 TCP 共用)。"""

    def __init__(self, db, devices, alert_engine):
        """@brief 注入存储/设备/告警引擎"""
        self._db = db
        self._devices = devices
        self._alerts = alert_engine

    def handle(self, token: str, payload: dict = None) -> dict:
        """
        @brief  一次上报(空载荷=心跳 online)
        @return {ok, device_id, status, received_at} 或 {ok: False, error}
        """
        device = self._devices.by_push_token(token or "")
        if device is None or device["kind"] != "push":
            return {"ok": False, "error": "token 无效"}
        status = (payload or {}).get("status", "online")
        if status not in PUSH_STATUSES:
            return {"ok": False, "error": f"非法状态: {status}"}
        detail = str((payload or {}).get("detail", ""))[:200]
        previous = self._devices.state_of(device["id"])["status"]
        received_at = _now()
        # 仅状态变化或非 online 才落明细(防心跳灌爆,契约)
        if status != previous or status != "online":
            outcome = self._devices.record_check(
                device["id"], status, detail or "推送上报", 0, source="push")
            self._alerts.on_check(device, outcome)
        else:
            self._touch_heartbeat(device["id"], received_at)
        if status == "online":
            self._alerts.resolve_now(device)     # 恢复推送当场解除(契约)
        return {"ok": True, "device_id": device["id"], "status": status,
                "received_at": received_at}

    def _touch_heartbeat(self, device_id: int, at: str):
        """@brief 心跳仅刷新 last_checked_at(不落明细)"""
        self._db.execute(
            "UPDATE nvr_device_state SET last_checked_at = ?"
            " WHERE device_id = ?", (at, device_id))

    def overdue_check(self) -> list:
        """
        @brief  超阈值未上报判「推送设备离线」(同套状态机与去抖);
                新建从未上报显示未检测(首联宽限,契约)
        @return 判离线的设备名列表
        """
        flagged = []
        for device in self._devices.list(enabled=True):
            if device["kind"] != "push" or not device["push_grace_seconds"]:
                continue
            state = self._devices.state_of(device["id"])
            if state["last_checked_at"] is None:
                continue                     # 首联宽限:保持未检测
            last = datetime.fromisoformat(state["last_checked_at"])
            silent = (datetime.now(timezone.utc) - last).total_seconds()
            if silent > device["push_grace_seconds"] \
                    and state["status"] != "offline":
                outcome = self._devices.record_check(
                    device["id"], "offline",
                    f"超 {device['push_grace_seconds']} 秒未上报", 0,
                    source="push")
                self._alerts.on_check(device, outcome)
                flagged.append(device["name"])
        return flagged


def parse_tcp_line(line: bytes, max_line_bytes: int = 65536) -> tuple:
    """
    @brief  TCP 行协议解析(三种格式,每行一答):
            ①纯 token ②token+空格+JSON ③整行 JSON(含 token 字段)
    @return (token, payload) 或 (None, {"error": …})
    """
    if len(line) > max_line_bytes:
        return None, {"error": "行超长"}
    text = line.decode("utf-8", errors="replace").strip()
    if not text:
        return None, {"error": "空行"}
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None, {"error": "JSON 解析失败"}
        return payload.pop("token", None), payload
    if " " in text:
        token, rest = text.split(" ", 1)
        try:
            return token, json.loads(rest)
        except json.JSONDecodeError:
            return None, {"error": "JSON 解析失败"}
    return text, {}
