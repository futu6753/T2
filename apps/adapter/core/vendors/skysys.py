# -*- coding: utf-8 -*-
"""
@file    skysys.py
@brief   星逻驭光(无人机机库)南向客户端:token 获取(TTL 1800s,请求头名
         可配 TODO(GAP-23))、在飞批次轮询与批次终态查询(_batch_terminal
         对应 DSL reply.terminal 的 batch_status 命名查询)、takeoff/pause/
         resume/return_home 命令;missionBatch 候选解析,缺失显式报错。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import time

from apps.adapter.core.errors import UpstreamError, UpstreamRejected
from apps.adapter.core.vendors.transport import gated_base_url, json_body

BATCH_KEYS = ("missionBatch", "mission_batch", "batchId", "batch_id")


class SkysysClient:
    """星逻南向客户端(业务走内网网关B,实测;传输可注入)。"""

    def __init__(self, settings, transport, clock=time.monotonic):
        """@brief 绑定配置/传输/时钟(token 续期用)"""
        self.settings = settings
        self.transport = transport
        self._clock = clock
        self._token = ""
        self._token_expire = 0.0

    @property
    def configured(self) -> bool:
        """@brief 自动门控"""
        return not gated_base_url(self.settings.skysys_gw_b_base)

    def _ensure_token(self) -> str:
        """@brief token 获取与 TTL 续期(auth 域名与业务网关分离)"""
        if self._token and self._clock() < self._token_expire:
            return self._token
        body = json_body({"accessKey": self.settings.skysys_access_key,
                          "accessSecret": self.settings.skysys_access_secret,
                          "product": self.settings.skysys_product})
        resp = self.transport("POST",
                              self.settings.skysys_auth_base + "/auth/token",
                              headers={"Content-Type": "application/json"},
                              body=body)
        doc = resp.json()
        token = doc.get("accessToken") or doc.get("token") \
            or (doc.get("data") or {}).get("accessToken")
        if not token:
            raise UpstreamError(f"星逻 token 响应缺少令牌字段:{doc}")
        self._token = token
        self._token_expire = self._clock() + self.settings.skysys_token_ttl_s
        return token

    def _headers(self) -> dict:
        """@brief 业务请求头(token 头名可配,GAP-23)"""
        return {"Content-Type": "application/json",
                self.settings.skysys_token_header: self._ensure_token()}

    def _post(self, path: str, payload: dict) -> dict:
        """@brief 星逻统一 POST"""
        resp = self.transport("POST", self.settings.skysys_gw_b_base + path,
                              headers=self._headers(),
                              body=json_body(payload))
        doc = resp.json()
        if resp.status >= 500:
            raise UpstreamError(f"星逻接口 {path} 异常:HTTP {resp.status}")
        return doc

    @staticmethod
    def extract_batch(doc: dict) -> str:
        """@brief missionBatch 候选解析(GAP-23:缺失显式报错不猜)"""
        pool = dict(doc)
        pool.update(doc.get("data") or {})
        for key in BATCH_KEYS:
            if pool.get(key):
                return str(pool[key])
        raise UpstreamError(f"星逻响应缺少任务批次字段(候选 {BATCH_KEYS}):{doc}")

    def fetch_active_batches(self) -> list:
        """@brief 在飞批次轮询"""
        return self._post("/mission/batch/active", {}).get("data", [])

    def send_command(self, payload: dict) -> dict:
        """@brief 命令下行(明确拒绝→409;其余交 dispatch)"""
        doc = self._post("/mission/command", payload)
        if doc.get("code", 0) in (0, "0", 200):
            return doc
        raise UpstreamRejected(
            f"星逻拒绝命令:code={doc.get('code')} {doc.get('message', '')}",
            data={"vendor_payload": doc})

    def query(self, name: str, handle: str) -> dict:
        """@brief 命名查询(DSL reply.terminal.query 落点)"""
        if name == "batch_status":
            return self._batch_terminal(handle)
        raise UpstreamError(f"星逻不支持命名查询:{name}")

    def _batch_terminal(self, batch: str) -> dict:
        """@brief 批次终态查询(reply 礼貌轮询用)"""
        return self._post("/mission/batch/status", {"missionBatch": batch})
