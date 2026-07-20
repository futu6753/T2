# -*- coding: utf-8 -*-
"""
@file    siyun.py
@brief   大疆司运(FlyCart 运载)南向客户端:TD-022 推送验签
         X-DJI-Signature = HmacSHA256(AK+ts+nonce+event_type+sub_type, SK);
         请求侧鉴权头三兜底(AK_HEADER / AUTH_HEADER / AUTH_VALUE,
         TODO(GAP-22),403/200003 报错自带提示);物模型/任务轮询
         (lookback/page_size,code!=0 或 status=6 判 warn);
         create_task/start_task/edit_task_status/raw_cmd 命令与
         _cmd_terminal(bid) 终态查询。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import hashlib
import hmac

from apps.adapter.core.errors import (SignatureError, UpstreamError,
                                      UpstreamRejected)
from apps.adapter.core.vendors.transport import gated_base_url, json_body

WARN_TASK_STATUS = 6


def td022_signature(ak: str, sk: str, ts: str, nonce: str,
                    event_type: str, sub_type: str) -> str:
    """@brief TD-022 签名串:HmacSHA256(AK+ts+nonce+event_type+sub_type, SK)"""
    message = f"{ak}{ts}{nonce}{event_type}{sub_type}".encode("utf-8")
    return hmac.new(sk.encode("utf-8"), message, hashlib.sha256).hexdigest()


class SiyunClient:
    """司运南向客户端(传输可注入)。"""

    def __init__(self, settings, transport):
        """@brief 绑定配置与传输"""
        self.settings = settings
        self.transport = transport

    @property
    def configured(self) -> bool:
        """@brief 自动门控"""
        return not gated_base_url(self.settings.siyun_base_url)

    def _headers(self) -> dict:
        """@brief 鉴权头三兜底(GAP-22:目标环境按真实形态取舍)"""
        headers = {"Content-Type": "application/json"}
        if self.settings.siyun_ak_header:
            headers[self.settings.siyun_ak_header] = self.settings.siyun_ak
        if self.settings.siyun_auth_value:
            headers[self.settings.siyun_auth_header] = \
                self.settings.siyun_auth_value
        return headers

    def _post(self, path: str, payload: dict) -> dict:
        """@brief 司运统一 POST(403/200003 自带排障提示)"""
        resp = self.transport("POST", self.settings.siyun_base_url + path,
                              headers=self._headers(),
                              body=json_body(payload))
        doc = resp.json()
        code = doc.get("code", 0)
        if resp.status == 403 or code in (200003, "200003"):
            raise UpstreamError(
                f"司运鉴权失败(HTTP {resp.status} code={code}):请核对 "
                f"SIYUN_AK_HEADER / SIYUN_AUTH_HEADER / SIYUN_AUTH_VALUE "
                f"三项配置形态(GAP-22)")
        if resp.status >= 500:
            raise UpstreamError(f"司运接口 {path} 异常:HTTP {resp.status}")
        return doc

    def verify_webhook(self, headers: dict, event_type: str,
                       sub_type: str) -> bool:
        """@brief TD-022 入站验签(签名不符 → SignatureError,401)"""
        given = headers.get("x-dji-signature", "")
        expected = td022_signature(
            self.settings.siyun_ak, self.settings.siyun_sk,
            headers.get("x-dji-timestamp", ""),
            headers.get("x-dji-nonce", ""), event_type, sub_type)
        if not hmac.compare_digest(expected, given):
            raise SignatureError("司运推送验签失败(TD-022)")
        return True

    def fetch_properties(self) -> list:
        """@brief 物模型轮询"""
        doc = self._post("/openapi/v1/things/properties",
                         {"groupId": self.settings.siyun_group_id})
        return doc.get("data", [])

    def fetch_tasks(self) -> list:
        """@brief 任务轮询(lookback/page_size;code!=0 或 status=6 判 warn)"""
        doc = self._post("/openapi/v1/tasks/list", {
            "groupId": self.settings.siyun_group_id,
            "lookbackSeconds": self.settings.siyun_tasks_lookback_s,
            "pageSize": self.settings.siyun_tasks_page_size})
        rows = doc.get("data", [])
        for row in rows:
            if row.get("code", 0) not in (0, "0", None) \
                    or row.get("status") == WARN_TASK_STATUS:
                row["severity_hint"] = "warn"
        return rows

    def send_command(self, payload: dict) -> dict:
        """@brief 命令下行(ack 含 bid;明确拒绝→409)"""
        doc = self._post("/openapi/v1/commands", payload)
        if doc.get("code", 0) in (0, "0"):
            return doc
        raise UpstreamRejected(
            f"司运拒绝命令:code={doc.get('code')} {doc.get('message', '')}",
            data={"vendor_payload": doc})

    def query(self, name: str, handle: str) -> dict:
        """@brief 命名查询(DSL reply.terminal.query 落点)"""
        if name == "cmd_status":
            return self._cmd_terminal(handle)
        raise UpstreamError(f"司运不支持命名查询:{name}")

    def _cmd_terminal(self, bid: str) -> dict:
        """@brief 命令终态查询(按 bid)"""
        return self._post("/openapi/v1/commands/status", {"bid": bid})
