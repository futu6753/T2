# -*- coding: utf-8 -*-
"""
@file    test_deep_regression.py
@brief   完工审计深度回归(跨里程碑边界):
         CV:export_width 缩放件溯源回配、三备案并存精确匹配无串扰、
         JPEG q80 二压信道命中、缩略图契约;
         NVR:offline_duration 端到端+进程重启不重置窗口、EWMA 端到端、
         滞回恢复需连续成功、并发巡检互斥;
         平台:多系统审计交织写入后链校验仍通过。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import base64
import threading
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np
import cv2

from tests.cv_env import ADMIN_LOCAL, ADMIN_PASSWORD, CvEnv
from tests.nvr_env import NvrEnv


class TestCertvaultChannelDepth(unittest.TestCase):
    """certvault 信道与并存深度。"""

    def setUp(self):
        self.env = CvEnv()
        self.client = self.env.client()
        self.token = self.env.register_and_login(self.client, ADMIN_LOCAL,
                                                 ADMIN_PASSWORD)

    def tearDown(self):
        self.env.close()

    def test_deep_export_width_scaled_trace_hits(self):
        """export_width 缩放导出(尺寸≠嵌入尺寸)→ 溯源 resize 回配命中"""
        cert_id = self.env.upload_cert(self.client, self.token)
        issued = self.env.issue(self.client, self.token, cert_id,
                                extra_fields={"export_width": "400"}).json()
        suspect = base64.b64decode(issued["image_b64"])
        image = cv2.imdecode(np.frombuffer(suspect, np.uint8),
                             cv2.IMREAD_COLOR)
        self.assertEqual(image.shape[1], 400)               # 确认已缩放
        self.assertNotEqual(image.shape[0], issued["embed_shape"][0])
        hit = self.env.trace(self.client, self.token, suspect).json()
        self.assertTrue(hit["found"], hit)
        self.assertEqual(hit["tracer_id"], issued["tracer_id"])

    def test_deep_three_records_no_cross_match(self):
        """三张证三次发证并存:各自溯源精确命中自身 tracer"""
        issued_list = []
        for seed in (11, 22, 33):
            # 大图=更高投票冗余(小图为边际信噪,生产图长边≤1600 冗余充裕)
            cert_id = self.env.upload_cert(self.client, self.token, seed=seed,
                                           size=(480, 720))
            issued_list.append(
                self.env.issue(self.client, self.token, cert_id).json())
        tracers = {entry["tracer_id"] for entry in issued_list}
        self.assertEqual(len(tracers), 3)                   # 无碰撞
        for entry in issued_list:
            suspect = base64.b64decode(entry["image_b64"])
            hit = self.env.trace(self.client, self.token, suspect).json()
            self.assertTrue(hit["found"])
            self.assertEqual(hit["tracer_id"], entry["tracer_id"])

    def test_deep_jpeg_recompress_q80_survives(self):
        """成品被二次压缩 q80(常见转发信道)后仍命中"""
        cert_id = self.env.upload_cert(self.client, self.token)
        issued = self.env.issue(self.client, self.token, cert_id).json()
        image = cv2.imdecode(
            np.frombuffer(base64.b64decode(issued["image_b64"]), np.uint8),
            cv2.IMREAD_COLOR)
        ok, recompressed = cv2.imencode(".jpg", image,
                                        [cv2.IMWRITE_JPEG_QUALITY, 80])
        hit = self.env.trace(self.client, self.token,
                             recompressed.tobytes()).json()
        self.assertTrue(hit["found"], hit)
        self.assertEqual(hit["tracer_id"], issued["tracer_id"])

    def test_deep_thumbnail_contract(self):
        """列表缩略图存在且宽 ≤240(02-B1)"""
        self.env.upload_cert(self.client, self.token)
        listing = self.client.get(
            "/certs", headers=self.env.auth_headers(self.token)).json()
        thumb_b64 = listing["certs"][0]["thumb_b64"]
        thumb = cv2.imdecode(
            np.frombuffer(base64.b64decode(thumb_b64), np.uint8),
            cv2.IMREAD_COLOR)
        self.assertLessEqual(thumb.shape[1], 240)


class TestNvrDebounceDepth(unittest.TestCase):
    """去抖模式端到端深度(HTTP 级真实驱动)。"""

    def test_deep_offline_duration_survives_restart(self):
        """duration 模式:窗口起点自时间线;重建引擎(重启)不重置"""
        env = NvrEnv(debounce_mode="offline_duration",
                     offline_duration_seconds=300)
        try:
            admin = env.login("admin")
            device = env.create_device(admin, "NVR-A", "10.0.0.1")
            env.fleet.set("10.0.0.1", "offline")
            admin.post("/api/patrol/run")
            self.assertEqual(
                admin.get("/api/alerts?state=firing").json()["active_total"],
                0)                                        # 未达 300s 不触发
            # 把 since 回拨 301 秒(模拟持续故障跨越阈值)
            past = (datetime.now(timezone.utc)
                    - timedelta(seconds=301)).isoformat()
            env.db.execute(
                "UPDATE nvr_device_state SET since = ? WHERE device_id = ?",
                (past, device["id"]))
            # "进程重启":重建 AlertEngine(快照驱动,无内部态)
            from apps.nvr.alerts import AlertEngine
            from apps.nvr.debounce import DebouncePolicy
            rebuilt = AlertEngine(
                env.db, env.ctx.devices,
                DebouncePolicy("offline_duration",
                               offline_duration_seconds=300))
            env.ctx.patrol._alerts = rebuilt
            admin.post("/api/patrol/run")                 # 重启后首轮即触发
            self.assertEqual(
                len(rebuilt.list_alerts(state="firing")), 1)
        finally:
            env.close()

    def test_deep_ewma_mode_end_to_end(self):
        """EWMA 模式:单次故障不触发;连续故障爬升触发;恢复即解"""
        env = NvrEnv(debounce_mode="ewma")
        try:
            admin = env.login("admin")
            env.create_device(admin, "NVR-A", "10.0.0.1")
            env.fleet.set("10.0.0.1", "offline")
            admin.post("/api/patrol/run")                 # ewma=0.4 < 0.75
            self.assertEqual(
                admin.get("/api/alerts?state=firing").json()["active_total"],
                0)
            admin.post("/api/patrol/run")                 # 0.64
            admin.post("/api/patrol/run")                 # 0.784 ≥ 0.75 触发
            self.assertEqual(
                admin.get("/api/alerts?state=firing").json()["active_total"],
                1)
            env.fleet.set("10.0.0.1", "online")
            admin.post("/api/patrol/run")
            self.assertEqual(
                admin.get("/api/alerts?state=firing").json()["active_total"],
                0)
        finally:
            env.close()

    def test_deep_hysteresis_resolve_needs_two_ok(self):
        """滞回:触发 3 连败;恢复首次成功不解除,连续 2 次成功才解除"""
        env = NvrEnv(debounce_mode="hysteresis")
        try:
            admin = env.login("admin")
            env.create_device(admin, "NVR-A", "10.0.0.1")
            env.fleet.set("10.0.0.1", "offline")
            for _ in range(3):
                admin.post("/api/patrol/run")
            self.assertEqual(
                admin.get("/api/alerts?state=firing").json()["active_total"],
                1)
            env.fleet.set("10.0.0.1", "online")
            admin.post("/api/patrol/run")                 # 首次成功:保持
            self.assertEqual(
                admin.get("/api/alerts?state=firing").json()["active_total"],
                1)
            admin.post("/api/patrol/run")                 # 连续第 2 次:解除
            self.assertEqual(
                admin.get("/api/alerts?state=firing").json()["active_total"],
                0)
        finally:
            env.close()

    def test_deep_concurrent_patrol_mutex(self):
        """并发两轮巡检:确定性重叠下一轮执行、另一轮 conflict(互斥契约)"""
        env = NvrEnv()
        try:
            admin = env.login("admin")
            env.create_device(admin, "NVR-0", "10.0.0.10")
            started = threading.Event()
            release = threading.Event()

            def slow_factory(device, password):
                def check():
                    started.set()                 # 通知:首轮已进入执行
                    release.wait(timeout=10)      # 挂起直至第二轮已判定
                    return {"status": "online", "detail": "ok",
                            "latency_ms": 1, "offline_channels": []}
                return check
            env.ctx.patrol._checker_factory = slow_factory
            results = {}

            def first_run():
                results["first"] = env.ctx.patrol.run_cycle()
            worker = threading.Thread(target=first_run)
            worker.start()
            self.assertTrue(started.wait(timeout=10))   # 首轮确已持锁
            second = env.ctx.patrol.run_cycle()         # 重叠期发起第二轮
            self.assertTrue(second.get("conflict"), second)
            release.set()
            worker.join(timeout=15)
            self.assertEqual(results["first"]["checked"], 1)
            self.assertNotIn("conflict", results["first"])
        finally:
            release.set()
            env.close()


class TestAuditChainInterleaved(unittest.TestCase):
    """多系统审计交织写入后链校验(02-B4/H04 §三)。"""

    def test_deep_interleaved_audit_chain_verifies(self):
        """cv 业务审计与 nvr 风格事件交织 20 条,verify_chain 全量通过"""
        from gd_storage.audit import AuditWriter, verify_chain
        env = CvEnv()
        try:
            client = env.client()
            token = env.register_and_login(client, ADMIN_LOCAL,
                                           ADMIN_PASSWORD)
            other_writer = AuditWriter(env.db, env.ctx.suite)
            for round_no in range(8):
                other_writer.append("nvr-system", "settings_changed",
                                    {"system": "nvr", "round": round_no},
                                    "10.0.0.9")
                env.ctx.audit.append("cv-system", "cert_traced",
                                     {"system": "certvault",
                                      "round": round_no}, "10.0.0.8")
            count = verify_chain(env.db)
            self.assertGreaterEqual(count, 16)
            # 保护层一:在线 UPDATE 被防篡改触发器直接拒绝
            with self.assertRaises(Exception):
                env.db.execute(
                    "UPDATE audit_logs SET detail = '{\"forged\":1}'"
                    " WHERE id = (SELECT MAX(id) - 3 FROM audit_logs)")
            # 保护层二:模拟攻击者离线拿到 DB(先卸触发器再篡改)
            # → 链式哈希校验必失败(不可抵赖兜底)
            env.db.execute("DROP TRIGGER IF EXISTS trg_audit_no_update")
            env.db.execute(
                "UPDATE audit_logs SET detail = '{\"forged\":1}'"
                " WHERE id = (SELECT MAX(id) - 3 FROM audit_logs)")
            with self.assertRaises(Exception):
                verify_chain(env.db)
        finally:
            env.close()


if __name__ == "__main__":
    unittest.main()
