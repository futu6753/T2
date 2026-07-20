# -*- coding: utf-8 -*-
"""
@file    devices.py
@brief   设备台账与状态机(L04 §4/§7):CRUD(密码信封加密,任何接口不返回
         密码;PUT 传 password=轮换;推送设备响应含 token 而 NVR 无该字段)、
         每次检测=明细+状态机(since/consecutive_fails)+跃迁时间线、
         删除级联清历史、保留期清理(retention_days=90,0=永久且时间线不清)。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import secrets
from datetime import datetime, timedelta, timezone

from gd_common.errors import PolicyValidationError
from gd_crypto import (
    decrypt_envelope, encrypt_envelope, envelope_from_json, envelope_to_json,
)
from apps.nvr.checker import STATUS_UNCHECKED

DEVICE_AAD = b"nvr_device_password"
DEVICE_COLUMNS = ("id, name, kind, host, port, username, region, station,"
                  " enabled, push_token, push_grace_seconds, created_at")


def _now() -> datetime:
    """@brief UTC 当前时间"""
    return datetime.now(timezone.utc)


def _row_to_device(row: tuple) -> dict:
    """@brief 行 → 设备字典(不含密码)"""
    keys = [key.strip() for key in DEVICE_COLUMNS.split(",")]
    device = dict(zip(keys, row))
    if device["kind"] != "push":
        device.pop("push_token")             # NVR 无 token 字段(契约)
    return device


class DeviceService:
    """设备 CRUD + 状态机 + 时间线。"""

    def __init__(self, db, ring, suite):
        """@brief 注入存储与密钥环"""
        self._db = db
        self._ring = ring
        self._suite = suite

    # ---- CRUD -----------------------------------------------------------
    def create(self, payload: dict) -> dict:
        """@brief 建设备;推送设备自动发 token,首联宽限支持"""
        kind = payload.get("kind", "nvr")
        if kind not in ("nvr", "push"):
            raise PolicyValidationError(f"未知设备类型: {kind}")
        token = secrets.token_urlsafe(24) if kind == "push" else None
        password_ct = self._seal_password(payload.get("password", "")) \
            if payload.get("password") else ""
        now = _now().isoformat()
        self._db.execute(
            "INSERT INTO nvr_devices(name, kind, host, port, username,"
            " password_ct, region, station, enabled, push_token,"
            " push_grace_seconds, created_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (payload.get("name", ""), kind, payload.get("host", ""),
             int(payload.get("port", 80)), payload.get("username", ""),
             password_ct, payload.get("region", ""),
             payload.get("station", ""),
             1 if payload.get("enabled", True) else 0, token,
             int(payload.get("push_grace_seconds", 0)), now))
        row = self._db.query(
            f"SELECT {DEVICE_COLUMNS} FROM nvr_devices"
            " ORDER BY id DESC LIMIT 1")[0]
        device_id = row[0]
        self._db.execute(
            "INSERT INTO nvr_device_state(device_id, status, since)"
            " VALUES(?, ?, ?)", (device_id, STATUS_UNCHECKED, now))
        return _row_to_device(row)

    def update(self, device_id: int, payload: dict) -> dict:
        """@brief 部分更新;password 传入=轮换密文"""
        current = self.get(device_id)
        if current is None:
            raise PolicyValidationError("设备不存在")
        fields, params = [], []
        for column in ("name", "host", "username", "region", "station"):
            if column in payload:
                fields.append(f"{column} = ?")
                params.append(payload[column])
        for column in ("port", "push_grace_seconds"):
            if column in payload:
                fields.append(f"{column} = ?")
                params.append(int(payload[column]))
        if "enabled" in payload:
            fields.append("enabled = ?")
            params.append(1 if payload["enabled"] else 0)
        if payload.get("password"):
            fields.append("password_ct = ?")
            params.append(self._seal_password(payload["password"]))
        if fields:
            params.append(device_id)
            self._db.execute(
                f"UPDATE nvr_devices SET {', '.join(fields)} WHERE id = ?",
                tuple(params))
        return self.get(device_id)

    def delete(self, device_id: int):
        """@brief 删除设备级联清历史(L04 §7:建议停用代替)"""
        for table in ("nvr_check_results", "nvr_timeline", "nvr_channels",
                      "nvr_alerts"):
            self._db.execute(f"DELETE FROM {table} WHERE device_id = ?",
                             (device_id,))
        self._db.execute("DELETE FROM nvr_device_state WHERE device_id = ?",
                         (device_id,))
        self._db.execute("DELETE FROM nvr_devices WHERE id = ?", (device_id,))

    def get(self, device_id: int) -> dict:
        """@brief 单设备(不含密码)"""
        rows = self._db.query(
            f"SELECT {DEVICE_COLUMNS} FROM nvr_devices WHERE id = ?",
            (device_id,))
        return _row_to_device(rows[0]) if rows else None

    def list(self, region: str = None, station: str = None,
             enabled=None) -> list:
        """@brief 列表(过滤 region/station/enabled)"""
        conditions, params = [], []
        if region:
            conditions.append("region = ?")
            params.append(region)
        if station:
            conditions.append("station = ?")
            params.append(station)
        if enabled is not None:
            conditions.append("enabled = ?")
            params.append(1 if enabled else 0)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._db.query(
            f"SELECT {DEVICE_COLUMNS} FROM nvr_devices{where} ORDER BY id",
            tuple(params))
        return [_row_to_device(row) for row in rows]

    def by_push_token(self, token: str) -> dict:
        """@brief 推送 token → 设备"""
        rows = self._db.query(
            f"SELECT {DEVICE_COLUMNS} FROM nvr_devices WHERE push_token = ?",
            (token,))
        return dict(zip([k.strip() for k in DEVICE_COLUMNS.split(",")],
                        rows[0])) if rows else None

    # ---- 密码信封 -------------------------------------------------------
    def _seal_password(self, password: str) -> str:
        """@brief 设备密码信封加密(H04:静态凭据必加密)"""
        envelope = encrypt_envelope(password.encode(), self._ring,
                                    self._suite, aad=DEVICE_AAD)
        return envelope_to_json(envelope)

    def open_password(self, device_id: int) -> str:
        """@brief 解密设备密码(仅巡检内部用;解密失败抛出由巡检隔离)"""
        rows = self._db.query(
            "SELECT password_ct FROM nvr_devices WHERE id = ?", (device_id,))
        if not rows or not rows[0][0]:
            return ""
        return decrypt_envelope(envelope_from_json(rows[0][0]), self._ring,
                                aad=DEVICE_AAD).decode()

    # ---- 状态机 + 时间线 ------------------------------------------------
    def record_check(self, device_id: int, status: str, detail: str,
                     latency_ms: int, source: str = "patrol") -> dict:
        """
        @brief  一次检测入账:明细 + 状态机推进 + 跃迁时间线
        @return {changed, from_status, to_status, consecutive_fails, since}
        """
        now = _now().isoformat()
        self._db.execute(
            "INSERT INTO nvr_check_results(device_id, status, source, detail,"
            " latency_ms, checked_at) VALUES(?, ?, ?, ?, ?, ?)",
            (device_id, status, source, detail, latency_ms, now))
        rows = self._db.query(
            "SELECT status, since, consecutive_fails FROM nvr_device_state"
            " WHERE device_id = ?", (device_id,))
        previous, since, fails = rows[0] if rows else (STATUS_UNCHECKED, now, 0)
        fails = 0 if status == "online" else fails + 1
        changed = status != previous
        if changed:
            since = now
            self._db.execute(
                "INSERT INTO nvr_timeline(device_id, event_type, from_status,"
                " to_status, detail, occurred_at)"
                " VALUES(?, 'status_change', ?, ?, ?, ?)",
                (device_id, previous, status, detail, now))
        self._db.execute(
            "INSERT INTO nvr_device_state(device_id, status, since,"
            " consecutive_fails, last_checked_at, last_detail)"
            " VALUES(?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(device_id) DO UPDATE SET status = excluded.status,"
            " since = excluded.since,"
            " consecutive_fails = excluded.consecutive_fails,"
            " last_checked_at = excluded.last_checked_at,"
            " last_detail = excluded.last_detail",
            (device_id, status, since, fails, now, detail))
        return {"changed": changed, "from_status": previous,
                "to_status": status, "consecutive_fails": fails,
                "since": since}

    def state_of(self, device_id: int) -> dict:
        """@brief 当前状态机快照"""
        rows = self._db.query(
            "SELECT status, since, consecutive_fails, last_checked_at,"
            " last_detail FROM nvr_device_state WHERE device_id = ?",
            (device_id,))
        if not rows:
            return {"status": STATUS_UNCHECKED, "since": None,
                    "consecutive_fails": 0, "last_checked_at": None,
                    "last_detail": ""}
        return {"status": rows[0][0], "since": rows[0][1],
                "consecutive_fails": rows[0][2], "last_checked_at": rows[0][3],
                "last_detail": rows[0][4]}

    def offline_seconds(self, device_id: int) -> float:
        """@brief 非在线持续秒数(窗口起点从时间线状态推导;在线返回 0)"""
        state = self.state_of(device_id)
        if state["status"] in ("online", STATUS_UNCHECKED) \
                or not state["since"]:
            return 0.0
        since = datetime.fromisoformat(state["since"])
        return (_now() - since).total_seconds()

    def prune(self, retention_days: int) -> int:
        """@brief 巡检明细保留期清理(0=永久;时间线不清理)@return 删除行数"""
        if retention_days <= 0:
            return 0
        cutoff = (_now() - timedelta(days=retention_days)).isoformat()
        before = self._db.query(
            "SELECT COUNT(*) FROM nvr_check_results WHERE checked_at < ?",
            (cutoff,))[0][0]
        self._db.execute(
            "DELETE FROM nvr_check_results WHERE checked_at < ?", (cutoff,))
        return before
