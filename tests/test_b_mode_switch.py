# -*- coding: utf-8 -*-
"""
@file    test_b_mode_switch.py
@brief   H09 §二 B 组:DEMO⇄等保三切换。B.2 完整恢复清单(测试码失效、演示账号
         停用、demo 会话吊销、自检全绿、healthz 报 prod、审计 mode_changed)、
         B.3 环境检查(演示密钥进生产被阻止;生产切 DEMO 需二次确认+审计)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import unittest

from tests.base import make_temp_db  # noqa: F401
from tests.idp_env import IdpEnv, TEST_IP, USER_ACCOUNT

from gd_common.errors import ConfigError, PolicyValidationError
from gd_crypto.keyring import DEMO_MASTER_KEY_HEX
from gd_storage import LocalVolatileStore
from apps.idp.accounts import DEMO_SEED_PASSWORD
from apps.idp.context import IdpContext
from apps.idp.mode import ModeService
from apps.idp.web import create_app
from selfcheck.asgi import AsgiClient

DEMO_ADMIN = "admin@example.com"
DEMO_USER = "demo@example.com"


class HotSwitchEnv(IdpEnv):
    """热切换环境:DEMO 经后台覆盖层开启(env 不锁定,可热切生产)。"""

    def _environ(self) -> dict:
        """@brief 不注入 DEMO_MODE env;由 settings 覆盖层控制"""
        return {"MASTER_KEY_HEX": "a1" * 32, "MASTER_KEY_ID": "mk1"}

    def enable_demo_via_settings(self):
        """@brief 后台覆盖层开 DEMO 并重建应用"""
        self.ctx.settings.set_override("demo_mode", True, "system", TEST_IP)
        self.ctx.refresh_profile()
        self.ctx.accounts.seed_demo_accounts(self.ctx.profile, TEST_IP)
        self.app = create_app(self.ctx)


class TestModeSwitchRecovery(unittest.TestCase):
    """B.2 恢复清单端到端。"""

    def setUp(self):
        self.env = HotSwitchEnv(is_demo=False)
        self.env.enable_demo_via_settings()

    def tearDown(self):
        self.env.close()

    def test_b2_demo_to_prod_full_recovery_checklist(self):
        """DEMO 全部简化生效 → 切生产后统一失效并留审计锚点"""
        ctx = self.env.ctx
        client = self.env.client()
        # DEMO 态:演示账号 + 测试码可登录、cert-demo 可用、healthz 报 demo
        ctx.accounts.bind_totp(DEMO_USER, "system", TEST_IP)
        resp = self.env.login(client, DEMO_USER, DEMO_SEED_PASSWORD,
                              extra={"totp_code": "123456"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(client.get("/login/cert-demo").status_code, 200)
        self.assertEqual(client.get("/healthz").json()["mode"], "demo")
        self.assertEqual(client.get("/portal").status_code, 200)   # demo 会话在线
        # 切生产(恢复清单顺序执行)
        report = ModeService(ctx).switch_to_prod("op_admin", TEST_IP)
        self.assertEqual(report["mode"], "prod")
        self.assertGreaterEqual(report["demo_accounts_disabled"], 2)
        self.assertGreaterEqual(report["demo_sessions_revoked"], 1)
        self.assertEqual(report["selfcheck"], "PASS")
        app_after = create_app(ctx)
        client_after = AsgiClient(app_after)
        # D4/D5 即刻 404;healthz 报 prod
        self.assertEqual(client_after.get("/login/cert-demo").status_code, 404)
        self.assertEqual(client_after.get("/wx/scan").status_code, 404)
        self.assertEqual(client_after.get("/healthz").json()["mode"], "prod")
        # 演示账号停用:同一凭据+测试码全部失效
        again = self.env.login(client_after, DEMO_USER, DEMO_SEED_PASSWORD,
                               extra={"totp_code": "123456"})
        self.assertEqual(again.status_code, 401)
        # demo 会话已吊销:原 Cookie 访问 /portal 被打回登录
        client.app = app_after
        self.assertEqual(client.get("/portal").status_code, 302)
        # 审计出现 mode_changed 锚点
        rows = ctx.db.query(
            "SELECT COUNT(*) FROM audit_logs WHERE action = 'mode_changed'")
        self.assertGreaterEqual(rows[0][0], 1)

    def test_b3_prod_to_demo_requires_confirm_and_reason(self):
        """生产切 DEMO:未确认/未填原因拒绝;确认后成功并审计"""
        ctx = self.env.ctx
        ModeService(ctx).switch_to_prod("op_admin", TEST_IP)
        with self.assertRaises(PolicyValidationError):
            ModeService(ctx).switch_to_demo("op_admin", TEST_IP,
                                            confirm=False, reason="联调")
        with self.assertRaises(PolicyValidationError):
            ModeService(ctx).switch_to_demo("op_admin", TEST_IP,
                                            confirm=True, reason="  ")
        report = ModeService(ctx).switch_to_demo("op_admin", TEST_IP,
                                                 confirm=True, reason="供应商联调")
        self.assertEqual(report["mode"], "demo")
        rows = ctx.db.query("SELECT detail FROM audit_logs"
                            " WHERE action = 'mode_changed' ORDER BY id DESC LIMIT 1")
        self.assertIn("供应商联调", rows[0][0])


class TestModeEnvChecks(unittest.TestCase):
    """B.3 环境检查。"""

    def test_b3_demo_master_key_blocked_from_prod(self):
        """演示派生主密钥进入生产被阻止(H05 §3.2.5)"""
        import tempfile
        ctx = IdpContext(f"sqlite:///{tempfile.mktemp(suffix='.db')}",
                         tempfile.mkdtemp(prefix="idp-keys-"),
                         store=LocalVolatileStore(),
                         environ={"MASTER_KEY_HEX": DEMO_MASTER_KEY_HEX,
                                  "MASTER_KEY_ID": "demo"})
        try:
            with self.assertRaises(ConfigError):
                ModeService(ctx).switch_to_prod("op_admin", TEST_IP)
        finally:
            ctx.close()

    def test_b3_env_locked_demo_mode_rejects_hot_switch_to_demo(self):
        """DEMO_MODE 由 env 锁定时,后台热切 DEMO 被拒并提示改 env(H05 §3.1)"""
        env = IdpEnv(is_demo=True)          # env 注入 DEMO_MODE=1(锁定层)
        try:
            with self.assertRaises(PolicyValidationError):
                ModeService(env.ctx).switch_to_demo("op_admin", TEST_IP,
                                                    confirm=True, reason="测试")
        finally:
            env.close()


if __name__ == "__main__":
    unittest.main()
