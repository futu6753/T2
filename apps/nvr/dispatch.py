# -*- coding: utf-8 -*-
"""
@file    dispatch.py
@brief   通知派发(L04 §7):每渠道每条留痕 pending→sent/failed→abandoned,
         线性退避 backoff×次数、重试队列持久化重启续跑;
         Webhook 签名 `X-NVRM-Signature: sha256=HMAC(secret, "{ts}."+body)`
         + X-NVRM-Timestamp;阿里云短信按官方 RPC HMAC-SHA1 直连 Dysmsapi
         零 SDK(签名逐字单测锁定),模板两变量值超 20 字自动截断;
         传输层可注入(离线测试注入 fake HTTP)。通知失败仅记日志。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import base64
import hashlib
import hmac
import json
import secrets
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from gd_common.jsonlog import get_logger

_log = get_logger("nvr.dispatch")

SMS_VAR_MAX_CHARS = 20               # 阿里云模板变量截断阈(契约)


def _now() -> datetime:
    """@brief UTC 当前时间"""
    return datetime.now(timezone.utc)


def sign_webhook(secret: str, timestamp: str, body: str) -> str:
    """@brief Webhook 签名契约:sha256=HMAC(secret, "{ts}."+body)"""
    digest = hmac.new(secret.encode(), f"{timestamp}.{body}".encode(),
                      hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def aliyun_sms_signature(access_secret: str, http_method: str,
                         params: dict) -> str:
    """
    @brief  阿里云 RPC 签名(官方 HMAC-SHA1,零 SDK;逐字单测锁定):
            规范化查询串 → 待签串 METHOD&%2F&percentEncode(query) →
            HMAC-SHA1(secret+"&") → Base64
    """
    def encode(value: str) -> str:
        return urllib.parse.quote(str(value), safe="~") \
            .replace("+", "%20").replace("*", "%2A").replace("%7E", "~")

    canonical = "&".join(f"{encode(key)}={encode(params[key])}"
                         for key in sorted(params))
    to_sign = f"{http_method}&%2F&{encode(canonical)}"
    digest = hmac.new((access_secret + "&").encode(), to_sign.encode(),
                      hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def build_sms_params(access_key_id: str, region_id: str, sign_name: str,
                     template_code: str, phone: str, device_name: str,
                     status_text: str, nonce: str = None,
                     timestamp: str = None) -> dict:
    """@brief Dysmsapi SendSms 公共参数+业务参数(变量超 20 字截断)"""
    variables = {"device": device_name[:SMS_VAR_MAX_CHARS],
                 "status": status_text[:SMS_VAR_MAX_CHARS]}
    return {
        "AccessKeyId": access_key_id, "Action": "SendSms",
        "Format": "JSON", "PhoneNumbers": phone, "RegionId": region_id,
        "SignName": sign_name, "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": nonce or secrets.token_hex(16),
        "SignatureVersion": "1.0",
        "TemplateCode": template_code,
        "TemplateParam": json.dumps(variables, ensure_ascii=False),
        "Timestamp": timestamp or _now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Version": "2017-05-25",
    }


class WebhookChannel:
    """Webhook 渠道(HMAC 签名头)。"""

    channel_id = "webhook"

    def ready(self) -> bool:
        """@brief 就绪度:地址与密钥均已配置"""
        return bool(self._url and self._secret)

    def describe(self) -> str:
        """@brief 就绪说明(不回显密钥)"""
        if not self._url:
            return "未配置接收地址"
        return "已配置" if self._secret else "缺少签名密钥(NVR_WEBHOOK_SECRET)"

    def __init__(self, url: str, secret: str, timeout_seconds: float = 5.0,
                 transport=None):
        """@brief transport(url, headers, body)→(status, resp_body) 可注入"""
        self._url = url
        self._secret = secret
        self._timeout = timeout_seconds
        self._transport = transport or self._default_transport

    def _default_transport(self, url, headers, body):
        """@brief 标准库 HTTP POST"""
        request = urllib.request.Request(url, data=body.encode(),
                                         headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=self._timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")

    def send(self, payload: dict):
        """@brief 投递(非 2xx 抛错交重试)"""
        body = json.dumps(payload, ensure_ascii=False)
        timestamp = str(int(_now().timestamp()))
        headers = {"Content-Type": "application/json",
                   "X-NVRM-Timestamp": timestamp,
                   "X-NVRM-Signature": sign_webhook(self._secret, timestamp,
                                                    body)}
        status, _ = self._transport(self._url, headers, body)
        if not 200 <= status < 300:
            raise RuntimeError(f"Webhook 应答 {status}")


class AliyunSmsChannel:
    """阿里云短信渠道(RPC 直连零 SDK)。"""

    channel_id = "aliyun_sms"
    ENDPOINT = "https://dysmsapi.aliyuncs.com/"

    def ready(self) -> bool:
        """@brief 就绪度:AK/签名/模板/手机号均已配置"""
        return bool(self._ak and self._secret and self._sign_name
                    and self._template and self._phones)

    def describe(self) -> str:
        """@brief 就绪说明(不回显密钥)"""
        missing = [name for name, ok in (
            ("AccessKey", self._ak and self._secret),
            ("签名", self._sign_name), ("模板", self._template),
            ("手机号", self._phones)) if not ok]
        return "已配置" if not missing else f"缺少: {'、'.join(missing)}"

    def __init__(self, access_key_id: str, access_secret: str,
                 region_id: str, sign_name: str, template_code: str,
                 phone_numbers: list, transport=None):
        """@brief transport(url)→(status, body) 可注入"""
        self._ak = access_key_id
        self._secret = access_secret
        self._region = region_id
        self._sign_name = sign_name
        self._template = template_code
        self._phones = phone_numbers
        self._transport = transport or self._default_transport

    def _default_transport(self, url):
        """@brief 标准库 GET(RPC 签名在查询串)"""
        with urllib.request.urlopen(url, timeout=8) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")

    def send(self, payload: dict):
        """@brief 逐手机号投递(任一失败抛错交重试)"""
        for phone in self._phones:
            params = build_sms_params(
                self._ak, self._region, self._sign_name, self._template,
                phone, payload.get("device_name", ""),
                payload.get("status_text", ""))
            params["Signature"] = aliyun_sms_signature(self._secret, "GET",
                                                       params)
            query = urllib.parse.urlencode(params)
            status, body = self._transport(f"{self.ENDPOINT}?{query}")
            if status != 200 or '"Code":"OK"' not in body.replace(" ", ""):
                raise RuntimeError(f"短信应答异常: HTTP {status}")


class Dispatcher:
    """派发器:落队列 → 尝试 → 线性退避重试(持久化续跑)。"""

    def __init__(self, db, channels: list, max_attempts: int = 3,
                 backoff_seconds: int = 60):
        """@brief max_attempts 含首发(L04 alerting.retry)"""
        self._db = db
        self._channels = {channel.channel_id: channel for channel in channels}
        self._max_attempts = max(int(max_attempts), 1)
        self._backoff = max(int(backoff_seconds), 1)

    def channels(self) -> dict:
        """@brief 渠道注册表(就绪度端点用)"""
        return dict(self._channels)

    def enqueue(self, alert: dict, device: dict, kind: str):
        """@brief 告警事件入队各渠道并立即尝试一次"""
        payload = self._build_payload(alert, device, kind)
        for channel_id in self._channels:
            self._db.execute(
                "INSERT INTO nvr_notifications(alert_id, channel, state,"
                " attempts, payload, created_at, updated_at)"
                " VALUES(?, ?, 'pending', 0, ?, ?, ?)",
                (alert["id"], channel_id,
                 json.dumps(payload, ensure_ascii=False),
                 _now().isoformat(), _now().isoformat()))
        self.process_pending()

    def _build_payload(self, alert: dict, device: dict, kind: str) -> dict:
        """@brief 通知载荷(恢复通知带故障总时长)"""
        if kind == "resolved":
            status_text = "已恢复"
            title = (f"[恢复] {device['name']} 已恢复,故障总时长"
                     f" {alert.get('duration_seconds', 0)} 秒")
        else:
            status_text = alert["trigger_status"]
            title = (f"[告警] {device['name']}({device.get('station', '')})"
                     f" {alert['trigger_status']}:{alert['detail']}")
        return {"kind": kind, "alert_id": alert["id"],
                "device_name": device["name"],
                "region": device.get("region", ""),
                "station": device.get("station", ""),
                "scope": alert["scope"], "status_text": status_text,
                "title": title,
                "duration_seconds": alert.get("duration_seconds")}

    def process_pending(self, now: datetime = None) -> int:
        """
        @brief  处理到期 pending/failed(重启后从库续跑)@return 处理条数
        """
        current = now or _now()
        rows = self._db.query(
            "SELECT id, alert_id, channel, attempts, payload"
            " FROM nvr_notifications WHERE state IN ('pending','failed')"
            " AND (next_attempt_at IS NULL OR next_attempt_at <= ?)",
            (current.isoformat(),))
        handled = 0
        for note_id, alert_id, channel_id, attempts, payload_raw in rows:
            channel = self._channels.get(channel_id)
            if channel is None:
                continue
            attempts += 1
            try:
                channel.send(json.loads(payload_raw))
                self._db.execute(
                    "UPDATE nvr_notifications SET state = 'sent',"
                    " attempts = ?, updated_at = ? WHERE id = ?",
                    (attempts, _now().isoformat(), note_id))
            except Exception as exc:         # 通知失败仅记日志(契约)
                state = "abandoned" if attempts >= self._max_attempts \
                    else "failed"
                next_at = None if state == "abandoned" else \
                    (current + timedelta(
                        seconds=self._backoff * attempts)).isoformat()
                self._db.execute(
                    "UPDATE nvr_notifications SET state = ?, attempts = ?,"
                    " next_attempt_at = ?, last_error = ?, updated_at = ?"
                    " WHERE id = ?",
                    (state, attempts, next_at, str(exc)[:200],
                     _now().isoformat(), note_id))
                _log.warning("通知投递失败", extra={"ctx": {
                    "channel": channel_id, "attempts": attempts,
                    "state": state}})
            handled += 1
        return handled

    def list_notifications(self, state: str = None,
                           alert_id: int = None) -> list:
        """@brief 通知留痕查询"""
        conditions, params = [], []
        if state:
            conditions.append("state = ?")
            params.append(state)
        if alert_id:
            conditions.append("alert_id = ?")
            params.append(alert_id)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._db.query(
            "SELECT id, alert_id, channel, state, attempts, next_attempt_at,"
            f" last_error, created_at FROM nvr_notifications{where}"
            " ORDER BY id DESC", tuple(params))
        return [dict(zip(("id", "alert_id", "channel", "state", "attempts",
                          "next_attempt_at", "last_error", "created_at"), row))
                for row in rows]
