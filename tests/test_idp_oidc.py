# -*- coding: utf-8 -*-
"""
@file    test_idp_oidc.py
@brief   OIDC 协议回归(H09 §二 A/C):发现文档与 JWKS、授权码+PKCE 全流程、
         授权码一次性防重放、PKCE 篡改拒绝、应用访问控制 access_denied、
         IdP 重启 kid 不变且进行中登录可继续(C08 等价)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import base64
import hashlib
import secrets
import unittest
import urllib.parse

from tests.base import make_temp_db  # noqa: F401  (确保 sys.path 注入)
from tests.idp_env import IdpEnv, USER_ACCOUNT, USER_PASSWORD

from gd_sso_client.jwt_verify import verify_jwt

RP_REDIRECT = "http://rp.internal/sso/callback"


def _pkce() -> tuple:
    """@brief 生成 PKCE (verifier, challenge)"""
    verifier = secrets.token_urlsafe(40)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class TestOidcFlow(unittest.TestCase):
    """授权码 + PKCE 端到端(进程内 ASGI)。"""

    def setUp(self):
        self.env = IdpEnv(is_demo=False)
        self.env.seed_admin_and_user()
        self.client_secret = self.env.ctx.oidc.create_client(
            "certvault", "证件溯源", [RP_REDIRECT])

    def tearDown(self):
        self.env.close()

    def _authorize_url(self, challenge: str, state: str = "st1",
                       nonce: str = "n1", client_id: str = "certvault") -> str:
        """@brief 组装 /authorize 请求 URL"""
        params = urllib.parse.urlencode({
            "client_id": client_id, "redirect_uri": RP_REDIRECT,
            "state": state, "nonce": nonce, "code_challenge": challenge,
            "code_challenge_method": "S256"})
        return f"/authorize?{params}"

    def _login_and_get_code(self, client, verifier_challenge: tuple) -> str:
        """@brief 登录 → authorize → 取回授权码"""
        _, challenge = verifier_challenge
        self.env.login(client, USER_ACCOUNT, USER_PASSWORD)
        resp = client.get(self._authorize_url(challenge))
        self.assertEqual(resp.status_code, 302)
        location = resp.headers["location"]
        self.assertTrue(location.startswith(RP_REDIRECT))
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)
        self.assertEqual(query["state"], ["st1"])
        return query["code"][0]

    def test_a1_oidc_discovery_and_jwks(self):
        """发现文档五端点齐全;JWKS 含 RS256 公钥(02-A1)"""
        client = self.env.client()
        doc = client.get("/.well-known/openid-configuration").json()
        for field in ("authorization_endpoint", "token_endpoint",
                      "userinfo_endpoint", "jwks_uri", "end_session_endpoint"):
            self.assertIn(field, doc)
        self.assertEqual(doc["code_challenge_methods_supported"], ["S256"])
        jwks = client.get("/jwks.json").json()
        self.assertEqual(jwks["keys"][0]["alg"], "RS256")
        self.assertEqual(jwks["keys"][0]["kid"], self.env.ctx.keys.kid)

    def test_c1_full_auth_code_pkce_flow(self):
        """口令登录→授权码→换 id_token(RS256 验签、groups/amr/nonce 齐备)"""
        client = self.env.client()
        pair = _pkce()
        code = self._login_and_get_code(client, pair)
        token_resp = client.post("/token", data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": RP_REDIRECT, "client_id": "certvault",
            "client_secret": self.client_secret, "code_verifier": pair[0]})
        self.assertEqual(token_resp.status_code, 200)
        token_set = token_resp.json()
        claims = verify_jwt(token_set["id_token"],
                            self.env.ctx.keys.jwks(),
                            issuer=self.env.ctx.issuer, audience="certvault",
                            nonce="n1")
        self.assertEqual(claims["sub"], USER_ACCOUNT)
        self.assertEqual(claims["preferred_username"], "张三")
        self.assertIn("pwd", claims["amr"])
        info = client.get("/userinfo", headers={
            "authorization": f"Bearer {token_set['access_token']}"}).json()
        self.assertEqual(info["sub"], USER_ACCOUNT)

    def test_auth_code_replay_rejected(self):
        """授权码严格一次性:二次兑换 invalid_grant(02-A1 防重放)"""
        client = self.env.client()
        pair = _pkce()
        code = self._login_and_get_code(client, pair)
        form = {"grant_type": "authorization_code", "code": code,
                "redirect_uri": RP_REDIRECT, "client_id": "certvault",
                "client_secret": self.client_secret, "code_verifier": pair[0]}
        self.assertEqual(client.post("/token", data=form).status_code, 200)
        replay = client.post("/token", data=form)
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(replay.json()["error"], "invalid_grant")

    def test_pkce_verifier_mismatch_rejected(self):
        """PKCE verifier 不匹配拒绝(防授权码窃取)"""
        client = self.env.client()
        code = self._login_and_get_code(client, _pkce())
        resp = client.post("/token", data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": RP_REDIRECT, "client_id": "certvault",
            "client_secret": self.client_secret,
            "code_verifier": "wrong-verifier-value"})
        self.assertEqual(resp.json()["error"], "invalid_grant")

    def test_a6_access_policy_denied(self):
        """限组应用对非组员在 authorize 处标准 access_denied(02-A6)"""
        self.env.ctx.oidc.create_client("nvr", "NVR 监控", [RP_REDIRECT],
                                        access_policy="groups",
                                        access_groups=["ops"])
        client = self.env.client()
        self.env.login(client, USER_ACCOUNT, USER_PASSWORD)
        _, challenge = _pkce()
        resp = client.get(self._authorize_url(challenge, client_id="nvr"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("error=access_denied", resp.headers["location"])

    def test_c4_idp_restart_kid_stable_and_pending_login_survives(self):
        """C08 等价:重启后 kid 不变;重启前签发的登录上下文 rid 仍可完成登录"""
        client = self.env.client()
        _, challenge = _pkce()
        resp = client.get(self._authorize_url(challenge))     # 未登录 → 302 /login?rid=
        self.assertEqual(resp.status_code, 302)
        rid = urllib.parse.parse_qs(
            urllib.parse.urlsplit(resp.headers["location"]).query)["rid"][0]
        kid_before = self.env.ctx.keys.kid
        self.env.restart()                                    # 模拟进程重启
        self.assertEqual(self.env.ctx.keys.kid, kid_before)
        client_after = self.env.client()
        login_resp = client_after.post("/login", data={
            "account": USER_ACCOUNT, "password": USER_PASSWORD, "rid": rid})
        self.assertEqual(login_resp.status_code, 302)
        self.assertIn("/authorize?", login_resp.headers["location"])


if __name__ == "__main__":
    unittest.main()
