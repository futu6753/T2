# -*- coding: utf-8 -*-
"""
@file    nvr_env.py
@brief   nvr 测试基座:可编程 fake ISAPI 探针(按 host 配置行为)、
         IdP+nvr 装配、SSO 登录辅助、HMAC 签名请求辅助。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import json
import time

from tests.idp_env import IdpEnv
from tests.rp_env import drive_sso_login, make_sso_client

from apps.nvr.checker import DeviceChecker, IsapiTimeout
from apps.nvr.exposition import sign_public_request
from apps.nvr.web import create_app as create_nvr
from selfcheck.asgi import AsgiClient

NVR_REDIRECT = "http://nvr.internal/sso/callback"


class FakeFleet:
    """可编程设备群:host → 行为("online"/"auth"/"timeout_tcp"/
    "offline"/"icmp_only"/("channels", [离线通道])/"raise")。"""

    def __init__(self):
        self.behavior = {}

    def set(self, host: str, behavior):
        """@brief 设定某 host 行为"""
        self.behavior[host] = behavior

    def checker_factory(self, options: dict = None):
        """@brief 提供给 PatrolService/NvrContext 的工厂"""
        options = options or {}

        def factory(device, password):
            behavior = self.behavior.get(device["host"], "online")

            def isapi(host, port, username, pwd, timeout):
                current = self.behavior.get(host, "online")
                if current == "online":
                    return 200, []
                if current == "auth":
                    return 401, []
                if isinstance(current, tuple) and current[0] == "channels":
                    return 200, list(current[1])
                if current == "raise":
                    raise RuntimeError("探针崩溃")
                raise IsapiTimeout()

            tcp_ok = behavior == "timeout_tcp"
            ping_ok = behavior == "icmp_only"
            checker = DeviceChecker(
                isapi,
                tcp_probe=lambda h, p, t: self.behavior.get(h) == "timeout_tcp",
                ping_probe=lambda h, t: self.behavior.get(h) == "icmp_only",
                channel_check=options.get("channel_check", True),
                channel_offline_abnormal=options.get(
                    "channel_offline_abnormal", False),
                timeout_seconds=1)
            return lambda: checker.check(device["host"], device["port"],
                                         device["username"], password)
        return factory


class NvrEnv:
    """nvr 端到端环境。"""

    def __init__(self, **options):
        """@brief 装配 IdP + nvr(fleet.checker_factory 自动注入)"""
        self.idp = IdpEnv(is_demo=False)
        self.idp.seed_admin_and_user()
        self.sso, self.rp_store, _ = make_sso_client(
            self.idp, "nvr", "nvr", NVR_REDIRECT)
        self.fleet = FakeFleet()
        options.setdefault("checker_factory",
                           self.fleet.checker_factory(options))
        options.setdefault("settings", self.idp.ctx.settings)
        ctx = self.idp.ctx
        self.app = create_nvr(ctx.db, ctx.suite, self.sso, ring=ctx.ring,
                              **options)
        self.ctx = self.app.state.ctx
        self.db = ctx.db

    def login(self, role: str = "admin") -> AsgiClient:
        """@brief SSO 登录并赋角色 @return 带会话客户端"""
        client = drive_sso_login(self.idp, AsgiClient(self.app))
        username = self.db.query(
            "SELECT username FROM nvr_users ORDER BY id DESC LIMIT 1")[0][0]
        self.ctx.accounts.set_role(username, role)
        return client

    def create_device(self, client: AsgiClient, name: str, host: str,
                      **extra) -> dict:
        """@brief HTTP 建设备"""
        payload = {"name": name, "host": host, "password": "devpwd", **extra}
        resp = client.request("POST", "/api/devices",
                              raw_body=json.dumps(payload).encode(),
                              content_type="application/json")
        assert resp.status_code == 200, resp.body
        return resp.json()

    def public_get(self, client: AsgiClient, path: str, secret: str,
                   key_id: str, params: dict = None):
        """@brief 带 HMAC 签名的对外请求(签名用明文,传输 percent-encode)"""
        import urllib.parse
        params = params or {}
        timestamp = str(time.time())
        signature = sign_public_request(secret, "GET", path, params,
                                        timestamp)
        query = "&".join(
            f"{key}={urllib.parse.quote(str(value))}"
            for key, value in params.items())
        target = f"{path}?{query}" if query else path
        return client.get(target, headers={
            "X-API-Key-Id": key_id, "X-API-Timestamp": timestamp,
            "X-API-Signature": signature})

    def close(self):
        """@brief 释放"""
        self.idp.close()
