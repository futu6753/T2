# -*- coding: utf-8 -*-
"""
@file    zhiguang.py
@brief   织光(光伏清洁机器人)南向客户端:hmac_v1 签名(文档化假设,
         换算法只改 _digest 一处,TODO(GAP-21))、Webhook 验签三模式
         (strict→401 / log→只记 / off)、机器人/告警/清扫任务三路轮询、
         强制入库/出库/临时清扫命令。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import hashlib
import hmac
import json

from apps.adapter.core.errors import (SignatureError, UpstreamError,
                                      UpstreamRejected)
from apps.adapter.core.vendors.transport import (TransportTimeout,
                                                 gated_base_url, json_body)

VERIFY_STRICT = "strict"
VERIFY_LOG = "log"
VERIFY_OFF = "off"


class ZhiguangSigner:
    """织光签名器:验签覆盖原始 body 字节(L01 §4)。"""

    def __init__(self, app_secret: str, mode: str = "hmac_v1"):
        """@brief 记录密钥与算法模式"""
        self.app_secret = app_secret
        self.mode = mode

    def _digest(self, raw_body: bytes) -> str:
        """@brief 签名算法唯一落点(GAP-21:换厂商真实算法只改这里)"""
        return hmac.new(self.app_secret.encode("utf-8"), raw_body,
                        hashlib.sha256).hexdigest()

    def sign(self, raw_body: bytes) -> str:
        """@brief 出站签名(轮询请求体/模拟推送共用)"""
        if self.mode == "off":
            return ""
        return self._digest(raw_body)

    def verify(self, raw_body: bytes, signature: str) -> bool:
        """@brief 入站验签(常量时间比较)"""
        if self.mode == "off":
            return True
        expected = self._digest(raw_body)
        return hmac.compare_digest(expected, signature or "")


class ZhiguangClient:
    """织光南向客户端(传输可注入)。"""

    def __init__(self, settings, transport):
        """@brief 绑定配置与传输"""
        self.settings = settings
        self.transport = transport
        self.signer = ZhiguangSigner(settings.zg_app_secret,
                                     settings.zg_sign_mode)

    @property
    def configured(self) -> bool:
        """@brief 自动门控:BASE_URL 空/example.com 不发请求"""
        return not gated_base_url(self.settings.zg_base_url)

    def _headers(self, raw_body: bytes) -> dict:
        """@brief 轮询请求头(appKey + 签名)"""
        return {"Content-Type": "application/json",
                "X-ZG-App-Key": self.settings.zg_app_key,
                "X-ZG-Signature": self.signer.sign(raw_body)}

    def _post(self, path: str, payload: dict) -> dict:
        """@brief 织光统一 POST(code!=0 → UpstreamError)"""
        body = json_body(payload)
        resp = self.transport("POST", self.settings.zg_base_url + path,
                              headers=self._headers(body), body=body)
        doc = resp.json()
        if resp.status >= 500 or doc.get("code", 0) not in (0, "0"):
            raise UpstreamError(f"织光接口 {path} 异常:HTTP {resp.status} "
                                f"code={doc.get('code')}")
        return doc

    def fetch_robots(self) -> list:
        """@brief 轮询机器人 OSD 列表"""
        return self._post("/open/robot/list", {}).get("data", [])

    def fetch_alarms(self) -> list:
        """@brief 轮询告警列表"""
        return self._post("/open/alarm/list", {}).get("data", [])

    def fetch_tasks(self) -> list:
        """@brief 轮询清扫任务列表"""
        return self._post("/open/task/list", {}).get("data", [])

    def verify_webhook(self, raw_body: bytes, signature: str) -> bool:
        """
        @brief  入站 Webhook 验签(覆盖原始 body 字节)
        @return 验签结论;strict 失败抛 SignatureError(401)
        """
        valid = self.signer.verify(raw_body, signature)
        mode = self.settings.zg_verify_webhook
        if not valid and mode == VERIFY_STRICT:
            raise SignatureError("织光推送验签失败(strict 模式)")
        return valid if mode != VERIFY_OFF else True

    def send_command(self, payload: dict) -> dict:
        """@brief 命令下行(ack:code=0 受理;明确拒绝→409;超时上抛)"""
        body = json_body(payload)
        try:
            resp = self.transport("POST",
                                  self.settings.zg_base_url + "/open/command",
                                  headers=self._headers(body), body=body)
        except TransportTimeout:
            raise
        doc = resp.json()
        if doc.get("code", 0) in (0, "0"):
            return doc
        raise UpstreamRejected(
            f"织光拒绝命令:code={doc.get('code')} {doc.get('message', '')}",
            data={"vendor_payload": doc})

    @staticmethod
    def parse_webhook(raw_body: bytes) -> dict:
        """@brief 推送体解析:非法 JSON 或缺 id → ValueError(api 层转 400)"""
        try:
            doc = json.loads(raw_body)
        except ValueError as exc:
            raise ValueError(f"织光推送体非法 JSON:{exc}") from exc
        if not isinstance(doc, dict) or doc.get("id") in (None, ""):
            raise ValueError("织光推送体缺少 id")
        return doc
