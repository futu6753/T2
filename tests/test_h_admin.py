# -*- coding: utf-8 -*-
"""
@file    test_h_admin.py
@brief   管理台回归(02-A4 / H03 §4):未登录 401、非管理员 403、CSRF 全覆盖、
         末位 admin 守护、停用即刻断线、解锁留审计、审计链一键校验。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import unittest

from tests.base import make_temp_db  # noqa: F401
from tests.idp_env import (
    ADMIN_ACCOUNT, ADMIN_PASSWORD, IdpEnv, USER_ACCOUNT, USER_PASSWORD,
)


class TestAdminZone(unittest.TestCase):
    """管理台五区安全语义。"""

    def setUp(self):
        self.env = IdpEnv(is_demo=False)
        self.env.seed_admin_and_user()

    def tearDown(self):
        self.env.close()

    def _admin_client(self):
        """@brief 登录管理员并取 CSRF 令牌 @return (client, csrf)"""
        client = self.env.client()
        self.env.login(client, ADMIN_ACCOUNT, ADMIN_PASSWORD)
        csrf = client.get("/admin/csrf").json()["csrf_token"]
        return client, csrf

    def test_h1_admin_requires_login_and_admin_role(self):
        """未登录 401;普通用户 403(最小权限,H04 §二.c)"""
        anonymous = self.env.client()
        self.assertEqual(anonymous.get("/admin/audit").status_code, 401)
        normal = self.env.client()
        self.env.login(normal, USER_ACCOUNT, USER_PASSWORD)
        self.assertEqual(normal.get("/admin/audit").status_code, 403)

    def test_h1_csrf_enforced_on_admin_posts(self):
        """管理操作缺失/错误 CSRF 令牌一律 403(H03 §3)"""
        client, _ = self._admin_client()
        resp = client.post("/admin/users/unlock",
                           data={"account": USER_ACCOUNT, "csrf_token": "bogus"})
        self.assertEqual(resp.status_code, 403)

    def test_h1_last_admin_guard(self):
        """末位 admin 守护:禁止停用最后一名管理员(H03 §4)"""
        client, csrf = self._admin_client()
        resp = client.post("/admin/users/disable",
                           data={"account": ADMIN_ACCOUNT, "csrf_token": csrf})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("末位", resp.json()["error"])

    def test_h1_disable_user_revokes_sessions_immediately(self):
        """停用账号即刻断线(逐请求回库校验语义,H04 §二.b)"""
        user_client = self.env.client()
        self.env.login(user_client, USER_ACCOUNT, USER_PASSWORD)
        self.assertEqual(user_client.get("/portal").status_code, 200)
        admin_client, csrf = self._admin_client()
        resp = admin_client.post("/admin/users/disable",
                                 data={"account": USER_ACCOUNT,
                                       "csrf_token": csrf})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(user_client.get("/portal").status_code, 302)

    def test_h1_unlock_writes_audit_and_chain_verifies(self):
        """解锁留审计;审计链一键校验通过(02-A4 审计区)"""
        admin_client, csrf = self._admin_client()
        resp = admin_client.post("/admin/users/unlock",
                                 data={"account": USER_ACCOUNT,
                                       "csrf_token": csrf})
        self.assertEqual(resp.status_code, 200)
        rows = self.env.ctx.db.query(
            "SELECT COUNT(*) FROM audit_logs WHERE action = 'user_unlocked'")
        self.assertGreaterEqual(rows[0][0], 1)
        verify = admin_client.get("/admin/audit/verify").json()
        self.assertEqual(verify["chain"], "OK")

    def test_h1_client_create_returns_secret_once(self):
        """注册应用返回一次性明文密钥,库中仅存哈希(02-A4 应用区)"""
        admin_client, csrf = self._admin_client()
        resp = admin_client.post("/admin/clients/create", data={
            "client_id": "quiz", "name": "安全刷题",
            "redirect_uri": "http://quiz.internal/sso/callback",
            "csrf_token": csrf})
        body = resp.json()
        self.assertIn("client_secret_once", body)
        stored = self.env.ctx.oidc.get_client("quiz")["secret_hash"]
        self.assertNotIn(body["client_secret_once"], stored)


if __name__ == "__main__":
    unittest.main()
