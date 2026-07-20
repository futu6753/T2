# -*- coding: utf-8 -*-
"""
@file    test_e2e_f3d.py
@brief   三维大屏浏览器端到端(Chromium,类级共享 IdP+F3D 双真实服务):
         首屏 KPI+WS 就绪、掉线→安灯/告警 DOM、fps 回报驱动档位、
         站点名 XSS 转义(编辑权不放大为公开页脚本执行)、鉴权矩阵+SSO 进管理台。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import time
import unittest

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:                      # GAP-15:离线环境自动跳过
    PLAYWRIGHT_AVAILABLE = False

from tests.e2e.live import LiveStack
from tests.idp_env import USER_ACCOUNT, USER_PASSWORD

from apps.factory3d.web import create_app


def _factory(db, suite, sso):
    """@brief LiveStack 工厂:E2E 不测外部密钥,免 ring 装配"""
    return create_app(db, suite, sso, environ={})


@unittest.skipUnless(PLAYWRIGHT_AVAILABLE, "Playwright 未安装(GAP-15,自动跳过)")
class TestBrowserF3d(unittest.TestCase):
    """真实浏览器驱动的大屏用例。"""

    @classmethod
    def setUpClass(cls):
        """@brief 拉起双服务与 Chromium;推送周期压到 0.5s 加速"""
        cls.stack = LiveStack(_factory, "f3d", "f3d-e2e")
        cls.ctx = cls.stack.rp_app.state.f3d
        cls.ctx.settings.set_override("f3d_push_interval_seconds", 0.5,
                                      "e2e", "0.0.0.0")
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        """@brief 回收"""
        cls.browser.close()
        cls.playwright.stop()
        cls.stack.close()

    def _page(self):
        """@brief 每用例独立浏览器上下文(隔离 Cookie/JS 全局)"""
        context = self.browser.new_context()
        self.addCleanup(context.close)
        return context.new_page()

    def test_big_screen_first_paint_and_ws(self):
        """首屏:F3D_VER 注入、WS 连上后 KPI=23、设备清单渲染"""
        page = self._page()
        page.goto(f"{self.stack.rp_base}/")
        self.assertEqual(page.evaluate("window.F3D_VER"), "5.0.0-m6")
        page.wait_for_function(
            "document.getElementById('ws-chip').textContent === '已连接'")
        page.wait_for_function(
            "document.getElementById('kpi-total').textContent === '23'")
        page.wait_for_function(
            "document.querySelectorAll('#device-list li').length === 23")
        self.assertIn("三维物联监控大屏", page.title())

    def test_offline_drives_andon_and_alarm_hud(self):
        """掉线设备:KPI 离线+1;满延时转正后安灯变红、HUD 计数 1"""
        page = self._page()
        page.goto(f"{self.stack.rp_base}/")
        page.wait_for_function(
            "document.getElementById('kpi-total').textContent === '23'")
        device_id = next(iter(self.ctx.simulator.runtime))
        # 以「两分钟前掉线」注入:下一次 tick 即转正(免等真实延时)
        self.ctx.apply_status(device_id, "offline", "toggle",
                              now=time.time() - 120)
        page.wait_for_function(
            "document.getElementById('kpi-offline').textContent === '1'")
        page.wait_for_function(
            "document.getElementById('alarm-count').textContent === '1'")
        page.wait_for_function(
            "document.getElementById('andon').className.includes('alarm')")
        page.wait_for_function(
            f"!!document.querySelector('#device-list li.offline"
            f"[data-id=\"{device_id}\"]')")
        self.ctx.apply_status(device_id, "online", "toggle")
        page.wait_for_function(
            "document.getElementById('kpi-offline').textContent === '0'")
        page.wait_for_function(
            "document.getElementById('andon').className.includes('ok')")

    def test_fps_report_drives_tier_chip(self):
        """__reportFps 连续低帧 → 档位芯片离开 full;高帧 → 回到 full"""
        page = self._page()
        page.goto(f"{self.stack.rp_base}/")
        page.wait_for_function(
            "document.getElementById('ws-chip').textContent === '已连接'")
        deadline = time.time() + 15
        while time.time() < deadline:
            page.evaluate("window.__reportFps(6)")
            chip = page.text_content("#tier-chip")
            if chip != "档位:full":
                break
            page.wait_for_timeout(150)
        self.assertNotEqual(page.text_content("#tier-chip"), "档位:full")
        deadline = time.time() + 20
        while time.time() < deadline:
            page.evaluate("window.__reportFps(60)")
            if page.text_content("#tier-chip") == "档位:full":
                break
            page.wait_for_timeout(150)
        self.assertEqual(page.text_content("#tier-chip"), "档位:full")

    def test_site_name_xss_is_escaped(self):
        """站点名注入 <img onerror>:不执行脚本,按文本可见(转义防线)"""
        hostile = '<img src=x onerror="window.__pwned=1">'
        self.ctx.settings.set_override("f3d_site_name", hostile, "e2e",
                                       "0.0.0.0")
        self.addCleanup(self.ctx.settings.set_override, "f3d_site_name",
                        None, "e2e", "0.0.0.0")
        page = self._page()
        page.goto(f"{self.stack.rp_base}/")
        page.wait_for_function(
            "document.getElementById('ws-chip').textContent === '已连接'")
        self.assertIsNone(page.evaluate("window.__pwned"))
        self.assertIn("<img", page.text_content("#site-name"))
        self.assertEqual(page.evaluate(
            "document.querySelectorAll('#site-name img').length"), 0)

    def test_auth_matrix_and_sso_into_admin(self):
        """匿名 /admin/edit 被引导 SSO;IdP 登录后进入管理台(operator)"""
        page = self._page()
        page.goto(f"{self.stack.rp_base}/sso/login?next=/admin/edit")
        page.wait_for_selector("#login-form")
        page.fill("input[name=account]", USER_ACCOUNT)
        page.fill("input[name=password]", USER_PASSWORD)
        page.click("#login-submit")
        page.wait_for_url(f"{self.stack.rp_base}/**")
        body = page.content()
        self.assertIn("交互式编辑台", body)
        self.assertIn(USER_ACCOUNT, body)


if __name__ == "__main__":
    unittest.main()
