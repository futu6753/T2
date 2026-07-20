# -*- coding: utf-8 -*-
"""
@file    test_e2e_idp.py
@brief   浏览器 E2E(Chromium):登录页可操作性(06-E18)、rid 透传 SSO 全链
         (06-E19)、首登强改闭环、门户 HTML、免登跳转。真 uvicorn + 真重定向。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import unittest

try:                                     # 离线目标环境无 Playwright 整组跳过(GAP-15)
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:                      # pragma: no cover - 离线分支
    PLAYWRIGHT_AVAILABLE = False

from tests.e2e.live import LiveStack
from tests.idp_env import (ADMIN_ACCOUNT, ADMIN_PASSWORD, TEST_IP,
                           USER_ACCOUNT, USER_PASSWORD)

from apps.nvr.web import create_app as create_nvr

NEW_USER = "browseruser"
NEW_USER_TMP_PASSWORD = "Tmp!Passw0rd11"
NEW_USER_FINAL_PASSWORD = "Fin4l!Passw0rd"


@unittest.skipUnless(PLAYWRIGHT_AVAILABLE, "Playwright 未安装(GAP-15,自动跳过)")
class TestBrowserIdp(unittest.TestCase):
    """真实浏览器驱动的 IdP/SSO 用例(类级共享双服务与浏览器)。"""

    @classmethod
    def setUpClass(cls):
        """@brief 拉起 IdP+nvr 双真实服务与 Chromium"""
        cls.stack = LiveStack(create_nvr, "nvr", "nvr-e2e")
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        """@brief 回收浏览器与双服务"""
        cls.browser.close()
        cls.playwright.stop()
        cls.stack.close()

    def _page(self):
        """@brief 每用例独立上下文页面(隔离 Cookie)"""
        context = self.browser.new_context()
        self.addCleanup(context.close)
        return context.new_page()

    def _fill_login(self, page, account: str, password: str):
        """@brief 在登录页填表并提交"""
        page.fill("input[name=account]", account)
        page.fill("input[name=password]", password)
        page.click("#login-submit")

    def test_e18_login_page_operable(self):
        """E18:/login 表单含账号/口令/动态码输入与提交按钮,浏览器可操作"""
        page = self._page()
        page.goto(f"{self.stack.idp_base}/login")
        self.assertTrue(page.is_visible("input[name=account]"))
        self.assertTrue(page.is_visible("input[name=password]"))
        self.assertTrue(page.is_visible("input[name=totp_code]"))
        self.assertTrue(page.is_visible("#login-submit"))

    def test_e18_wrong_password_recoverable(self):
        """E18:错口令→PRG 人话报错(非裸 JSON),页面仍可重试并登录成功"""
        page = self._page()
        page.goto(f"{self.stack.idp_base}/login")
        self._fill_login(page, USER_ACCOUNT, "Wrong!Pass0")
        page.wait_for_selector("#login-error")
        self.assertIn("用户名或口令错误", page.inner_text("#login-error"))
        self._fill_login(page, USER_ACCOUNT, USER_PASSWORD)
        page.wait_for_selector("#portal-title")
        self.assertIn("企业门户", page.inner_text("#portal-title"))

    def test_first_login_force_change_browser_loop(self):
        """首登强改:临时口令→改密页→成功提示→新口令登录全浏览器闭环"""
        ctx = self.stack.idp_env.ctx
        ctx.accounts.create_user(NEW_USER, "浏览器用户", NEW_USER_TMP_PASSWORD,
                                 ctx.profile, "system", TEST_IP)
        page = self._page()
        page.goto(f"{self.stack.idp_base}/login")
        self._fill_login(page, NEW_USER, NEW_USER_TMP_PASSWORD)
        page.wait_for_selector("form[action='/account/password']")
        page.fill("input[name=old_password]", NEW_USER_TMP_PASSWORD)
        page.fill("input[name=new_password]", NEW_USER_FINAL_PASSWORD)
        page.fill("input[name=confirm_password]", NEW_USER_FINAL_PASSWORD)
        page.click("form[action='/account/password'] button[type=submit]")
        page.wait_for_selector("#login-notice")
        self.assertIn("修改成功", page.inner_text("#login-notice"))
        self._fill_login(page, NEW_USER, NEW_USER_FINAL_PASSWORD)
        page.wait_for_selector("#portal-title")

    def test_portal_html_for_browser(self):
        """门户对浏览器渲染 HTML 卡片页(API 侧 JSON 契约不变)"""
        page = self._page()
        page.goto(f"{self.stack.idp_base}/login")
        self._fill_login(page, ADMIN_ACCOUNT, ADMIN_PASSWORD)
        page.wait_for_selector("#portal-apps")
        self.assertIn("nvr", page.inner_text("#portal-apps"))

    def test_e19_sso_full_chain_rid_passthrough(self):
        """E19:RP→authorize→登录页(rid 隐藏透传)→登录后续接授权链回 RP"""
        page = self._page()
        page.goto(f"{self.stack.rp_base}/sso/login?next=/admin")
        page.wait_for_selector("#login-form")
        rid_value = page.get_attribute("input[name=rid]", "value")
        self.assertTrue(rid_value, "登录页必须携带 rid 隐藏域(06-E19)")
        self._fill_login(page, USER_ACCOUNT, USER_PASSWORD)
        page.wait_for_url(f"{self.stack.rp_base}/**")
        self.assertTrue(page.url.startswith(self.stack.rp_base),
                        f"登录成功须续接授权链回 RP,实际停在 {page.url}")

    def test_second_visit_skips_login(self):
        """免登跳转:IdP 已有会话时二次访问 RP 不再出登录页"""
        page = self._page()
        page.goto(f"{self.stack.rp_base}/sso/login?next=/")
        self._fill_login(page, USER_ACCOUNT, USER_PASSWORD)
        page.wait_for_url(f"{self.stack.rp_base}/**")
        page.goto(f"{self.stack.rp_base}/sso/login?next=/")
        page.wait_for_url(f"{self.stack.rp_base}/**")
        self.assertNotIn("/login", page.url)


if __name__ == "__main__":
    unittest.main()
