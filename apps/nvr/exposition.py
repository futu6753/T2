# -*- coding: utf-8 -*-
"""
@file    exposition.py
@brief   ①Prometheus 指标(L04 §4):文本格式 0.0.4 手写零依赖,需登录或
         Bearer(常数时间比较);nvrm_devices_*、nvrm_alerts_active(_by_scope)、
         nvrm_patrol_*、设备/通道标签级指标、process_start_time_seconds。
         ②对外只读 /public/v1(HMAC-SHA256):待签串五行
         METHOD\\nPATH\\n排序k=v&…\\nTS\\nsha256(body),容差 300s;失败一律
         401「鉴权失败」不区分原因并记 public_auth_failed。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import hashlib
import hmac
import time

from gd_common.jsonlog import get_logger

_log = get_logger("nvr.exposition")

_PROCESS_START = time.time()
DEFAULT_MAX_SKEW_SECONDS = 300


def _escape_label(value: str) -> str:
    """@brief Prometheus 标签值转义"""
    return str(value).replace("\\", "\\\\").replace('"', '\\"') \
        .replace("\n", "\\n")


def render_metrics(db, per_device: bool = True, per_channel: bool = True,
                   include_disabled: bool = False) -> str:
    """@brief 生成 0.0.4 文本(手写零依赖,契约)"""
    lines = []

    def gauge(name, help_text, samples):
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        for labels, value in samples:
            label_text = "" if not labels else "{" + ",".join(
                f'{key}="{_escape_label(val)}"'
                for key, val in labels.items()) + "}"
            lines.append(f"{name}{label_text} {value}")

    enabled_clause = "" if include_disabled else " WHERE d.enabled = 1"
    status_rows = db.query(
        "SELECT s.status, COUNT(*) FROM nvr_device_state s"
        f" JOIN nvr_devices d ON d.id = s.device_id{enabled_clause}"
        " GROUP BY s.status")
    gauge("nvrm_devices_total", "设备数(按状态)",
          [({"status": row[0]}, row[1]) for row in status_rows])
    alert_rows = db.query(
        "SELECT scope, COUNT(*) FROM nvr_alerts WHERE state = 'firing'"
        " GROUP BY scope")
    gauge("nvrm_alerts_active", "活动告警总数",
          [({}, sum(row[1] for row in alert_rows))])
    gauge("nvrm_alerts_active_by_scope", "活动告警(按范围)",
          [({"scope": row[0]}, row[1]) for row in alert_rows])
    if per_device:
        device_rows = db.query(
            "SELECT d.name, d.region, d.station, s.status,"
            " s.consecutive_fails FROM nvr_device_state s"
            f" JOIN nvr_devices d ON d.id = s.device_id{enabled_clause}")
        gauge("nvrm_device_up", "设备在线(1/0)",
              [({"device": row[0], "region": row[1], "station": row[2]},
                1 if row[3] == "online" else 0) for row in device_rows])
        gauge("nvrm_device_consecutive_fails", "设备连续失败次数",
              [({"device": row[0]}, row[4]) for row in device_rows])
    if per_channel:
        channel_rows = db.query(
            "SELECT status, COUNT(*) FROM nvr_channels WHERE removed = 0"
            " GROUP BY status")
        gauge("nvrm_channels_by_status", "通道数(按状态)",
              [({"status": row[0]}, row[1]) for row in channel_rows])
    gauge("process_start_time_seconds", "进程启动时间戳",
          [({}, _PROCESS_START)])
    return "\n".join(lines) + "\n"


def metrics_token_ok(provided: str, expected: str) -> bool:
    """@brief Bearer 常数时间比较(expected 空=不启用 Bearer)"""
    if not expected:
        return True
    return hmac.compare_digest(provided or "", expected)


# ---- 对外只读 HMAC ------------------------------------------------------
def canonical_string(method: str, path: str, query_params: dict,
                     timestamp: str, body: bytes) -> str:
    """@brief 待签串五行(契约):METHOD\\nPATH\\n排序k=v&…\\nTS\\nsha256(body)"""
    sorted_query = "&".join(f"{key}={query_params[key]}"
                            for key in sorted(query_params))
    body_hash = hashlib.sha256(body or b"").hexdigest()
    return f"{method}\n{path}\n{sorted_query}\n{timestamp}\n{body_hash}"


def sign_public_request(secret: str, method: str, path: str,
                        query_params: dict, timestamp: str,
                        body: bytes = b"") -> str:
    """@brief 客户端签名(README 对端示例同源)"""
    payload = canonical_string(method, path, query_params, timestamp, body)
    return hmac.new(secret.encode(), payload.encode(),
                    hashlib.sha256).hexdigest()


class PublicApiGuard:
    """对外 API 鉴权(失败一律 401 不区分原因)。"""

    def __init__(self, db, ring, suite, audit=None,
                 max_skew_seconds: int = DEFAULT_MAX_SKEW_SECONDS):
        """@brief 注入密钥存取(secret 密文落库)"""
        self._db = db
        self._ring = ring
        self._suite = suite
        self._audit = audit
        self._max_skew = max_skew_seconds

    def create_key(self, key_id: str) -> str:
        """@brief 建 API Key(明文仅返回一次,密文 AES-GCM 落库)"""
        import secrets as pysecrets
        from datetime import datetime, timezone
        from gd_crypto import encrypt_envelope, envelope_to_json
        secret = pysecrets.token_hex(24)
        envelope = encrypt_envelope(secret.encode(), self._ring, self._suite,
                                    aad=b"nvr_api_key")
        self._db.execute(
            "INSERT INTO nvr_api_keys(key_id, secret_ct, created_at)"
            " VALUES(?, ?, ?)",
            (key_id, envelope_to_json(envelope),
             datetime.now(timezone.utc).isoformat()))
        return secret

    def revoke_key(self, key_id: str):
        """@brief 吊销"""
        from datetime import datetime, timezone
        self._db.execute(
            "UPDATE nvr_api_keys SET revoked_at = ? WHERE key_id = ?",
            (datetime.now(timezone.utc).isoformat(), key_id))

    def _secret_for(self, key_id: str) -> str:
        """@brief 取未吊销密钥明文(无=None)"""
        from gd_crypto import decrypt_envelope, envelope_from_json
        rows = self._db.query(
            "SELECT secret_ct FROM nvr_api_keys WHERE key_id = ?"
            " AND revoked_at IS NULL", (key_id,))
        if not rows:
            return None
        return decrypt_envelope(envelope_from_json(rows[0][0]), self._ring,
                                aad=b"nvr_api_key").decode()

    def verify(self, method: str, path: str, query_params: dict,
               headers: dict, body: bytes) -> bool:
        """@brief 验签(时间容差 300s;任何失败记 public_auth_failed)"""
        key_id = headers.get("x-api-key-id", "")
        timestamp = headers.get("x-api-timestamp", "")
        signature = headers.get("x-api-signature", "")
        ok = False
        try:
            skew = abs(time.time() - float(timestamp))
            secret = self._secret_for(key_id)
            if secret is not None and skew <= self._max_skew:
                expected = sign_public_request(secret, method, path,
                                               query_params, timestamp, body)
                ok = hmac.compare_digest(signature, expected)
        except (ValueError, TypeError):
            ok = False
        if not ok:
            _log.warning("public_auth_failed",
                         extra={"ctx": {"key_id": key_id[:8]}})
            if self._audit:
                self._audit.append(key_id or "-", "login_failed",
                                   {"system": "nvr",
                                    "reason": "public_auth_failed"},
                                   "0.0.0.0")
        return ok
