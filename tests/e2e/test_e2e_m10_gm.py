# -*- coding: utf-8 -*-
"""
@file    test_e2e_m10_gm.py
@brief   里程碑 10 浏览器全链路(Playwright + chromium 真服务):
         ① gm 套件下 SSO 全链——IdP(CRYPTO_SUITE=gm)真 HTTP 签发
            SM2-with-SM3 id_token,quiz RP 经 JWKS(双钥)真验签建会话;
            浏览器走完 /app/login → IdP 表单 → 回跳登录态;
         ② gm 套件下 certvault SPA 全链——本地登录 → 上传证件 →
            生成水印件 → 溯源命中(JWT 内存特例:全程站内路由导航);
            服务端佐证:blob 信封 alg=SM4-GCM、审计链全绿、
            crypto_suite_changed 锚点存在。
         无 Playwright 的离线目标环境整组自动跳过(GAP-15)。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import base64
import json
import unittest
import urllib.parse
import urllib.request

try:
    from playwright.sync_api import sync_playwright
    _PLAYWRIGHT = True
except ImportError:                                   # GAP-15:离线环境跳过
    _PLAYWRIGHT = False

from tests.cv_env import sample_cert_jpeg
from tests.e2e.live import (
    HttpTransport, LiveIdpEnv, LiveServer, LiveStack, pick_port,
)
from tests.idp_env import USER_ACCOUNT, USER_PASSWORD

from apps.certvault.web import create_app as create_certvault
from apps.quiz.web import create_app as create_quiz
from gd_crypto.suites import ALG_SM4_GCM, SUITE_GM
from gd_sso_client.client import load_config, SsoClient
from gd_storage import LocalVolatileStore
from gd_storage.audit import verify_chain

GM_ENV = {"CRYPTO_SUITE": SUITE_GM}
CV_USER = "e2e_gm_user"
CV_PASSWORD = "E2e!Gm#Passw0rd"


def _http_json(url: str) -> dict:
    """@brief 真 HTTP GET JSON"""
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


@unittest.skipUnless(_PLAYWRIGHT, "无 Playwright,浏览器组跳过(GAP-15)")
class TestBrowserM10GmSso(unittest.TestCase):
    """① gm 套件 SSO 全链(SM2SM3 令牌真 HTTP 签发-验签闭环)。"""

    @classmethod
    def setUpClass(cls):
        cls.play = sync_playwright().start()
        cls.browser = cls.play.chromium.launch()
        cls.stack = LiveStack(
            lambda db, suite, sso: create_quiz(db, suite, sso,
                                               guest_mode_enabled=True),
            "quiz", "quiz-gm-e2e", idp_extra_environ=dict(GM_ENV))

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()
        cls.browser.close()
        cls.play.stop()

    def test_gm_sso_full_chain_browser(self):
        """gm IdP:healthz/JWKS 双钥;浏览器 SSO 闭环 = SM2 签验真链路"""
        # 服务端形态:healthz 报 gm;JWKS 同时含 RSA 与 SM2 键(H04 §8.2.7)
        idp_health = _http_json(f"{self.stack.idp_base}/healthz")
        self.assertEqual(idp_health["crypto_suite"], SUITE_GM)
        jwks = _http_json(
            f"{self.stack.idp_base}/jwks.json")["keys"]
        curves = {key.get("crv") for key in jwks}
        kinds = {key.get("kty") for key in jwks}
        self.assertIn("SM2", curves)
        self.assertIn("RSA", kinds)
        # 浏览器全链:RP 登录页 → SSO → IdP 表单 → 回跳登录态。
        # 登录成功本身即 SM2-with-SM3 id_token 经真 HTTP JWKS 验签通过。
        page = self.browser.new_context().new_page()
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
        # 登录态确立(Cookie 会话):/me 返回 SSO 身份
        page.goto(f"{self.stack.rp_base}/me")
        self.assertIn(USER_ACCOUNT, page.content())
        # 启动锚点:显式 gm → crypto_suite_changed 已入审计,链校验全绿
        actions = [row[0] for row in self.stack.idp_env.ctx.db.query(
            "SELECT action FROM audit_logs ORDER BY id")]
        self.assertIn("crypto_suite_changed", actions)
        self.assertGreater(verify_chain(self.stack.idp_env.ctx.db), 0)
        page.context.close()


@unittest.skipUnless(_PLAYWRIGHT, "无 Playwright,浏览器组跳过(GAP-15)")
class TestBrowserM10GmCertvault(unittest.TestCase):
    """② gm 套件 certvault SPA 全链:登录→上传→发证→溯源(站内导航)。"""

    @classmethod
    def setUpClass(cls):
        cls.play = sync_playwright().start()
        cls.browser = cls.play.chromium.launch()
        # 手工装配(certvault 工厂需 ring/store,LiveStack 三参签名不够)
        idp_port = pick_port()
        cls.idp_env = LiveIdpEnv(f"http://127.0.0.1:{idp_port}",
                                 extra_environ=dict(GM_ENV))
        cls.idp_env.seed_admin_and_user()
        ctx = cls.idp_env.ctx
        cv_port = pick_port()
        cls.cv_base = f"http://127.0.0.1:{cv_port}"
        redirect = f"{cls.cv_base}/sso/callback"
        secret = ctx.oidc.create_client("cv-gm-e2e", "certvault 系统",
                                        [redirect])
        environ = {"SSO_ISSUER": ctx.issuer, "SSO_CLIENT_ID": "cv-gm-e2e",
                   "SSO_CLIENT_SECRET": secret, "SSO_REDIRECT": redirect,
                   "SSO_COOKIE_SECURE": "0"}
        sso = SsoClient(load_config(environ), LocalVolatileStore(),
                        HttpTransport(), system="certvault")
        cls.cv_app = create_certvault(ctx.db, ctx.ring, ctx.suite,
                                      LocalVolatileStore(), sso,
                                      allow_open_register=True)
        cls.cv_ctx = cls.cv_app.state.ctx
        cls.idp_server = LiveServer(cls.idp_env.app, port=idp_port)
        cls.cv_server = LiveServer(cls.cv_app, port=cv_port)
        # 预注册本地用户(真 HTTP;SPA 无注册页,注册属 API 面)
        body = urllib.parse.urlencode({
            "username": CV_USER, "password": CV_PASSWORD,
            "display_name": "GM 全链用户"}).encode()
        request = urllib.request.Request(
            f"{cls.cv_base}/auth/register", data=body, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(request, timeout=10) as resp:
            assert resp.status == 200

    @classmethod
    def tearDownClass(cls):
        cls.cv_server.stop()
        cls.idp_server.stop()
        cls.idp_env.close()
        cls.browser.close()
        cls.play.stop()

    def test_gm_certvault_browser_full_chain(self):
        """浏览器四步全链;服务端佐证 SM4-GCM 信封与审计链"""
        self.assertEqual(self.cv_ctx.suite.name, SUITE_GM)
        page = self.browser.new_context().new_page()
        # ① 本地登录(JWT 仅存页面内存 → 后续全程站内路由导航)
        page.goto(f"{self.cv_base}/app/login")
        page.wait_for_selector("text=本地账号登录")
        page.fill(".gd-field:has-text('用户名') input", CV_USER)
        page.fill(".gd-field:has-text('口令') input", CV_PASSWORD)
        with page.expect_response("**/auth/login"):
            page.get_by_role("button", name="登录", exact=True).click()
        page.wait_for_selector("text=上传证件")
        # ② 上传证件(大图提冗余;file input 直喂内存字节)
        image_bytes = sample_cert_jpeg(seed=7, size=(640, 960))
        page.fill("input[placeholder='如:出入证']", "E2E 出入证")
        page.set_input_files("input[type=file]", files=[{
            "name": "cert.jpg", "mimeType": "image/jpeg",
            "buffer": image_bytes}])
        with page.expect_response("**/certs/upload") as upload_info:
            page.click("button:has-text('上传')")
        self.assertEqual(upload_info.value.status, 200,
                         upload_info.value.text())
        page.wait_for_selector("text=E2E 出入证")
        # ③ 生成水印件(捕 /issue 响应取成品图字节)
        page.click("text=生成水印件")
        page.wait_for_selector("text=选择证件与流转介质")
        page.fill("input[placeholder='如:XX 银行']", "张三")
        page.fill("input[placeholder='如:办理开户']", "E2E 验证")
        with page.expect_response("**/issue") as issued_info:
            page.click("button:has-text('生成水印件并登记备案')")
        issued = issued_info.value.json()
        page.wait_for_selector("text=已生成 · 备案号")
        watermarked = base64.b64decode(issued["image_b64"])
        # ④ 溯源命中(同图盲提;命中人话消息含交付对象)
        page.click("text=溯源识别")
        page.wait_for_selector("text=上传疑似外泄文件")
        page.set_input_files("input[type=file]", files=[{
            "name": "suspect.jpg", "mimeType": "image/jpeg",
            "buffer": watermarked}])
        with page.expect_response("**/trace") as trace_info:
            page.click("button:has-text('开始溯源识别')")
        self.assertTrue(trace_info.value.json().get("found"),
                        trace_info.value.text())
        page.wait_for_selector("text=交付对象")
        # 服务端佐证:证件 blob 为 SM4-GCM 信封;审计链全绿含套件锚点
        import os
        row = self.cv_ctx.db.query(
            "SELECT blob_path FROM cv_certs ORDER BY id DESC LIMIT 1")[0]
        with open(os.path.join(self.cv_ctx.store._blob_dir, row[0]),
                  "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["alg"], ALG_SM4_GCM)
        actions = [r[0] for r in self.cv_ctx.db.query(
            "SELECT action FROM audit_logs ORDER BY id")]
        self.assertIn("crypto_suite_changed", actions)
        self.assertGreater(verify_chain(self.cv_ctx.db), 0)
        page.context.close()


if __name__ == "__main__":
    unittest.main()
