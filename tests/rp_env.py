# -*- coding: utf-8 -*-
"""
@file    rp_env.py
@brief   RP 生态测试基座:IdP + 任一 RP 应用进程内装配、浏览器模拟驱动
         完整授权码流(GET /sso/login → IdP 登录 → 回调 → RP 会话 Cookie)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import urllib.parse

from tests.idp_env import IdpEnv, USER_ACCOUNT, USER_PASSWORD

from gd_sso_client.client import SsoClient, load_config
from gd_storage import LocalVolatileStore
from selfcheck.asgi import AsgiClient


class RawBodyTransport:
    """SsoClient 传输层:直连 IdP 进程内应用(令牌交换 POST 透传表单)。"""

    def __init__(self, idp_client: AsgiClient):
        """@brief 绑定 IdP ASGI 客户端"""
        self._client = idp_client

    def __call__(self, method, url, headers, body):
        """@brief 执行请求并回 (status, headers, body)"""
        path = url[url.index("/", len("http://")):] if "://" in url else url
        if method == "GET":
            resp = self._client.get(path, headers=headers or {})
        else:
            data = urllib.parse.parse_qs((body or b"").decode())
            resp = self._client.post(path, data={k: v[0] for k, v in data.items()},
                                     headers=headers or {})
        return resp.status_code, resp.headers, resp.body


def make_sso_client(idp_env: IdpEnv, system: str, client_id: str,
                    redirect_uri: str, store=None,
                    extra_env: dict = None) -> tuple:
    """
    @brief  在 IdP 注册应用并装配对应 SsoClient
    @return (SsoClient, rp_store, client_secret)
    """
    secret = idp_env.ctx.oidc.create_client(
        client_id, f"{system} 系统", [redirect_uri],
        backchannel_url=f"http://{system}.internal/backchannel-logout")
    rp_store = store if store is not None else LocalVolatileStore()
    environ = {"SSO_ISSUER": idp_env.ctx.issuer, "SSO_CLIENT_ID": client_id,
               "SSO_CLIENT_SECRET": secret, "SSO_REDIRECT": redirect_uri,
               "SSO_COOKIE_SECURE": "0"}      # 测试无 TLS;生产默认 1(06-E13)
    environ.update(extra_env or {})
    sso = SsoClient(load_config(environ), rp_store,
                    RawBodyTransport(idp_env.client()), system=system)
    return sso, rp_store, secret


def drive_sso_login(idp_env: IdpEnv, rp_client: AsgiClient, next_path: str = "/",
                    idp_browser: AsgiClient = None,
                    account: str = USER_ACCOUNT,
                    password: str = USER_PASSWORD) -> AsgiClient:
    """
    @brief  浏览器模拟:RP /sso/login → IdP 登录 → 回调 → RP 会话 Cookie
    @param  idp_browser 复用的 IdP 浏览器(免登跳转场景传入已登录实例)
    @return 携带 RP 会话 Cookie 的 rp_client(原地更新 Cookie 罐)
    """
    browser = idp_browser if idp_browser is not None else idp_env.client()
    start = rp_client.get(f"/sso/login?next={urllib.parse.quote(next_path)}")
    assert start.status_code == 302, start.body
    authorize_path = start.headers["location"]
    authorize_path = authorize_path[authorize_path.index("/authorize"):]
    resp = browser.get(authorize_path)
    if "/login?rid=" in resp.headers.get("location", ""):
        rid = urllib.parse.parse_qs(urllib.parse.urlsplit(
            resp.headers["location"]).query)["rid"][0]
        login = idp_env.login(browser, account, password, extra={"rid": rid})
        assert login.status_code == 302, login.body
        follow = login.headers["location"]
        resp = browser.get(follow[follow.index("/authorize"):])
    callback_url = resp.headers["location"]
    callback_path = callback_url[callback_url.index("/sso/callback"):]
    final = rp_client.get(callback_path)
    assert final.status_code == 302, final.body
    return rp_client
