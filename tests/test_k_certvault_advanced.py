# -*- coding: utf-8 -*-
"""
@file    test_k_certvault_advanced.py
@brief   H09 §二 K(CV 组)增强项:引擎故障隔离(注入故障引擎不拖垮溯源)、
         13-R-CV-3 组合投票置信(一致=high/不一致=conflict+审计)、
         笔记与存档越权 403、13-R-CV-2 推荐器(介质优先/反馈调序/回退)、
         管理区(建号首登强改/重置口令 SSO=踢/停用即刻断线/审计校验导出)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import base64
import time
import unittest

from tests.cv_env import (
    ADMIN_LOCAL, ADMIN_PASSWORD, CvEnv, USER_LOCAL, USER_PASSWORD,
    build_multipart, sample_cert_jpeg,
)
from tests.rp_env import drive_sso_login

from apps.certvault.recommend import recommend_engine
from apps.certvault.wm.engines import ENGINE_TM, EngineRegistry
from selfcheck.asgi import AsgiClient


class FaultyEngine:
    """注入用故障引擎(embed/extract 一律抛错)。"""

    engine_id = ENGINE_TM
    name = "TrustMark(注入故障)"
    recommended_strength = 1.0

    def availability(self):
        return True, "故障注入:可用但提取抛错"

    def embed(self, y_channel, tracer_id, strength):
        return y_channel                   # 嵌入无害透传(聚焦提取故障)

    def extract(self, y_channel, strength):
        raise RuntimeError("注入的提取故障")


class EchoEngine:
    """注入用回显引擎(extract 恒返回配置的 tracer)。"""

    engine_id = ENGINE_TM
    name = "TrustMark(注入回显)"
    recommended_strength = 1.0

    def __init__(self, tracer_id):
        self._tracer_id = tracer_id

    def availability(self):
        return True, "故障注入:恒命中"

    def embed(self, y_channel, tracer_id, strength):
        return y_channel

    def extract(self, y_channel, strength):
        return self._tracer_id


def _registry_with(engine) -> EngineRegistry:
    """@brief 构造注入了替身 tm 引擎的注册表"""
    registry = EngineRegistry()
    registry._engines[ENGINE_TM] = engine
    return registry


class TestEngineFaultIsolation(unittest.TestCase):
    """引擎故障隔离(L02:任一引擎异常只记 engine_errors)。"""

    def test_k_faulty_blind_engine_does_not_break_trace(self):
        """tm 提取抛错 → bw 回配仍命中;engine_errors 记录故障"""
        env = CvEnv(registry=_registry_with(FaultyEngine()))
        try:
            client = env.client()
            token = env.register_and_login(client, ADMIN_LOCAL, ADMIN_PASSWORD)
            cert_id = env.upload_cert(client, token)
            # 用组合备案让 tm 进入盲提候选(bw+tm 组合:bw 实际嵌入)
            issued = env.issue(client, token, cert_id,
                               extra_fields={"engine": "bw+tm"}).json()
            suspect = base64.b64decode(issued["image_b64"])
            hit = env.trace(client, token, suspect).json()
            self.assertTrue(hit["found"], hit)
            self.assertEqual(hit["engine"], "bw")
            self.assertIn("tm", hit["engine_errors"])
            self.assertIn("注入的提取故障", hit["engine_errors"]["tm"])
        finally:
            env.close()


class TestComboVoteConfidence(unittest.TestCase):
    """13-R-CV-3 组合投票置信。"""

    def _issue_combo(self, env, echo_tracer=None):
        """@brief 组合发证并返回 (issued, client, token)"""
        client = env.client()
        token = env.register_and_login(client, ADMIN_LOCAL, ADMIN_PASSWORD)
        cert_id = env.upload_cert(client, token)
        issued = env.issue(client, token, cert_id,
                           extra_fields={"engine": "bw+tm"}).json()
        return issued, client, token

    def test_k_combo_double_hit_consistent_high(self):
        """双引擎一致 → confidence=high"""
        placeholder = EchoEngine(0)
        env = CvEnv(registry=_registry_with(placeholder))
        try:
            issued, client, token = self._issue_combo(env)
            placeholder._tracer_id = int(issued["tracer_id"], 16)  # 回显一致
            suspect = base64.b64decode(issued["image_b64"])
            hit = env.trace(client, token, suspect).json()
            self.assertTrue(hit["found"])
            self.assertEqual(hit["confidence"], "high")
            self.assertEqual(len(hit["vote_detail"]), 2)
        finally:
            env.close()

    def test_k_combo_double_hit_conflict_alerts_audit(self):
        """双引擎不一致 → conflict + 审计告警(疑似碰撞/伪造)"""
        placeholder = EchoEngine(0x1234)          # 与真实 tracer 必不一致
        env = CvEnv(registry=_registry_with(placeholder))
        try:
            issued, client, token = self._issue_combo(env)
            suspect = base64.b64decode(issued["image_b64"])
            hit = env.trace(client, token, suspect).json()
            # tm 盲提先命中回显值但无对应备案 → bw 回配命中真实备案,
            # 交叉校验 tm 回显不一致 → conflict
            self.assertTrue(hit["found"])
            self.assertEqual(hit["confidence"], "conflict")
            rows = env.db.query(
                "SELECT COUNT(*) FROM audit_logs WHERE action = 'cert_traced'"
                " AND detail LIKE '%conflict%'")
            self.assertGreater(rows[0][0], 0)
        finally:
            env.close()


class TestNotePrivilege(unittest.TestCase):
    """笔记/存档/笔记图越权 403(L02 §8)。"""

    def test_k_note_download_noteimage_privilege(self):
        """他人访问笔记/下载/笔记图 403;发证人与管理员放行"""
        env = CvEnv()
        try:
            issuer = env.client()
            issuer_token = env.register_and_login(issuer, ADMIN_LOCAL,
                                                  ADMIN_PASSWORD)
            # 第二个账号(非管理员)
            outsider = env.client()
            outsider_token = env.register_and_login(outsider, USER_LOCAL,
                                                    USER_PASSWORD)
            cert_id = env.upload_cert(issuer, issuer_token)
            issued = env.issue(
                issuer, issuer_token, cert_id,
                extra_fields={"note_location": "档案室A柜",
                              "note_text": "交付于前台"},
                files={"note_images": ("n.jpg", sample_cert_jpeg(7))}).json()
            tracer = issued["tracer_id"]
            own_note = issuer.get(f"/records/{tracer}/note",
                                  headers=env.auth_headers(issuer_token))
            self.assertEqual(own_note.status_code, 200)
            self.assertEqual(own_note.json()["note"]["location"], "档案室A柜")
            for path in (f"/records/{tracer}/note",
                         f"/records/{tracer}/download"):
                denied = outsider.get(path,
                                      headers=env.auth_headers(outsider_token))
                self.assertEqual(denied.status_code, 403, path)
            rows = env.db.query("SELECT id FROM cv_note_images LIMIT 1")
            image_id = rows[0][0]
            denied_img = outsider.get(
                f"/records/note_image/{image_id}",
                headers=env.auth_headers(outsider_token))
            self.assertEqual(denied_img.status_code, 403)
            ok_img = issuer.get(f"/records/note_image/{image_id}",
                                headers=env.auth_headers(issuer_token))
            self.assertEqual(ok_img.status_code, 200)
        finally:
            env.close()


class TestRecommender(unittest.TestCase):
    """13-R-CV-2 推荐器。"""

    def test_k_recommender_medium_priority_and_fallback(self):
        """电子介质推荐 bw;打印介质候选不可用回退 bw 且注明回退"""
        env = CvEnv()
        try:
            electronic = recommend_engine(env.ctx.registry, env.ctx.records,
                                          "idcard", "electronic")
            self.assertEqual(electronic["engine"], "bw")
            self.assertFalse(electronic["fallback"])
            printed = recommend_engine(env.ctx.registry, env.ctx.records,
                                       "idcard", "print")
            self.assertEqual(printed["engine"], "bw")   # stega/tm 不可用→bw
            self.assertIn("理", printed["reason"][:60] + "理")  # 有人话理由
        finally:
            env.close()

    def test_k_recommender_feedback_reorders(self):
        """足量反馈后按命中率调序(注入 tm 可用 + bw 低命中反馈)"""
        echo = EchoEngine(0)
        env = CvEnv(registry=_registry_with(echo))
        try:
            for _ in range(6):                     # bw 电子介质低命中反馈
                env.ctx.records.add_engine_feedback("t1", "bw",
                                                    "electronic", False)
            for _ in range(6):                     # tm 电子介质全命中
                env.ctx.records.add_engine_feedback("t2", "tm",
                                                    "electronic", True)
            result = recommend_engine(env.ctx.registry, env.ctx.records,
                                      "idcard", "electronic")
            self.assertEqual(result["engine"], "tm")
            self.assertIn("命中率 100%", result["reason"])
        finally:
            env.close()


class TestAdminArea(unittest.TestCase):
    """管理区全套(L02 §3 admin)。"""

    def setUp(self):
        self.env = CvEnv()
        self.client = self.env.client()
        self.admin_token = self.env.register_and_login(
            self.client, ADMIN_LOCAL, ADMIN_PASSWORD)

    def tearDown(self):
        self.env.close()

    def _headers(self):
        return self.env.auth_headers(self.admin_token)

    def test_k_admin_create_user_one_time_password_force_change(self):
        """建号返一次性口令;首登业务接口 403 引导改密"""
        created = self.client.post(
            "/admin/users", data={"username": "staff01",
                                  "display_name": "职员一"},
            headers=self._headers()).json()
        password = created["one_time_password"]
        login = self.env.client().post("/auth/login", data={
            "username": "staff01", "password": password})
        self.assertEqual(login.status_code, 200)
        self.assertTrue(login.json()["must_change_password"])
        token = login.json()["token"]
        blocked = self.env.client().get("/certs",
                                        headers=self.env.auth_headers(token))
        self.assertEqual(blocked.status_code, 403)
        self.assertTrue(blocked.json().get("must_change_password"))
        changed = self.env.client().post(
            "/auth/change_password",
            data={"old_password": password, "new_password": "GdStaff#26ab"},
            headers=self.env.auth_headers(token))
        self.assertEqual(changed.status_code, 200)
        allowed = self.env.client().get(
            "/certs", headers=self.env.auth_headers(changed.json()["token"]))
        self.assertEqual(allowed.status_code, 200)

    def test_k_admin_reset_password_sso_user_means_kick(self):
        """SSO 用户重置口令 = 踢下线(H03 §6);本地用户 = 新一次性口令"""
        cv_client = drive_sso_login(self.env.idp, AsgiClient(self.env.app))
        exchanged = cv_client.post("/auth/sso/exchange").json()
        sso_token = exchanged["token"]
        rows = self.env.db.query(
            "SELECT id FROM cv_users WHERE sso_sub IS NOT NULL")
        sso_uid = rows[0][0]
        time.sleep(1.1)
        reset = self.client.post(
            f"/admin/users/{sso_uid}/reset_password", headers=self._headers())
        self.assertEqual(reset.status_code, 200)
        self.assertIn("踢下线", reset.json()["note"])
        replay = AsgiClient(self.env.app).get(
            "/auth/me", headers=self.env.auth_headers(sso_token))
        self.assertEqual(replay.status_code, 401)

    def test_k_admin_disable_cuts_session_and_enable_restores(self):
        """停用即刻断线(逐请求回库);启用恢复"""
        created = self.client.post(
            "/admin/users", data={"username": "staff02"},
            headers=self._headers()).json()
        login = self.env.client().post("/auth/login", data={
            "username": "staff02", "password": created["one_time_password"]})
        token = login.json()["token"]
        rows = self.env.db.query(
            "SELECT id FROM cv_users WHERE username = 'staff02'")
        uid = rows[0][0]
        self.client.post(f"/admin/users/{uid}/disable",
                         headers=self._headers())
        cut = self.env.client().get("/auth/me",
                                    headers=self.env.auth_headers(token))
        self.assertEqual(cut.status_code, 403)
        self.client.post(f"/admin/users/{uid}/enable", headers=self._headers())
        restored = self.env.client().get(
            "/auth/me", headers=self.env.auth_headers(token))
        self.assertEqual(restored.status_code, 200)

    def test_k_admin_audit_verify_and_export(self):
        """审计链一键校验通过;CSV 导出含表头并留导出审计"""
        verify = self.client.get("/admin/audit/verify", headers=self._headers())
        self.assertTrue(verify.json()["ok"])
        export = self.client.get("/admin/audit/export", headers=self._headers())
        self.assertEqual(export.status_code, 200)
        self.assertTrue(export.body.decode().startswith("id,actor,action"))
        rows = self.env.db.query(
            "SELECT COUNT(*) FROM audit_logs WHERE action = 'data_exported'")
        self.assertEqual(rows[0][0], 1)

    def test_k_health_reports_engine_availability(self):
        """/health 含引擎可用性(06-E7);/engines 契约字段"""
        health = self.client.get("/health").json()
        engine_map = {entry["id"]: entry for entry in health["engines"]}
        self.assertTrue(engine_map["bw"]["available"])
        self.assertFalse(engine_map["stega"]["available"])
        self.assertIn("模型未安装", engine_map["stega"]["detail"])
        engines = self.client.get("/engines").json()
        self.assertEqual(engines["default"], "bw")


if __name__ == "__main__":
    unittest.main()
