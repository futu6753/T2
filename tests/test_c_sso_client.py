# -*- coding: utf-8 -*-
"""
@file    test_c_sso_client.py
@brief   H09 §二 C 组 / 06-E13 回归:RP 统一接入库端到端(传输层直连 IdP ASGI)。
         覆盖:完整授权码+PKCE 流、state 一次性、nonce 防重放、伪签名拒绝、
         back-channel 强制下线、开放重定向防护、未配 env 时 SSO 不启用。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import json
import unittest
import urllib.parse

from tests.base import make_temp_db  # noqa: F401
from tests.idp_env import IdpEnv, USER_ACCOUNT, USER_PASSWORD

from gd_common.errors import CryptoError, PolicyValidationError
from gd_storage import LocalVolatileStore
from gd_sso_client.client import SsoClient, load_config
from apps.idp.tokens import encode_jwt, mint_logout_token
from selfcheck.asgi import AsgiClient

RP_REDIRECT = "http://rp.internal/sso/callback"


class RawBodyTransport:
    """支持原始 body 的传输层(令牌交换 POST 用)。"""

    def __init__(self, idp_client: AsgiClient):
        """@brief 绑定 IdP 客户端"""
        self._client = idp_client

    def __call__(self, method, url, headers, body):
        """@brief 执行请求并透传原始 body"""
        path = url[url.index("/", len("http://")):] if "://" in url else url
        if method == "GET":
            resp = self._client.get(path, headers=headers or {})
        else:
            data = urllib.parse.parse_qs((body or b"").decode())
            resp = self._client.post(path, data={k: v[0] for k, v in data.items()},
                                     headers=headers or {})
        return resp.status_code, resp.headers, resp.body


class TestSsoClientFlow(unittest.TestCase):
    """RP 五路由语义端到端。"""

    def setUp(self):
        self.env = IdpEnv(is_demo=False)
        self.env.seed_admin_and_user()
        self.secret = self.env.ctx.oidc.create_client(
            "nvr", "NVR 监控", [RP_REDIRECT],
            backchannel_url="http://rp.internal/backchannel-logout")
        self.idp_browser = self.env.client()     # 模拟用户浏览器(带 IdP 会话)
        self.rp_store = LocalVolatileStore()
        self.sso = SsoClient(load_config({
            "SSO_ISSUER": self.env.ctx.issuer, "SSO_CLIENT_ID": "nvr",
            "SSO_CLIENT_SECRET": self.secret, "SSO_REDIRECT": RP_REDIRECT,
        }), self.rp_store, RawBodyTransport(self.env.client()))

    def tearDown(self):
        self.env.close()

    def _drive_authorize(self, redirect_url: str) -> dict:
        """@brief 用"浏览器"完成登录+authorize,返回回调 query 参数"""
        path = redirect_url[redirect_url.index("/authorize"):]
        resp = self.idp_browser.get(path)
        if "/login?rid=" in resp.headers.get("location", ""):
            rid = urllib.parse.parse_qs(urllib.parse.urlsplit(
                resp.headers["location"]).query)["rid"][0]
            login = self.env.login(self.idp_browser, USER_ACCOUNT, USER_PASSWORD,
                                   extra={"rid": rid})
            resp = self.idp_browser.get(
                login.headers["location"][login.headers["location"].index("/authorize"):])
        location = resp.headers["location"]
        assert location.startswith(RP_REDIRECT), location
        return {key: values[0] for key, values in urllib.parse.parse_qs(
            urllib.parse.urlsplit(location).query).items()}

    def test_c2_full_rp_flow_unified_login(self):
        """C.2:RP 统一登录端到端(state/nonce/PKCE→回调→本地会话)"""
        redirect_url = self.sso.build_login_redirect("/dashboard")
        callback_query = self._drive_authorize(redirect_url)
        result = self.sso.handle_callback(callback_query)
        self.assertEqual(result["claims"]["sub"], USER_ACCOUNT)
        self.assertEqual(result["next"], "/dashboard")
        session = self.sso.get_session(result["session_id"])
        self.assertEqual(session["sub"], USER_ACCOUNT)

    def test_c2_state_single_use_and_expiry_semantics(self):
        """state 严格一次性:同一回调重放被拒(H08 §3 安全性质)"""
        callback_query = self._drive_authorize(self.sso.build_login_redirect("/"))
        self.sso.handle_callback(dict(callback_query))
        with self.assertRaises(PolicyValidationError):
            self.sso.handle_callback(dict(callback_query))

    def test_c2_forged_id_token_rejected(self):
        """伪签名 id_token 拒绝(RP 验签按 JWKS kid)"""
        forged_env = IdpEnv(is_demo=False)      # 另一把私钥签发 = 伪造
        try:
            forged = encode_jwt({"iss": self.env.ctx.issuer, "aud": "nvr",
                                 "sub": "attacker", "exp": 9999999999,
                                 "nonce": "x"}, forged_env.ctx.keys)
            from gd_sso_client.jwt_verify import verify_jwt
            with self.assertRaises(CryptoError):
                verify_jwt(forged, self.env.ctx.keys.jwks(),
                           issuer=self.env.ctx.issuer, audience="nvr", nonce="x")
        finally:
            forged_env.close()

    def test_c2_nonce_mismatch_rejected(self):
        """nonce 不匹配拒绝(防授权码注入/重放)"""
        callback_query = self._drive_authorize(self.sso.build_login_redirect("/"))
        # 篡改 state 存根中的 nonce 期望值以模拟注入
        other = self.sso.build_login_redirect("/")
        other_state = urllib.parse.parse_qs(
            urllib.parse.urlsplit(other).query)["state"][0]
        with self.assertRaises(CryptoError):
            self.sso.handle_callback({"state": other_state,
                                      "code": callback_query["code"]})

    def test_c2_backchannel_logout_revokes_all_sessions(self):
        """back-channel:验 logout_token 后即刻吊销该用户全部本地会话"""
        result = self.sso.handle_callback(
            self._drive_authorize(self.sso.build_login_redirect("/")))
        self.assertIsNotNone(self.sso.get_session(result["session_id"]))
        logout_token = mint_logout_token(self.env.ctx.issuer, "nvr",
                                         USER_ACCOUNT, self.env.ctx.keys)
        sub = self.sso.handle_backchannel_logout(logout_token)
        self.assertEqual(sub, USER_ACCOUNT)
        self.assertIsNone(self.sso.get_session(result["session_id"]))

    def test_c2_next_only_relative_path(self):
        """回跳 next 只接受站内相对路径(开放重定向防护,06-E13)"""
        redirect_url = self.sso.build_login_redirect("https://evil.example/steal")
        state = urllib.parse.parse_qs(
            urllib.parse.urlsplit(redirect_url).query)["state"][0]
        record = json.loads(self.rp_store.get(f"gd:rp:state:{state}"))
        self.assertEqual(record["next"], "/")

    def test_c2_sso_disabled_without_required_env(self):
        """必填 env 缺任一:SSO 不启用、登录页据此隐藏按钮(H08 §3)"""
        config = load_config({"SSO_ISSUER": "http://idp", "SSO_CLIENT_ID": "x"})
        self.assertFalse(config.is_enabled)
        client = SsoClient(config, self.rp_store, RawBodyTransport(self.env.client()))
        self.assertEqual(client.status(), {"enabled": False})
        with self.assertRaises(PolicyValidationError):
            client.build_login_redirect("/")


if __name__ == "__main__":
    unittest.main()
