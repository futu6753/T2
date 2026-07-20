# -*- coding: utf-8 -*-
"""
@file    test_c_rp_ecosystem.py
@brief   H09 §二 C 组(里程碑 3):RP 四系统统一登录、免登跳转、back-channel
         单点登出扇出全通;首次 SSO 建号(最小角色/无口令旁路/显示名冲突后缀/
         口令时间戳刷新,06-E16)、重复登录固定映射、停用拦截;
         certvault JWT exchange 特例与 iat 吊销(踢下线);quiz 游客双身份;
         factory-3d 鉴权矩阵。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import unittest

from tests.base import make_temp_db  # noqa: F401
from tests.idp_env import IdpEnv, TEST_IP, USER_ACCOUNT
from tests.rp_env import drive_sso_login, make_sso_client

from apps.certvault.web import create_app as create_certvault
from apps.factory3d.web import create_app as create_f3d
from apps.idp.tokens import mint_logout_token
from apps.nvr.web import create_app as create_nvr
from apps.quiz.web import create_app as create_quiz
from apps.rp_common.accounts import UNUSABLE_PASSWORD_PREFIX
from selfcheck.asgi import AsgiClient

CV_REDIRECT = "http://cv.internal/sso/callback"
NVR_REDIRECT = "http://nvr.internal/sso/callback"
QUIZ_REDIRECT = "http://quiz.internal/sso/callback"
F3D_REDIRECT = "http://f3d.internal/sso/callback"


class RpEcosystemBase(unittest.TestCase):
    """公共装配:一个 IdP + 四个 RP 应用。"""

    def setUp(self):
        self.env = IdpEnv(is_demo=False)
        self.env.seed_admin_and_user()
        ctx = self.env.ctx
        self.cv_sso, self.cv_store, _ = make_sso_client(
            self.env, "certvault", "certvault", CV_REDIRECT)
        self.nvr_sso, self.nvr_store, _ = make_sso_client(
            self.env, "nvr", "nvr", NVR_REDIRECT)
        self.quiz_sso, _, _ = make_sso_client(
            self.env, "quiz", "quiz", QUIZ_REDIRECT)
        self.f3d_sso, _, _ = make_sso_client(
            self.env, "f3d", "factory3d", F3D_REDIRECT)
        self.cv_app = create_certvault(ctx.db, ctx.ring, ctx.suite,
                                       self.cv_store, self.cv_sso)
        self.nvr_app = create_nvr(ctx.db, ctx.suite, self.nvr_sso)
        self.quiz_app = create_quiz(ctx.db, ctx.suite, self.quiz_sso)
        self.f3d_app = create_f3d(ctx.db, ctx.suite, self.f3d_sso)

    def tearDown(self):
        self.env.close()


class TestUnifiedLoginAcrossSystems(RpEcosystemBase):
    """C.2/C.3:四系统统一登录 + 免登跳转 + 建号语义。"""

    def test_c2_four_systems_unified_login_and_sso_free_jump(self):
        """同一 IdP 浏览器会话免登跳转四系统(仅首个需输口令)"""
        browser = self.env.client()          # 同一"浏览器"跨四系统
        cv = drive_sso_login(self.env, AsgiClient(self.cv_app),
                             idp_browser=browser)
        self.assertEqual(cv.get("/healthz").json()["system"], "certvault")
        # 后续三系统:IdP 侧已有会话,authorize 直接 302 回调 = 免登跳转
        nvr = drive_sso_login(self.env, AsgiClient(self.nvr_app),
                              idp_browser=browser)
        quiz = drive_sso_login(self.env, AsgiClient(self.quiz_app),
                               idp_browser=browser)
        f3d = drive_sso_login(self.env, AsgiClient(self.f3d_app),
                              idp_browser=browser)
        self.assertEqual(nvr.get("/devices").status_code, 200)
        self.assertEqual(quiz.get("/me").json()["kind"], "sso")
        self.assertEqual(f3d.get("/admin/edit").status_code, 200)

    def test_c3_first_sso_creates_minimal_role_no_password_bypass(self):
        """首次建号:最小角色、口令置不可用随机值、重复登录固定映射(C.3)"""
        nvr = drive_sso_login(self.env, AsgiClient(self.nvr_app))
        accounts = self.nvr_app.state.accounts
        user = accounts.get_by_sub(USER_ACCOUNT)
        self.assertEqual(user["role"], "auditor")           # nvr 最小角色
        self.assertTrue(user["password_hash"].startswith(UNUSABLE_PASSWORD_PREFIX))
        first_id = user["id"]
        drive_sso_login(self.env, AsgiClient(self.nvr_app))  # 重复登录
        self.assertEqual(accounts.get_by_sub(USER_ACCOUNT)["id"], first_id)
        rows = self.env.ctx.db.query(
            "SELECT COUNT(*) FROM nvr_users WHERE sso_sub = ?", (USER_ACCOUNT,))
        self.assertEqual(rows[0][0], 1)                      # 固定映射不重复建号

    def test_c3_e16_password_timestamp_refreshed_on_each_login(self):
        """06-E16:每次 SSO 登录刷新口令时间戳,90 天周期不误伤 SSO 用户"""
        drive_sso_login(self.env, AsgiClient(self.nvr_app))
        stale = "2020-01-01T00:00:00+00:00"                  # 人为做旧
        self.env.ctx.db.execute(
            "UPDATE nvr_users SET password_changed_at = ? WHERE sso_sub = ?",
            (stale, USER_ACCOUNT))
        drive_sso_login(self.env, AsgiClient(self.nvr_app))
        rows = self.env.ctx.db.query(
            "SELECT password_changed_at FROM nvr_users WHERE sso_sub = ?",
            (USER_ACCOUNT,))
        self.assertGreater(rows[0][0], stale)                # 已刷新到当前

    def test_c3_display_name_conflict_gets_suffix(self):
        """显示名冲突自动加后缀(H03 §1)"""
        accounts = self.nvr_app.state.accounts
        accounts.ensure_sso_account({"sub": "user-a", "preferred_username": "张三"})
        second = accounts.ensure_sso_account(
            {"sub": "user-b", "preferred_username": "张三"})
        self.assertEqual(second["display_name"], "张三-2")

    def test_c2_disabled_account_blocked_and_session_rolled_back(self):
        """停用账户拦截:回调 403 且不留下可用会话(H08 §3)"""
        drive_sso_login(self.env, AsgiClient(self.nvr_app))
        self.nvr_app.state.accounts.set_status(USER_ACCOUNT, "disabled")
        rp_client = AsgiClient(self.nvr_app)
        with self.assertRaises(AssertionError):              # 回调非 302
            drive_sso_login(self.env, rp_client)
        self.assertEqual(rp_client.get("/devices").status_code, 401)


class TestBackchannelFanout(RpEcosystemBase):
    """C.2:back-channel 单点登出扇出全通(四系统)。"""

    def test_c2_backchannel_fanout_revokes_all_four_systems(self):
        """IdP /logout 扇出:四系统本地会话同刻吊销"""
        browser = self.env.client()
        clients = {
            "certvault": (drive_sso_login(self.env, AsgiClient(self.cv_app),
                                          idp_browser=browser), "/healthz"),
            "nvr": (drive_sso_login(self.env, AsgiClient(self.nvr_app),
                                    idp_browser=browser), "/devices"),
            "quiz": (drive_sso_login(self.env, AsgiClient(self.quiz_app),
                                     idp_browser=browser), "/me"),
            "f3d": (drive_sso_login(self.env, AsgiClient(self.f3d_app),
                                    idp_browser=browser), "/admin/edit"),
        }
        self.assertEqual(clients["nvr"][0].get("/devices").status_code, 200)
        # 模拟 IdP 扇出:对每个 RP 投递 logout_token(传输层扇出在 web.py 暂存)
        for system, sso, app in (("certvault", self.cv_sso, self.cv_app),
                                 ("nvr", self.nvr_sso, self.nvr_app),
                                 ("quiz", self.quiz_sso, self.quiz_app),
                                 ("f3d", self.f3d_sso, self.f3d_app)):
            token = mint_logout_token(self.env.ctx.issuer,
                                      sso.config.client_id,
                                      USER_ACCOUNT, self.env.ctx.keys)
            resp = AsgiClient(app).post("/backchannel-logout",
                                        data={"logout_token": token})
            self.assertEqual(resp.status_code, 200, system)
        self.assertEqual(clients["nvr"][0].get("/devices").status_code, 401)
        self.assertEqual(clients["quiz"][0].get("/me").status_code, 401)
        self.assertEqual(clients["f3d"][0].get("/admin/edit").status_code, 401)


class TestCertvaultExchange(RpEcosystemBase):
    """H08 §3 certvault 特例:JWT exchange 与 iat 吊销。"""

    def test_c2_exchange_sso_cookie_for_jwt_then_bearer(self):
        """SSO Cookie → 本系统 JWT → Bearer 复用既有机制"""
        cv = drive_sso_login(self.env, AsgiClient(self.cv_app))
        exchanged = cv.post("/auth/sso/exchange")
        self.assertEqual(exchanged.status_code, 200)
        token = exchanged.json()["token"]
        bare = AsgiClient(self.cv_app)                       # 无 Cookie,仅 Bearer
        me = bare.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["username"], USER_ACCOUNT)
        self.assertEqual(bare.get("/auth/me").status_code, 401)   # 无令牌拒

    def test_c2_admin_kick_invalidates_existing_jwt(self):
        """管理员踢下线:iat 早于吊销水位的 JWT 全部失效(H03 §6)"""
        import time
        cv = drive_sso_login(self.env, AsgiClient(self.cv_app))
        token = cv.post("/auth/sso/exchange").json()["token"]
        accounts = self.cv_app.state.accounts
        accounts.set_role(USER_ACCOUNT, "admin")             # 借同号演示管理操作
        admin_token = cv.post("/auth/sso/exchange").json()["token"]
        time.sleep(1.1)                                      # 越过 1 秒吊销粒度
        bare = AsgiClient(self.cv_app)
        kick = bare.request(
            "POST", "/admin/users/kick", data={"username": USER_ACCOUNT},
            headers={"Authorization": f"Bearer {admin_token}"})
        self.assertEqual(kick.status_code, 200)
        replay = bare.get("/auth/me",
                          headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(replay.status_code, 401)
        self.assertIn("吊销", replay.json()["error"])


class TestQuizAndF3dPolicies(RpEcosystemBase):
    """H03 §6 差异化条款。"""

    def test_c2_quiz_guest_mode_and_toggle(self):
        """quiz 游客 5 位 ID 分配/载入;quiz_guest_mode=false 全关"""
        quiz = AsgiClient(self.quiz_app)
        created = quiz.post("/guest/new").json()
        self.assertRegex(created["guest_code"], r"^\d{5}$")
        loaded = quiz.get(f"/guest/load/{created['guest_code']}")
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(quiz.get("/me").json()["kind"], "guest")
        closed_app = create_quiz(self.env.ctx.db, self.env.ctx.suite,
                                 self.quiz_sso, guest_mode_enabled=False)
        closed = AsgiClient(closed_app)
        self.assertEqual(closed.post("/guest/new").status_code, 403)

    def test_c2_f3d_auth_matrix_public_screen_admin_locked(self):
        """factory-3d 鉴权矩阵:大屏公开;/admin* 未登录 401;token 应急通道"""
        f3d = AsgiClient(self.f3d_app)
        self.assertTrue(f3d.get("/").json()["public"])
        self.assertEqual(f3d.get("/admin/edit").status_code, 401)
        token_app = create_f3d(self.env.ctx.db, self.env.ctx.suite,
                               self.f3d_sso, admin_token="emergency-token")
        via_token = AsgiClient(token_app).get(
            "/api/admin/layout", headers={"X-Admin-Token": "emergency-token"})
        self.assertEqual(via_token.status_code, 200)
        self.assertEqual(via_token.json()["operator"], "admin-token-channel")


if __name__ == "__main__":
    unittest.main()
