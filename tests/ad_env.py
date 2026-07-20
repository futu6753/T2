# -*- coding: utf-8 -*-
"""
@file    ad_env.py
@brief   adapter 测试基座:假时钟(sleep 即拨表,reply 语义零真实等待)、
         脚本化厂商传输替身(H06-E17"错误映射每个分支要有 mock 级活体
         测试")、core 对象图与 API 应用装配。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import json
import logging

from tests.base import REPO_ROOT  # noqa: F401 路径注入副作用

from apps.adapter.api.main import create_app, load_specs
from apps.adapter.core.config import Settings
from apps.adapter.core.vendors.transport import HttpResponse, TransportTimeout
from selfcheck.asgi import AsgiClient


class FakeClock:
    """假时钟:sleep 直接拨表,礼貌轮询/退避零真实耗时。"""

    def __init__(self, start: float = 1000.0):
        """@brief 初始起点"""
        self.now = float(start)
        self.sleeps = []

    def __call__(self) -> float:
        """@brief monotonic 语义"""
        return self.now

    def sleep(self, seconds: float):
        """@brief 记录并推进"""
        self.sleeps.append(round(float(seconds), 3))
        self.now += float(seconds)


class ScriptedTransport:
    """按 URL 后缀脚本化应答的传输替身。

    handlers: {path 后缀: 可调用(method, url, headers, body) → 应答}
    应答可为 dict(→200 JSON)、(status, dict)、异常实例或可调用。
    """

    def __init__(self, handlers: dict = None):
        """@brief 初始化脚本表与调用留痕"""
        self.handlers = dict(handlers or {})
        self.calls = []

    def __call__(self, method, url, headers=None, body=None,
                 timeout_s=None) -> HttpResponse:
        """@brief 匹配脚本并应答(无匹配 → 200 {code:0})"""
        self.calls.append({"method": method, "url": url,
                           "headers": dict(headers or {}),
                           "body": body})
        for suffix, handler in self.handlers.items():
            if url.endswith(suffix):
                outcome = handler
                if callable(handler):
                    outcome = handler(method, url, headers, body)
                if isinstance(outcome, Exception):
                    raise outcome
                if isinstance(outcome, tuple):
                    status, doc = outcome
                else:
                    status, doc = 200, outcome
                return HttpResponse(status, {}, json.dumps(doc).encode())
        return HttpResponse(200, {}, b'{"code": 0}')


def timeout_transport(*_args, **_kwargs):
    """@brief 永远超时的传输(ack 超时→504 分支)"""
    raise TransportTimeout("mock ack timeout")


def make_settings(**overrides) -> Settings:
    """@brief 全厂商已接线的测试配置(dedupe/幂等参数可覆盖)"""
    base = {
        "zg_base_url": "http://zg.test", "zg_app_key": "zg-key",
        "zg_app_secret": "zg-secret",
        "skysys_auth_base": "http://sky-auth.test",
        "skysys_gw_b_base": "http://sky-b.test",
        "skysys_access_key": "sky-ak", "skysys_access_secret": "sky-sk",
        "siyun_base_url": "http://siyun.test", "siyun_group_id": "g1",
        "siyun_ak": "dji-ak", "siyun_sk": "dji-sk",
        "downstream_url": "", "forward_backoff_base_s": 1.0,
    }
    base.update(overrides)
    return Settings(**base)


class AdapterEnv:
    """API 级装配:假时钟 + 可注入传输 + AsgiClient。"""

    def __init__(self, transports: dict = None, settings: Settings = None):
        """@brief 装配应用"""
        self.clock = FakeClock()
        self.settings = settings or make_settings()
        self.transports = transports or {"default": ScriptedTransport()}
        logging.getLogger("adapter").setLevel(logging.ERROR)
        self.app = create_app(self.settings, transports=self.transports,
                              clock=self.clock, sleeper=self.clock.sleep)
        self.ctx = self.app.state.ctx

    def client(self) -> AsgiClient:
        """@brief 进程内客户端"""
        return AsgiClient(self.app)

    def post_json(self, client, path, payload, headers=None):
        """@brief JSON POST 便捷封装"""
        return client.request("POST", path,
                              raw_body=json.dumps(payload).encode(),
                              headers=headers,
                              content_type="application/json")


def specs():
    """@brief 契约映射声明(harness/mappings)"""
    return load_specs()


def skysys_command_transport(poll_statuses: list) -> ScriptedTransport:
    """@brief 星逻命令链传输:token/命令 ack(带批次)/终态按脚本推进"""
    state = {"polls": list(poll_statuses)}

    def batch_status(_method, _url, _headers, _body):
        """@brief 每次查询弹出下一个状态,末值粘住"""
        status = state["polls"].pop(0) if len(state["polls"]) > 1 \
            else state["polls"][0]
        return {"code": 0, "data": {"status": status}}

    return ScriptedTransport({
        "/auth/token": {"accessToken": "tok-1"},
        "/mission/command": {"code": 0,
                             "data": {"missionBatch": "MB-77"}},
        "/mission/batch/status": batch_status,
    })


def siyun_command_transport(poll_statuses: list) -> ScriptedTransport:
    """@brief 司运命令链传输:ack(带 bid)/终态按脚本推进"""
    state = {"polls": list(poll_statuses)}

    def cmd_status(_method, _url, _headers, _body):
        """@brief 终态脚本"""
        status = state["polls"].pop(0) if len(state["polls"]) > 1 \
            else state["polls"][0]
        return {"code": 0, "data": {"status": status}}

    return ScriptedTransport({
        "/openapi/v1/commands": {"code": 0, "data": {"bid": "BID-9",
                                                     "task_id": "T-1"}},
        "/openapi/v1/commands/status": cmd_status,
    })
