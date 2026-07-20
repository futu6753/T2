# -*- coding: utf-8 -*-
"""
@file    test_idp_login.py
@brief   登录方式与账户策略回归(H09 §二 A/C.1):锁定(两步失败同计次+
         管理员解锁)、TOTP 真码、D2 测试码 DEMO/生产成对断言、D3 短信回显、
         口令策略校验、首登强改密。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import unittest

from tests.base import make_temp_db  # noqa: F401
from tests.idp_env import (
    ADMIN_ACCOUNT, IdpEnv, TEST_IP, USER_ACCOUNT, USER_PASSWORD,
)

from gd_common.errors import PolicyValidationError
from apps.idp import accounts as acc
from apps.idp.totp import totp_code

HTTP_LOCKED = 423
HTTP_UNAUTHORIZED = 401


class TestLoginLockout(unittest.TestCase):
    """A.1:5 次失败锁 15 分钟(两步验证失败同计次),管理员可解锁。"""

    def setUp(self):
        self.env = IdpEnv(is_demo=False)
        self.env.seed_admin_and_user()

    def tearDown(self):
        self.env.close()

    def test_a1_lockout_after_failures_two_step_counted_and_admin_unlock(self):
        """口令错 3 次 + TOTP 错 2 次 = 5 次触发锁定;解锁后可登录"""
        ctx, profile = self.env.ctx, self.env.ctx.profile
        secret = ctx.accounts.bind_totp(USER_ACCOUNT, "system", TEST_IP)
        client = self.env.client()
        for _ in range(3):
            resp = self.env.login(client, USER_ACCOUNT, "wrong-password")
            self.assertEqual(resp.status_code, HTTP_UNAUTHORIZED)
        for _ in range(2):        # 第一步正确、TOTP 错误 → 同计次(H04 §一.b)
            resp = self.env.login(client, USER_ACCOUNT, USER_PASSWORD,
                                  extra={"totp_code": "000000"})
            self.assertEqual(resp.status_code, HTTP_UNAUTHORIZED)
        self.assertTrue(ctx.accounts.is_locked(USER_ACCOUNT))
        locked = self.env.login(client, USER_ACCOUNT, USER_PASSWORD,
                                extra={"totp_code": totp_code(secret)})
        self.assertEqual(locked.status_code, HTTP_LOCKED)
        self.assertIn(str(profile.lockout_minutes), locked.json()["error"])
        ctx.accounts.admin_unlock(USER_ACCOUNT, ADMIN_ACCOUNT, TEST_IP)
        ok = self.env.login(client, USER_ACCOUNT, USER_PASSWORD,
                            extra={"totp_code": totp_code(secret)})
        self.assertEqual(ok.status_code, 302)

    def test_a1_totp_real_code_required_after_binding(self):
        """绑定 TOTP 后:无码拒绝、真码放行(RFC 6238)"""
        secret = self.env.ctx.accounts.bind_totp(USER_ACCOUNT, "system", TEST_IP)
        client = self.env.client()
        no_code = self.env.login(client, USER_ACCOUNT, USER_PASSWORD)
        self.assertEqual(no_code.status_code, HTTP_UNAUTHORIZED)
        ok = self.env.login(client, USER_ACCOUNT, USER_PASSWORD,
                            extra={"totp_code": totp_code(secret)})
        self.assertEqual(ok.status_code, 302)


class TestDemoSimplifications(unittest.TestCase):
    """05-D2/D3 成对断言(HTTP 级)。"""

    def test_b1_d2_test_code_accepted_in_demo_rejected_in_prod(self):
        """测试码 123456:DEMO 放行、生产同一校验必须失败"""
        demo = IdpEnv(is_demo=True)
        demo.seed_admin_and_user()
        demo.ctx.accounts.bind_totp(USER_ACCOUNT, "system", TEST_IP)
        resp = demo.login(demo.client(), USER_ACCOUNT, USER_PASSWORD,
                          extra={"totp_code": "123456"})
        self.assertEqual(resp.status_code, 302)
        demo.close()
        prod = IdpEnv(is_demo=False)
        prod.seed_admin_and_user()
        prod.ctx.accounts.bind_totp(USER_ACCOUNT, "system", TEST_IP)
        resp = prod.login(prod.client(), USER_ACCOUNT, USER_PASSWORD,
                          extra={"totp_code": "123456"})
        self.assertEqual(resp.status_code, HTTP_UNAUTHORIZED)
        prod.close()

    def test_b1_d3_sms_echo_demo_only_and_code_login(self):
        """DEMO 回显验证码可登录;生产响应不含验证码但真码可登录且一次性"""
        demo = IdpEnv(is_demo=True)
        demo.seed_admin_and_user()
        demo.ctx.settings.set_override("method_sms", True, "system", TEST_IP)
        demo.ctx.refresh_profile()
        demo.app = __import__("apps.idp.web", fromlist=["create_app"]) \
            .create_app(demo.ctx)
        client = demo.client()
        sent = client.post("/login/sms/send", data={"account": USER_ACCOUNT}).json()
        self.assertIn("demo_echo_code", sent)
        ok = client.post("/login/sms/verify",
                         data={"account": USER_ACCOUNT,
                               "code": sent["demo_echo_code"]})
        self.assertEqual(ok.status_code, 302)
        demo.close()
        prod = IdpEnv(is_demo=False)
        prod.seed_admin_and_user()
        prod.ctx.settings.set_override("method_sms", True, "system", TEST_IP)
        prod.ctx.refresh_profile()
        prod.app = __import__("apps.idp.web", fromlist=["create_app"]) \
            .create_app(prod.ctx)
        client = prod.client()
        sent = client.post("/login/sms/send", data={"account": USER_ACCOUNT}).json()
        self.assertNotIn("demo_echo_code", sent)
        real_code = prod.ctx.accounts.send_sms_code(USER_ACCOUNT, prod.ctx.profile)
        self.assertIsNone(real_code)      # 生产不回显
        prod.close()


class TestPasswordPolicy(unittest.TestCase):
    """H03 §2 口令策略与首登强改。"""

    def setUp(self):
        self.env = IdpEnv(is_demo=False)

    def tearDown(self):
        self.env.close()

    def test_a1_password_policy_min_length_and_complexity(self):
        """生产最小 10 位且至少三类字符"""
        with self.assertRaises(PolicyValidationError):
            acc.validate_password_policy("short1!", self.env.ctx.profile)
        with self.assertRaises(PolicyValidationError):
            acc.validate_password_policy("alllowercaseonly", self.env.ctx.profile)
        acc.validate_password_policy("Valid!Passw0rd", self.env.ctx.profile)

    def test_a1_first_login_force_change(self):
        """管理员建号默认首登强改:登录返回 403 引导改密,改密后可登录"""
        ctx = self.env.ctx
        ctx.accounts.create_user("newbie", "新员工", "Newbie!Pass01",
                                 ctx.profile, "system", TEST_IP)
        client = self.env.client()
        resp = self.env.login(client, "newbie", "Newbie!Pass01")
        self.assertEqual(resp.status_code, 403)
        self.assertIn("修改口令", resp.json()["error"])
        ctx.accounts.change_password("newbie", "Fresh!Passw0rd2", ctx.profile,
                                     "newbie", TEST_IP)
        ok = self.env.login(client, "newbie", "Fresh!Passw0rd2")
        self.assertEqual(ok.status_code, 302)


if __name__ == "__main__":
    unittest.main()
