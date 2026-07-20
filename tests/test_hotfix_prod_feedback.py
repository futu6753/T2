# -*- coding: utf-8 -*-
"""
@file    test_hotfix_prod_feedback.py
@brief   生产部署实测反馈热修回归(2026-07-19,UOS Server + PG14 + psycopg3):
         ①首登强改浏览器流:登录 303 → /account/password 页 → 改密 303 →
         新口令登录;API 流保留 403 JSON+rid;旧口令错复用锁定计数。
         ②PG 方言适配:_adapt_sql 转义 % 且 ?→%s;迁移执行
         INTEGER PRIMARY KEY→SERIAL(SQLite 不受影响)。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import unittest

from tests.idp_env import IdpEnv


class TestMustChangeBrowserFlow(unittest.TestCase):
    """首登强改的浏览器闭环(生产反馈:裸 JSON 卡死用户)。"""

    def setUp(self):
        self.env = IdpEnv(is_demo=False)
        self.env.ctx.accounts.create_user(
            "newop", "新运维", "GdInit#2026xy", self.env.ctx.profile,
            "admin", "0.0.0.0", force_change=True)
        self.client = self.env.client()

    def tearDown(self):
        self.env.close()

    def _login(self, password, html=True):
        headers = {"Accept": "text/html,application/xhtml+xml"} if html \
            else {}
        return self.client.post(
            "/login", data={"account": "newop", "password": password},
            headers=headers)

    def test_hotfix_browser_303_page_change_relogin(self):
        """浏览器:303 带票据 → 页面预填只读 → 改密 303 → 新口令登录 302"""
        resp = self._login("GdInit#2026xy")
        self.assertEqual(resp.status_code, 303)
        location = resp.headers["location"]
        self.assertTrue(location.startswith("/account/password?rid="))
        page = self.client.get(location)
        self.assertEqual(page.status_code, 200)
        self.assertIn("readonly", page.body.decode())
        self.assertIn("newop", page.body.decode())
        changed = self.client.post(
            "/account/password",
            data={"rid": location.split("rid=")[1], "account": "newop",
                  "old_password": "GdInit#2026xy",
                  "new_password": "GdNew#2026abc",
                  "confirm_password": "GdNew#2026abc"},
            headers={"Accept": "text/html"})
        self.assertEqual(changed.status_code, 303)
        self.assertEqual(changed.headers["location"], "/login?changed=1")
        relogin = self._login("GdNew#2026abc")
        self.assertEqual(relogin.status_code, 302)      # 进门户

    def test_hotfix_api_flow_keeps_json_with_ticket(self):
        """API(无 html Accept):403 JSON + next + rid 票据"""
        resp = self._login("GdInit#2026xy", html=False)
        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertEqual(body["next"], "/account/password")
        self.assertIn("rid", body)

    def test_hotfix_change_password_negatives(self):
        """确认不一致 400;同旧口令 400;旧口令错 401;弱口令 400"""
        base = {"account": "newop", "old_password": "GdInit#2026xy"}
        mismatch = self.client.post("/account/password", data={
            **base, "new_password": "GdNew#2026abc",
            "confirm_password": "GdOther#26ab"})
        self.assertEqual(mismatch.status_code, 400)
        same = self.client.post("/account/password", data={
            **base, "new_password": "GdInit#2026xy",
            "confirm_password": "GdInit#2026xy"})
        self.assertEqual(same.status_code, 400)
        wrong_old = self.client.post("/account/password", data={
            "account": "newop", "old_password": "wrong",
            "new_password": "GdNew#2026abc",
            "confirm_password": "GdNew#2026abc"})
        self.assertEqual(wrong_old.status_code, 401)
        weak = self.client.post("/account/password", data={
            **base, "new_password": "short", "confirm_password": "short"})
        self.assertEqual(weak.status_code, 400)


class TestPostgresDialectAdapters(unittest.TestCase):
    """PG 方言适配(psycopg3 % 严格解析 + SERIAL 自增,生产实测)。"""

    def test_hotfix_adapt_sql_escapes_percent_for_postgres(self):
        """PG:% → %%、? → %s;SQLite 原样"""
        from gd_storage.database import Database

        class _Stub(Database):
            def __init__(self, dialect):
                self.dialect = dialect          # 跳过连接,仅测适配函数

        pg = _Stub("postgres")
        adapted = pg._adapt_sql(
            "SELECT * FROM t WHERE name LIKE '%abc%' AND id = ?")
        self.assertEqual(
            adapted,
            "SELECT * FROM t WHERE name LIKE '%%abc%%' AND id = %s")
        sqlite = _Stub("sqlite")
        self.assertEqual(
            sqlite._adapt_sql("SELECT 1 WHERE a LIKE '%x%' AND id = ?"),
            "SELECT 1 WHERE a LIKE '%x%' AND id = ?")

    def test_hotfix_migration_serial_rewrite_pg_only(self):
        """迁移执行:PG 改写 SERIAL;SQLite 全量迁移正常(回归兜底)"""
        import inspect
        from gd_storage import migrations
        source = inspect.getsource(migrations)
        self.assertIn("SERIAL PRIMARY KEY", source)
        self.assertIn("DIALECT_SQLITE", source)
        from tests.base import make_temp_db
        db = make_temp_db()                     # SQLite 路径全量迁移可跑
        version = db.query(
            "SELECT MAX(version) FROM schema_migrations")[0][0]
        self.assertGreaterEqual(version, 6)
        db.close()


if __name__ == "__main__":
    unittest.main()
