# -*- coding: utf-8 -*-
"""
@file    test_g_multi_instance.py
@brief   H09 §二 G 组(06-E13):多实例语义进程内验证——共享库+共享易失态下,
         G.2 进行中登录上下文跨实例完成、会话跨实例存活(杀实例不丢);
         G.3 锁定计数跨实例累加、审计链双实例交替写入无分叉;
         G.4 策略热更新在全部实例 ≤ 下一请求生效;RP 双实例会话共享与
         back-channel 跨实例吊销。参考拓扑见 deploy/docker-compose.reference.yml。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import unittest
import urllib.parse

from tests.base import make_temp_db  # noqa: F401
from tests.idp_env import (
    IdpEnv, TEST_IP, USER_ACCOUNT, USER_PASSWORD,
)
from tests.rp_env import drive_sso_login, make_sso_client

from gd_storage.audit import verify_chain

from apps.idp.context import IdpContext
from apps.idp.tokens import mint_logout_token
from apps.idp.web import create_app
from apps.nvr.web import create_app as create_nvr
from selfcheck.asgi import AsgiClient

NVR_REDIRECT = "http://nvr.internal/sso/callback"


class MultiInstanceEnv:
    """双 IdP 实例:同库、同密钥目录、同易失态(compose 参考拓扑等价)。"""

    def __init__(self):
        """@brief 实例 1 由 IdpEnv 提供;实例 2 手工装配同源上下文"""
        self.env = IdpEnv(is_demo=False)
        self.env.seed_admin_and_user()
        self.ctx2 = IdpContext(f"sqlite:///{self.env.db_path}", self.env.key_dir,
                               store=self.env.store,
                               environ=self.env._environ())
        self.app2 = create_app(self.ctx2)

    def close(self):
        """@brief 释放两实例资源"""
        self.ctx2.close()
        self.env.close()


class TestIdpMultiInstance(unittest.TestCase):
    """G.2/G.3/G.4:IdP 双实例语义。"""

    def setUp(self):
        self.multi = MultiInstanceEnv()
        self.env = self.multi.env
        self.client_a = self.env.client()               # 打到实例 1
        self.client_b = AsgiClient(self.multi.app2)     # 打到实例 2

    def tearDown(self):
        self.multi.close()

    def test_g2_login_context_completes_on_other_instance(self):
        """进行中登录上下文(rid)在另一实例完成;会话跨实例存活(杀实例不丢)"""
        self.env.ctx.oidc.create_client("nvr", "NVR", [NVR_REDIRECT])
        authorize = ("/authorize?response_type=code&client_id=nvr"
                     f"&redirect_uri={urllib.parse.quote(NVR_REDIRECT)}"
                     "&scope=openid&state=s1&nonce=n1"
                     "&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
                     "&code_challenge_method=S256")
        started = self.client_a.get(authorize)          # 实例 1 签发 rid
        rid = urllib.parse.parse_qs(urllib.parse.urlsplit(
            started.headers["location"]).query)["rid"][0]
        self.client_b.cookies.update(self.client_a.cookies)
        done = self.env.login(self.client_b, USER_ACCOUNT, USER_PASSWORD,
                              extra={"rid": rid})       # 实例 2 完成登录
        self.assertEqual(done.status_code, 302)
        # 会话在共享易失态:实例 1"已被杀",Cookie 在实例 2 直接可用
        self.assertEqual(self.client_b.get("/portal").status_code, 200)
        # 同一 Cookie 回到实例 1 亦有效(无实例亲和)
        self.client_a.cookies.update(self.client_b.cookies)
        self.assertEqual(self.client_a.get("/portal").status_code, 200)

    def test_g3_lockout_counter_accumulates_across_instances(self):
        """锁定计数跨实例累加:A 错 3 次 + B 错 2 次 = 锁定(H03 §2)"""
        for _ in range(3):
            self.env.login(self.client_a, USER_ACCOUNT, "wrong")
        for _ in range(2):
            self.env.login(self.client_b, USER_ACCOUNT, "wrong")
        self.assertTrue(self.env.ctx.accounts.is_locked(USER_ACCOUNT))
        locked = self.env.login(self.client_a, USER_ACCOUNT, USER_PASSWORD)
        self.assertEqual(locked.status_code, 423)

    def test_g3_audit_chain_no_fork_with_interleaved_writers(self):
        """审计链双实例交替写入无分叉、一键校验通过(ARC-7 串行锁)"""
        for round_no in range(5):
            self.env.ctx.audit.append("inst-1", "settings_changed",
                                      {"round": round_no}, TEST_IP)
            self.multi.ctx2.audit.append("inst-2", "settings_changed",
                                         {"round": round_no}, TEST_IP)
        self.assertGreater(verify_chain(self.env.ctx.db), 0)
        rows = self.env.ctx.db.query(
            "SELECT COUNT(DISTINCT prev_hash) FROM audit_logs")
        total = self.env.ctx.db.query("SELECT COUNT(*) FROM audit_logs")
        self.assertEqual(rows[0][0], total[0][0])       # prev_hash 无重复=无分叉

    def test_g4_settings_hot_update_propagates_to_all_instances(self):
        """实例 1 改策略,实例 2 下一请求即生效(settings 版本轮询)"""
        self.assertEqual(self.client_b.post(
            "/login/sms/send", data={"account": USER_ACCOUNT}).status_code, 404)
        self.env.ctx.settings.set_override("method_sms", True, "op_admin", TEST_IP)
        self.env.ctx.refresh_profile()                  # 实例 1 主动刷新
        resp_b = self.client_b.post("/login/sms/send",
                                    data={"account": USER_ACCOUNT})
        self.assertNotEqual(resp_b.status_code, 404)    # 实例 2 已感知开关


class TestRpMultiInstance(unittest.TestCase):
    """G.1/G.2:RP 双实例(同一 SsoClient 配置 + 共享易失态)。"""

    def setUp(self):
        self.env = IdpEnv(is_demo=False)
        self.env.seed_admin_and_user()
        self.sso, self.rp_store, _ = make_sso_client(
            self.env, "nvr", "nvr", NVR_REDIRECT)
        ctx = self.env.ctx
        self.rp_app_a = create_nvr(ctx.db, ctx.suite, self.sso)
        self.rp_app_b = create_nvr(ctx.db, ctx.suite, self.sso)

    def tearDown(self):
        self.env.close()

    def test_g2_rp_session_shared_and_backchannel_cross_instance(self):
        """回调在实例 A 建会话 → 实例 B 直接可用;B 收扇出 → A 同刻失效"""
        client_a = drive_sso_login(self.env, AsgiClient(self.rp_app_a))
        client_b = AsgiClient(self.rp_app_b)
        client_b.cookies.update(client_a.cookies)
        self.assertEqual(client_b.get("/devices").status_code, 200)
        token = mint_logout_token(self.env.ctx.issuer, "nvr",
                                  USER_ACCOUNT, self.env.ctx.keys)
        fanout = client_b.post("/backchannel-logout",
                               data={"logout_token": token})
        self.assertEqual(fanout.status_code, 200)
        self.assertEqual(client_a.get("/devices").status_code, 401)


if __name__ == "__main__":
    unittest.main()
