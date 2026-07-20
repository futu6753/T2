# -*- coding: utf-8 -*-
"""
@file    live.py
@brief   浏览器 E2E 基座:uvicorn 线程托管真实 TCP 服务(端口先取定、再注入
         issuer 构建 IdP)、SsoClient 真 HTTP 传输层、IdP+RP 双服务装配。
         无 Playwright 的离线目标环境整组自动跳过(GAP-15,离线打包指引见台账)。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import socket
import threading
import time
import urllib.error
import urllib.request

import uvicorn

from tests.idp_env import IdpEnv

from gd_sso_client.client import SsoClient, load_config
from gd_storage import LocalVolatileStore

STARTUP_TIMEOUT_SECONDS = 10


def pick_port() -> int:
    """@brief 先绑定 0 端口取得空闲端口号(供 issuer 先于服务启动而定)"""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


class LiveServer:
    """真实 uvicorn 服务(线程托管):真 TCP / 真重定向 / 真 Cookie。"""

    def __init__(self, app, port: int = None):
        """@brief 启动服务并阻塞等待就绪(fail-fast:超时抛错)"""
        self.port = port if port is not None else pick_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port,
                                log_level="critical", access_log=False)
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError(f"uvicorn 启动超时(port={self.port})")
            time.sleep(0.02)

    def stop(self):
        """@brief 优雅停机并回收线程"""
        self._server.should_exit = True
        self._thread.join(timeout=STARTUP_TIMEOUT_SECONDS)


class HttpTransport:
    """SsoClient 传输层:对 live IdP 发真实 HTTP(发现/JWKS/令牌交换)。"""

    def __call__(self, method, url, headers, body):
        """@brief 执行请求 @return (status, headers, body)"""
        request = urllib.request.Request(url, data=body, method=method,
                                         headers=dict(headers or {}))
        try:
            with urllib.request.urlopen(request, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:      # 非 2xx 也回传状态与体
            return exc.code, dict(exc.headers), exc.read()


class LiveIdpEnv(IdpEnv):
    """issuer 指向真实端口的 IdP 测试环境(浏览器可直接访问)。"""

    def __init__(self, issuer: str, is_demo: bool = False,
                 extra_environ: dict = None):
        """@brief 先记 issuer 再走父类构建(restart 会用到)"""
        self.issuer = issuer
        super().__init__(is_demo=is_demo, extra_environ=extra_environ)

    def _environ(self) -> dict:
        """@brief 在父类环境上注入 IDP_ISSUER(H08:发现文档指向真实地址)"""
        environ = super()._environ()
        environ["IDP_ISSUER"] = self.issuer
        return environ


class LiveStack:
    """IdP + 一个 RP 的双真实服务装配(供浏览器全链路用例)。"""

    def __init__(self, rp_factory, rp_system: str, rp_client_id: str,
                 extra_env: dict = None, idp_extra_environ: dict = None):
        """
        @brief  组建双服务:先取两端口→构建 IdP(issuer=真实地址)→注册 RP→
                以真 HTTP 传输层装配 SsoClient→拉起两台 uvicorn
        @param  rp_factory        callable(db, suite, sso) → FastAPI 应用
        @param  idp_extra_environ IdP 追加环境(如 CRYPTO_SUITE=gm,里程碑 10)
        """
        idp_port, rp_port = pick_port(), pick_port()
        self.idp_env = LiveIdpEnv(f"http://127.0.0.1:{idp_port}",
                                  extra_environ=idp_extra_environ)
        self.idp_env.seed_admin_and_user()
        self.rp_base = f"http://127.0.0.1:{rp_port}"
        redirect = f"{self.rp_base}/sso/callback"
        secret = self.idp_env.ctx.oidc.create_client(
            rp_client_id, f"{rp_system} 系统", [redirect],
            backchannel_url=f"{self.rp_base}/backchannel-logout")
        environ = {"SSO_ISSUER": self.idp_env.ctx.issuer,
                   "SSO_CLIENT_ID": rp_client_id, "SSO_CLIENT_SECRET": secret,
                   "SSO_REDIRECT": redirect, "SSO_COOKIE_SECURE": "0"}
        environ.update(extra_env or {})
        self.sso = SsoClient(load_config(environ), LocalVolatileStore(),
                             HttpTransport(), system=rp_system)
        ctx = self.idp_env.ctx
        self.rp_app = rp_factory(ctx.db, ctx.suite, self.sso)
        self.idp_server = LiveServer(self.idp_env.app, port=idp_port)
        self.rp_server = LiveServer(self.rp_app, port=rp_port)
        self.idp_base = self.idp_server.base_url

    def close(self):
        """@brief 停两台服务并回收 IdP 环境"""
        self.rp_server.stop()
        self.idp_server.stop()
        self.idp_env.close()
