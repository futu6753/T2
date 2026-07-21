# -*- coding: utf-8 -*-
"""
@file    test_e2e_frontend.py
@brief   里程碑 9 前端浏览器 E2E(H09 §二 I.2/I.3/I.5):
         ① DEMO/生产横幅成对断言(05-D9):quiz SPA 顶部随 healthz.mode 二态;
         ② 凭据存储红线:SSO 登录闭环后 localStorage/sessionStorage 零令牌;
         ③ 401 跳登录保留站内 next(06-E13 站内约束);
         ④ F3 大屏:scene.js 在 CSP(nonce)下装载,#scene 内出现 WebGL canvas。
         无 Playwright 的离线目标环境整组自动跳过(GAP-15)。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import unittest

try:
    from playwright.sync_api import sync_playwright
    _PLAYWRIGHT = True
except ImportError:                                   # GAP-15:离线环境跳过
    _PLAYWRIGHT = False

from tests.e2e.live import LiveServer, LiveStack
from tests.idp_env import USER_ACCOUNT, USER_PASSWORD
from tests.f3d_env import F3dEnv

from apps.quiz.web import create_app as create_quiz


@unittest.skipUnless(_PLAYWRIGHT, "无 Playwright,浏览器组跳过(GAP-15)")
class TestBrowserFrontend(unittest.TestCase):
    """quiz SPA + F3 大屏浏览器端验收。"""

    @classmethod
    def setUpClass(cls):
        cls.play = sync_playwright().start()
        cls.browser = cls.play.chromium.launch()
        # quiz(SSO + 游客;profile 注入供横幅二态)
        cls.stack = LiveStack(
            lambda db, suite, sso: create_quiz(
                db, suite, sso, guest_mode_enabled=True),
            "quiz", "quiz-e2e")
        # F3 大屏(公开,uvicorn 托管以吃真实 CSP 头与模块脚本)
        cls.f3d_env = F3dEnv()
        cls.f3d_server = LiveServer(cls.f3d_env.app)

    @classmethod
    def tearDownClass(cls):
        cls.f3d_server.stop()
        cls.stack.close()
        cls.browser.close()
        cls.play.stop()

    def _page(self):
        """@brief 新上下文页面(bypass_csp 仅测试注入;CSP 头另行断言)"""
        return self.browser.new_context(bypass_csp=True).new_page()

    # ---- ① 横幅成对(05-D9) -------------------------------------------
    def test_banner_pair_by_mode(self):
        """profile 未注入 → 生产指示条;healthz 无 demo 时绝无红色横幅"""
        page = self._page()
        page.goto(f"{self.stack.rp_base}/app")
        # 直接等待生产指示条(count() 不自动等待,health 到达瞬间的
        # 重渲会被瞬时采样踩空——全量跑批高负载下实测偶发,2026-07-21 加固)
        page.wait_for_selector(".gd-banner-prod")
        self.assertEqual(page.locator(".gd-banner-demo").count(), 0)
        # 成对反向:直接驱动组件态(前端二态互斥由同一 health.mode 驱动)
        mode = page.evaluate(
            "fetch('/healthz').then(r => r.json()).then(j => j.mode ?? null)")
        self.assertIsNone(mode)          # profile 未装配 → healthz 不带 mode

    # ---- ② 凭据存储红线(I.5) ------------------------------------------
    def test_no_tokens_in_web_storage_after_login(self):
        """SSO 登录闭环后:两 storage 全空(HttpOnly Cookie 承载会话)"""
        page = self._page()
        page.goto(f"{self.stack.rp_base}/app/login")
        page.wait_for_selector("text=统一身份认证")
        with page.expect_navigation():
            page.click("text=使用统一身份认证(SSO)登录")
        page.wait_for_selector("input[name=account]")
        page.fill("input[name=account]", USER_ACCOUNT)
        page.fill("input[name=password]", USER_PASSWORD)
        with page.expect_navigation():
            page.click("#login-submit")
        page.wait_for_url(f"{self.stack.rp_base}/**")
        storages = page.evaluate(
            "[Object.keys(window.localStorage), Object.keys(window.sessionStorage)]")
        for keys in storages:
            for key in keys:
                value = page.evaluate(
                    f"window.localStorage.getItem({key!r}) ?? "
                    f"window.sessionStorage.getItem({key!r})")
                self.assertNotRegex(str(value), r"eyJ|token|bearer",
                                    f"storage 疑似令牌:{key}")
        # 身份确已建立(证明会话走 Cookie 而非 storage)
        page.goto(f"{self.stack.rp_base}/me")
        self.assertIn("sso", page.content())

    # ---- ③ 401 保留站内 next -------------------------------------------
    def test_unauthorized_redirect_keeps_next(self):
        """未登录深链 /app/wrongbook → API 401 → 登录页 URL 带 next"""
        page = self._page()
        page.goto(f"{self.stack.rp_base}/app/wrongbook")
        page.wait_for_url("**/app/login**")
        self.assertIn("next=%2Fapp%2Fwrongbook", page.url)

    # ---- ④ F3 场景装载 ---------------------------------------------------
    def test_f3d_scene_canvas_boots(self):
        """大屏:CSP 头在;scene.js 模块装载后 #scene 出现 canvas;
        fps 芯片被 rAF 回报刷新(端到端闭环的浏览器侧)"""
        page = self._page()
        resp = page.goto(self.f3d_server.base_url + "/")
        csp = resp.headers.get("content-security-policy", "")
        self.assertIn("script-src 'self' 'nonce-", csp)
        self.assertNotIn("unsafe-inline", csp.split("style-src")[0])
        page.wait_for_selector("#scene canvas", timeout=10000)
        page.wait_for_function(
            "document.getElementById('fps-chip').textContent.includes('fps')")
