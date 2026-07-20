# -*- coding: utf-8 -*-
"""
@file    context.py
@brief   factory-3d 上下文装配:布局/模拟器/告警引擎/降级阶梯/事件流/助手
         的统一装配与状态变更单一入口(toggle/外部注入/模拟器同驱状态机)。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import datetime
import hashlib
import hmac
import secrets
import time

from gd_crypto import (decrypt_envelope, encrypt_envelope,
                       envelope_from_json, envelope_to_json)
from gd_policy.service import SettingsService
from gd_storage.audit import AuditWriter

from apps.factory3d import layout as lo
from apps.factory3d import stream
from apps.factory3d.alarms import AlarmEngine
from apps.factory3d.render_ladder import TIERS, DegradeLadder
from apps.factory3d.simulator import STATUS_OFFLINE, STATUS_ONLINE, Simulator

F3D_VER = "5.0.0-m6"
EXTERNAL_SIG_TOLERANCE_SECONDS = 300     # 对外注入时间戳容差(同 nvr 惯例)
EXTERNAL_KEY_AAD = b"f3d-external"


class F3dContext:
    """全部 F3D 服务的装配与横切入口。"""

    def __init__(self, db, suite, ring=None, settings=None, environ=None):
        """@brief 装配服务(settings 缺省时按统一策略层新建)"""
        self.db = db
        self.suite = suite
        self.ring = ring
        self.settings = settings or SettingsService(db, environ=environ)
        self.audit = AuditWriter(db, suite)
        self.layouts = lo.LayoutService(db)
        doc, self.data_rev = self.layouts.get()
        self.simulator = Simulator(doc)
        self.alarms = AlarmEngine(db, self.settings)
        self.ladder = DegradeLadder(self.settings, on_change=self._tier_event)
        self.edit_session_active = False     # 编辑会话开关(L03 §4,409 语义)

    # ---- 横切入口 ---------------------------------------------------------
    def _tier_event(self, old_tier: int, new_tier: int, fps: float,
                    now: float):
        """@brief 档位变更入事件流(R-F3D-1 档位事件可查)"""
        stream.record_event(
            self.db, "tier_change", from_status=TIERS[old_tier],
            to_status=TIERS[new_tier], detail=f"fps={fps:.1f}", ts=now)

    def apply_status(self, device_id: str, status: str, source: str,
                     now: float = None) -> tuple:
        """
        @brief  状态变更单一入口:运行时 → 告警状态机 → 事件流
        @param  source toggle|external|simulator
        @return (old, new)
        """
        now = time.time() if now is None else now
        manual = source == "toggle"
        old, new = self.simulator.set_status(device_id, status, manual=manual,
                                             now=now)
        if old != new:
            state = self.simulator.runtime[device_id]
            stream.record_event(self.db, "status_change", device_id=device_id,
                                building=state["building"], from_status=old,
                                to_status=new, detail=source, ts=now)
            if new == STATUS_OFFLINE:
                self.alarms.on_offline(device_id, now=now)
            elif new == STATUS_ONLINE:
                self.alarms.on_online(device_id, now=now)
        return old, new

    def toggle_device(self, device_id: str, now: float = None) -> tuple:
        """@brief 模拟掉线/恢复(标记手动,模拟器不再自动改)"""
        state = self.simulator.runtime[device_id]
        target = (STATUS_ONLINE if state["status"] == STATUS_OFFLINE
                  else STATUS_OFFLINE)
        return self.apply_status(device_id, target, "toggle", now=now)

    def tick(self, now: float = None) -> list:
        """@brief 一个采集周期:模拟器指标游走 + pending→active 推进"""
        now = time.time() if now is None else now
        if self.settings.get("f3d_connection_mode") == "simulator":
            self.simulator.tick(now=now)
        promoted = self.alarms.tick(now=now)
        for device_id in promoted:
            building = self.simulator.runtime.get(device_id, {}).get(
                "building", "")
            stream.record_event(self.db, "alarm_active", device_id=device_id,
                                building=building, from_status="pending",
                                to_status="active", ts=now)
        return promoted

    def layout_changed(self, doc: dict, data_rev: int):
        """@brief 布局落库后的运行时联动(重建模拟器/清删除设备告警)"""
        known = set(self.simulator.runtime)
        self.data_rev = data_rev
        self.simulator.rebuild(doc)
        for device_id in known - set(self.simulator.runtime):
            self.alarms.device_removed(device_id)

    def kpi(self) -> dict:
        """@brief KPI 四枚(告警数=active)"""
        result = self.simulator.kpi()
        result["alarm"] = self.alarms.counts()["active"]
        return result

    # ---- 外部注入密钥(HMAC,五要素同 nvr /public/v1 惯例) ---------------
    def create_external_key(self) -> dict:
        """@brief 创建注入密钥:明文仅此一次,密文信封落库"""
        if self.ring is None:
            raise ValueError("未配置主密钥环,无法创建注入密钥")
        key_id = f"f3dk-{secrets.token_hex(4)}"
        secret = secrets.token_urlsafe(24)
        envelope = encrypt_envelope(secret.encode(), self.ring, self.suite,
                                    aad=EXTERNAL_KEY_AAD)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.db.execute(
            "INSERT INTO f3d_external_keys(key_id, secret_ct, enabled,"
            " created_at) VALUES(?, ?, 1, ?)",
            (key_id, envelope_to_json(envelope), now))
        return {"key_id": key_id, "secret": secret}

    def revoke_external_key(self, key_id: str) -> bool:
        """@brief 吊销注入密钥"""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        rows = self.db.query(
            "SELECT id FROM f3d_external_keys WHERE key_id = ? AND enabled = 1",
            (key_id,))
        if not rows:
            return False
        self.db.execute(
            "UPDATE f3d_external_keys SET enabled = 0, revoked_at = ?"
            " WHERE key_id = ?", (now, key_id))
        return True

    def verify_external(self, key_id: str, timestamp: str, signature: str,
                        body: bytes, now: float = None) -> bool:
        """
        @brief  校验注入签名 sha256=HMAC(secret, "{ts}." + body),容差 300s;
                失败一律 False(web 层统一 401「鉴权失败」,不泄漏细节)
        """
        now = time.time() if now is None else now
        rows = self.db.query(
            "SELECT secret_ct FROM f3d_external_keys"
            " WHERE key_id = ? AND enabled = 1", (key_id,))
        if not rows or self.ring is None:
            return False
        try:
            ts_value = float(timestamp)
        except (TypeError, ValueError):
            return False
        if abs(now - ts_value) > EXTERNAL_SIG_TOLERANCE_SECONDS:
            return False
        try:
            secret = decrypt_envelope(envelope_from_json(rows[0][0]),
                                      self.ring, aad=EXTERNAL_KEY_AAD)
        except (ValueError, KeyError):
            return False
        digest = hmac.new(secret, f"{timestamp}.".encode() + body,
                          hashlib.sha256).hexdigest()
        return hmac.compare_digest(f"sha256={digest}", signature or "")
