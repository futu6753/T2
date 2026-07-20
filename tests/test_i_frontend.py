# -*- coding: utf-8 -*-
"""
@file    test_i_frontend.py
@brief   里程碑 9 前端质量 I 组验收(H09 §二 I,离线可跑部分):
         E3 双条款静态断言、构建产物外链零命中、ui-kit 纯函数(四态错误
         文案含等待时长,经 node 直载 TS 源)、SPA 统一托管语义(深链兜底/
         CSP/越界/产物缺失 503)、healthz 横切徽标字段、游客载入闭环、
         F3 静态资产与 nonce CSP、离线构建前提(lockfile)。
         浏览器侧五断言见 tests/e2e/test_e2e_frontend.py。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from selfcheck.asgi import AsgiClient
from tests.quiz_env import QuizEnv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_NODE = shutil.which("node")


class TestE3AndExternalScan(unittest.TestCase):
    """I.1 E3 双条款 + I.4 外链零命中(两脚本以子进程等价 CI 步骤)。"""

    def test_i_e3_static_assertions(self):
        """E3 条款一/二全量文档通过;并对条款一做反例自检(能抓真违规)"""
        result = subprocess.run(
            ["python3", os.path.join(ROOT, "scripts", "check_frontend_e3.py")],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # 反例自检:引用先于定义的内联脚本必须被抓住
        import scripts.check_frontend_e3 as e3
        bad = ('<html><body><script>document.getElementById("late")'
               '.textContent="x";</script><div id="late"></div></body></html>')
        self.assertTrue(e3._check_clause_one("bad", bad))
        bad2 = '<div hidden>x</div>'
        self.assertTrue(e3._check_clause_two("bad2", bad2, ""))

    def test_i_external_urls_zero_hits(self):
        """构建产物承载性外链与黑名单域零命中;反例自检"""
        result = subprocess.run(
            ["python3",
             os.path.join(ROOT, "scripts", "scan_frontend_external.py")],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        import scripts.scan_frontend_external as scan
        self.assertTrue(scan._LOAD_BEARING.search(
            '<script src="https://cdn.evil/x.js">'))
        self.assertTrue(scan._LOAD_BEARING.search(
            "@import 'https://fonts.example/css';"))
        self.assertFalse(scan._LOAD_BEARING.search(
            "// 参见 https://threejs.org 文档"))   # 说明性链接不误报


@unittest.skipUnless(_NODE, "无 node,ui-kit 纯函数组跳过(打包机必跑)")
class TestUiKitPure(unittest.TestCase):
    """I.3 组件测试:fetch 封装四态错误文案(node 直载 TS 源)。"""

    def test_i_ui_kit_pure_functions(self):
        """26 组断言:401/403/423/429 区分、等待时长、信封、打码、路由"""
        result = subprocess.run(
            [_NODE, "--experimental-strip-types",
             os.path.join(ROOT, "frontend", "ui-kit", "tests",
                          "run_pure_tests.mjs")],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("全部通过", result.stderr + result.stdout)


class TestSpaHosting(unittest.TestCase):
    """I 组:F2 SPA 统一托管语义(rp_common.spa)。"""

    @classmethod
    def setUpClass(cls):
        cls.env = QuizEnv()
        cls.client = AsgiClient(cls.env.app)

    def test_i_spa_deep_link_and_csp(self):
        """/app 深链兜底回 index;统一 CSP;资产命中;越界不外泄"""
        for path in ("/app", "/app/wrongbook", "/app/q/17"):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, path)
            self.assertIn(b"root", resp.body)
            csp = resp.headers.get("content-security-policy", "")
            self.assertIn("default-src 'self'", csp)
            self.assertNotIn("unsafe-eval", csp)
        assets = os.listdir(os.path.join(ROOT, "apps", "quiz", "web",
                                         "dist", "assets"))
        js = next(a for a in assets if a.endswith(".js"))
        self.assertEqual(self.client.get(f"/app/assets/{js}").status_code, 200)
        leak = self.client.get("/app/../../../etc/passwd")
        self.assertNotIn(b"root:x", leak.body)

    def test_i_missing_dist_returns_503(self):
        """产物缺失 → /app 503 明示前端未构建(API 面不受影响)"""
        from apps.quiz.web import create_app as create_quiz
        with tempfile.TemporaryDirectory() as empty:
            app = create_quiz(self.env.idp.ctx.db, self.env.idp.ctx.suite,
                              self.env.sso, spa_dist=empty)
            client = AsgiClient(app)
            resp = client.get("/app")
            self.assertEqual(resp.status_code, 503)
            self.assertIn("构建", resp.json()["error"])
            self.assertEqual(client.get("/healthz").status_code, 200)

    def test_i_healthz_mode_suite_badge_fields(self):
        """H11 §二横切:profile 注入后 healthz 附 mode+crypto_suite;
        未注入保持既有断言零影响"""
        from apps.quiz.web import create_app as create_quiz
        base = self.client.get("/healthz").json()
        self.assertNotIn("mode", base)
        app = create_quiz(self.env.idp.ctx.db, self.env.idp.ctx.suite,
                          self.env.sso, profile=self.env.idp.ctx.profile)
        health = AsgiClient(app).get("/healthz").json()
        self.assertIn(health["mode"], ("demo", "prod"))
        self.assertTrue(health["crypto_suite"])

    def test_i_guest_load_sets_cookie_and_progress(self):
        """里程碑 9 修复:输 ID 载入=设游客 Cookie+回真实进度(H03 §6)"""
        first = AsgiClient(self.env.app)
        code = first.post("/guest/new").json()["guest_code"]
        second = AsgiClient(self.env.app)     # 新设备
        loaded = second.get(f"/guest/load/{code}")
        self.assertEqual(loaded.status_code, 200)
        self.assertIn("attempted", loaded.json()["progress"])
        me = second.get("/me").json()
        self.assertEqual((me["kind"], me["guest_code"]), ("guest", code))


class TestF3dStatic(unittest.TestCase):
    """I 组:F3 静态资产与 nonce CSP(非浏览器侧)。"""

    @classmethod
    def setUpClass(cls):
        from tests.f3d_env import F3dEnv
        cls.env = F3dEnv()
        cls.client = AsgiClient(cls.env.app)

    def test_i_f3d_nonce_csp_and_scene_assets(self):
        """大屏页 per-response nonce 注入两处且入 CSP;scene.js 与 Three.js
        本地副本可达;越界 404;nonce 逐响应不同"""
        resp = self.client.get("/", headers={"accept": "text/html"})
        body = resp.body.decode()
        csp = resp.headers.get("content-security-policy", "")
        nonces = re.findall(r'nonce="([^"]+)"', body)
        self.assertEqual(len(set(nonces)), 1)
        self.assertEqual(len(nonces), 2)
        self.assertIn(f"'nonce-{nonces[0]}'", csp)
        self.assertNotIn("unsafe-inline", csp.split("style-src")[0])
        self.assertIn("/static/scene.js", body)
        again = self.client.get("/", headers={"accept": "text/html"})
        self.assertNotEqual(nonces[0],
                            re.findall(r'nonce="([^"]+)"',
                                       again.body.decode())[0])
        self.assertEqual(self.client.get("/static/scene.js").status_code, 200)
        three = self.client.get("/static/vendor/three.module.min.js")
        self.assertEqual(three.status_code, 200)
        self.assertGreater(len(three.body), 100_000)
        self.assertEqual(
            self.client.get("/static/../web.py").status_code, 404)


class TestOfflineBuildPreconditions(unittest.TestCase):
    """I.4 断网构建前提:lockfile 锁定 + 产物随仓交付(H11 §七)。"""

    def test_i_lockfile_and_dist_shipped(self):
        """package-lock.json 在;四 SPA dist 均含 index.html 与 hash 资产"""
        self.assertTrue(os.path.isfile(
            os.path.join(ROOT, "frontend", "package-lock.json")))
        for app in ("quiz", "certvault", "nvr", "adapter"):
            dist = os.path.join(ROOT, "apps", app, "web", "dist")
            self.assertTrue(
                os.path.isfile(os.path.join(dist, "index.html")), app)
            assets = os.listdir(os.path.join(dist, "assets"))
            self.assertTrue(any(a.endswith(".js") for a in assets), app)


class TestF1SecurityHeaders(unittest.TestCase):
    """I 组补充:F1 安全头固化(/admin 零 JS CSP;登录页同源 CSP)。"""

    def test_i_f1_csp_admin_zero_js(self):
        """/admin* → default-src 'none' 无 script 放行;/login → 同源 CSP"""
        from tests.idp_env import IdpEnv
        env = IdpEnv()
        env.seed_admin_and_user()
        client = AsgiClient(env.app)
        login_csp = client.get("/login").headers.get(
            "content-security-policy", "")
        self.assertIn("default-src 'self'", login_csp)
        admin = client.get("/admin")           # 未登录跳转/403 也应带头?
        # 管理区任意 HTML 响应(含登录跳转页)零 JS:script-src 不出现
        if "text/html" in admin.headers.get("content-type", ""):
            admin_csp = admin.headers.get("content-security-policy", "")
            self.assertIn("default-src 'none'", admin_csp)
            self.assertNotIn("script-src", admin_csp)
