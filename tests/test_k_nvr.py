# -*- coding: utf-8 -*-
"""
@file    test_k_nvr.py
@brief   H09 §二 K(NVR 组):判定树五分支契约、状态机+时间线、
         13-R-NVR-1 去抖五模式、告警生命周期(重复抑制/恢复即解带时长/
         通道 scope 并存/unknown 不误触发)、巡检互斥与单台隔离、
         推送三格式+宽限+恢复即解、API 契约与 RBAC。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import json
import unittest

from tests.nvr_env import NvrEnv

from apps.nvr.checker import DeviceChecker, IsapiTimeout
from apps.nvr.debounce import (
    ALL_MODES, DebouncePolicy, MODE_ADAPTIVE, MODE_CONSECUTIVE,
    MODE_DURATION, MODE_EWMA, MODE_HYSTERESIS, next_ewma,
)


class TestCheckerContract(unittest.TestCase):
    """L04 §3 判定树契约(五分支+通道连带开关)。"""

    def test_k_decision_tree_five_branches(self):
        """200=online;401=auth_failed;超时+TCP=timeout;
        TCP不通+ping=abnormal;全不通=offline"""
        online = DeviceChecker(lambda *a: (200, [])).check("h", 80, "u", "p")
        auth = DeviceChecker(lambda *a: (401, [])).check("h", 80, "u", "p")

        def timeout_probe(*args):
            raise IsapiTimeout()
        timeout = DeviceChecker(timeout_probe,
                                tcp_probe=lambda h, p, t: True) \
            .check("h", 80, "u", "p")
        icmp = DeviceChecker(timeout_probe, tcp_probe=lambda h, p, t: False,
                             ping_probe=lambda h, t: True) \
            .check("h", 80, "u", "p")
        offline = DeviceChecker(timeout_probe,
                                tcp_probe=lambda h, p, t: False,
                                ping_probe=lambda h, t: False) \
            .check("h", 80, "u", "p")
        self.assertEqual(
            [online["status"], auth["status"], timeout["status"],
             icmp["status"], offline["status"]],
            ["online", "auth_failed", "timeout", "abnormal", "offline"])
        for outcome in (online, auth, timeout, icmp, offline):
            self.assertNotIn("p", outcome["detail"].split())  # 凭据不出输出

    def test_k_channel_offline_abnormal_switch(self):
        """通道离线连带异常仅在双开关开启时生效(T13 默认关)"""
        default_off = DeviceChecker(lambda *a: (200, [3])) \
            .check("h", 80, "u", "p")
        self.assertEqual(default_off["status"], "online")
        switched_on = DeviceChecker(lambda *a: (200, [3]),
                                    channel_offline_abnormal=True) \
            .check("h", 80, "u", "p")
        self.assertEqual(switched_on["status"], "abnormal")
        self.assertIn("离线录像通道", switched_on["detail"])


class TestDebounceFamily(unittest.TestCase):
    """13-R-NVR-1 去抖五模式判定语义。"""

    def test_k_all_modes_fire_and_resolve_semantics(self):
        """五模式触发条件与恢复条件"""
        base = {"status": "offline", "offline_seconds": 0, "ewma": 0,
                "consecutive_ok": 0, "flap_rate": 0}
        consecutive = DebouncePolicy(MODE_CONSECUTIVE, consecutive_failures=3)
        self.assertFalse(consecutive.should_fire(
            {**base, "consecutive_fails": 2}))
        self.assertTrue(consecutive.should_fire(
            {**base, "consecutive_fails": 3}))
        duration = DebouncePolicy(MODE_DURATION, offline_duration_seconds=300)
        self.assertFalse(duration.should_fire(
            {**base, "consecutive_fails": 9, "offline_seconds": 299}))
        self.assertTrue(duration.should_fire(
            {**base, "consecutive_fails": 1, "offline_seconds": 300}))
        ewma_policy = DebouncePolicy(MODE_EWMA)
        score = 0.0
        for _ in range(3):
            score = next_ewma(score, True)
        self.assertFalse(ewma_policy.should_fire(
            {**base, "ewma": next_ewma(0, True)}))       # 单次故障不触发
        self.assertTrue(ewma_policy.should_fire({**base, "ewma": score}))
        hysteresis = DebouncePolicy(MODE_HYSTERESIS)
        self.assertTrue(hysteresis.should_fire(
            {**base, "consecutive_fails": 3}))
        self.assertFalse(hysteresis.should_resolve(
            {"status": "online", "consecutive_ok": 1}))  # 恢复需连续 2 次
        self.assertTrue(hysteresis.should_resolve(
            {"status": "online", "consecutive_ok": 2}))
        adaptive = DebouncePolicy(MODE_ADAPTIVE)
        self.assertTrue(adaptive.should_fire(
            {**base, "consecutive_fails": 3, "flap_rate": 0}))
        self.assertFalse(adaptive.should_fire(
            {**base, "consecutive_fails": 3, "flap_rate": 0.8}))  # 提阈
        self.assertEqual(len(ALL_MODES), 5)


class TestAlertLifecycleHttp(unittest.TestCase):
    """告警生命周期(HTTP 级,consecutive=2 加速)。"""

    def setUp(self):
        self.env = NvrEnv(consecutive_failures=2)
        self.admin = self.env.login("admin")
        self.device = self.env.create_device(self.admin, "NVR-A", "10.0.0.1",
                                             region="东渡", station="泊位1")

    def tearDown(self):
        self.env.close()

    def test_k_alert_fire_suppress_resolve_with_duration(self):
        """连续2次触发;子状态切换不重复告警;恢复即解带时长"""
        self.env.fleet.set("10.0.0.1", "offline")
        self.admin.post("/api/patrol/run")
        self.admin.post("/api/patrol/run")
        alerts = self.admin.get("/api/alerts?state=firing").json()
        self.assertEqual(alerts["active_total"], 1)
        self.assertEqual(alerts["alerts"][0]["trigger_status"], "offline")
        self.env.fleet.set("10.0.0.1", "timeout_tcp")   # 子状态切换
        self.admin.post("/api/patrol/run")
        still = self.admin.get("/api/alerts?state=firing").json()
        self.assertEqual(still["active_total"], 1)      # 不重复告警
        self.assertEqual(still["alerts"][0]["id"], alerts["alerts"][0]["id"])
        self.env.fleet.set("10.0.0.1", "online")
        self.admin.post("/api/patrol/run")
        resolved = self.admin.get("/api/alerts?state=resolved").json()
        self.assertEqual(len(resolved["alerts"]), 1)
        self.assertIsNotNone(resolved["alerts"][0]["duration_seconds"])
        self.assertEqual(
            self.admin.get("/api/alerts?state=firing").json()["active_total"],
            0)

    def test_k_channel_alert_coexists_and_unknown_no_false_fire(self):
        """通道告警与本体并存;NVR 不可达时通道 unknown 不误触发"""
        self.env.fleet.set("10.0.0.1", ("channels", [2, 5]))
        self.admin.post("/api/patrol/run")
        firing = self.admin.get("/api/alerts?state=firing&scope=channel") \
            .json()["alerts"]
        self.assertEqual(len(firing), 1)
        self.assertIn("通道2", firing[0]["detail"])
        channels = self.admin.get(
            f"/api/devices/{self.device['id']}/channels").json()
        self.assertEqual(channels["summary"].get("offline"), 2)
        # 本体离线两轮:设备线触发,但通道线不新增(unknown)
        self.env.fleet.set("10.0.0.1", "offline")
        self.admin.post("/api/patrol/run")
        self.admin.post("/api/patrol/run")
        by_scope = self.admin.get("/api/status/overview").json()[
            "alerts_by_scope"]
        self.assertEqual(by_scope.get("device"), 1)
        self.assertEqual(by_scope.get("channel"), 1)    # 保持,不误增
        unknown = self.admin.get(
            f"/api/devices/{self.device['id']}/channels").json()
        self.assertEqual(unknown["summary"].get("unknown"), 2)

    def test_k_manual_check_drives_state_machine(self):
        """手动检测 source=manual 同样入状态机与告警(现场复检即解)"""
        self.env.fleet.set("10.0.0.1", "offline")
        for _ in range(2):
            self.admin.post(f"/api/devices/{self.device['id']}/check")
        self.assertEqual(
            self.admin.get("/api/alerts?state=firing").json()["active_total"],
            1)
        results = self.admin.get(
            f"/api/devices/{self.device['id']}/results?source=manual").json()
        self.assertEqual(len(results["results"]), 2)
        self.env.fleet.set("10.0.0.1", "online")
        self.admin.post(f"/api/devices/{self.device['id']}/check")
        self.assertEqual(
            self.admin.get("/api/alerts?state=firing").json()["active_total"],
            0)

    def test_k_timeline_and_changes_stream(self):
        """状态跃迁入时间线;/api/status/changes 全局流"""
        self.env.fleet.set("10.0.0.1", "offline")
        self.admin.post("/api/patrol/run")
        self.env.fleet.set("10.0.0.1", "online")
        self.admin.post("/api/patrol/run")
        timeline = self.admin.get(
            f"/api/devices/{self.device['id']}/timeline").json()["timeline"]
        transitions = [(entry["from_status"], entry["to_status"])
                       for entry in timeline
                       if entry["event_type"] == "status_change"]
        self.assertIn(("offline", "online"), transitions)
        changes = self.admin.get("/api/status/changes").json()["changes"]
        self.assertGreaterEqual(len(changes), 2)


class TestPatrolSemantics(unittest.TestCase):
    """巡检互斥/单台隔离/密钥缺失。"""

    def test_k_single_device_decrypt_failure_isolated(self):
        """单台密文损坏=该台 abnormal+原因,其余不受影响"""
        env = NvrEnv()
        try:
            admin = env.login("admin")
            good = env.create_device(admin, "NVR-好", "10.0.0.1")
            bad = env.create_device(admin, "NVR-坏", "10.0.0.2")
            env.db.execute(
                "UPDATE nvr_devices SET password_ct = 'not-a-envelope'"
                " WHERE id = ?", (bad["id"],))
            env.fleet.set("10.0.0.1", "online")
            result = admin.post("/api/patrol/run").json()
            self.assertEqual(result["by_status"].get("online"), 1)
            self.assertEqual(result["by_status"].get("abnormal"), 1)
            self.assertIn("NVR-坏", result["errors"])
            state = env.ctx.devices.state_of(bad["id"])
            self.assertIn("解密失败", state["last_detail"])
        finally:
            env.close()

    def test_k_master_key_missing_skips_cycle(self):
        """主密钥缺失 → 整轮拒绝执行"""
        env = NvrEnv(master_key_ready=lambda: False)
        try:
            admin = env.login("admin")
            resp = admin.post("/api/patrol/run")
            self.assertEqual(resp.status_code, 400)
            self.assertIn("主密钥缺失", resp.json()["error"])
        finally:
            env.close()


class TestPushIngestHttp(unittest.TestCase):
    """推送设备接入(HTTP 契约)。"""

    def setUp(self):
        self.env = NvrEnv(consecutive_failures=2)
        self.admin = self.env.login("admin")
        self.push = self.env.create_device(self.admin, "光伏箱", "",
                                           kind="push",
                                           push_grace_seconds=60)

    def tearDown(self):
        self.env.close()

    def test_k_ingest_contract_and_heartbeat_dedup(self):
        """应答四字段;重复 online 心跳不落明细(防灌爆)"""
        token = self.push["push_token"]
        first = self.admin.get(f"/ingest/{token}").json()
        for key in ("ok", "device_id", "status", "received_at"):
            self.assertIn(key, first)
        for _ in range(5):
            self.admin.get(f"/api/ingest/{token}")     # 别名路径心跳
        rows = self.env.db.query(
            "SELECT COUNT(*) FROM nvr_check_results WHERE device_id = ?"
            " AND source = 'push'", (self.push["id"],))
        self.assertEqual(rows[0][0], 1)                # 仅首次变化落库
        invalid = self.admin.get("/ingest/badtoken")
        self.assertEqual(invalid.status_code, 404)

    def test_k_push_offline_debounce_and_instant_recovery(self):
        """离线上报走去抖;恢复推送当场解除"""
        token = self.push["push_token"]
        body = json.dumps({"status": "offline", "detail": "断电"}).encode()
        for _ in range(2):
            self.admin.request("POST", f"/ingest/{token}", raw_body=body,
                               content_type="application/json")
        self.assertEqual(
            self.admin.get("/api/alerts?state=firing").json()["active_total"],
            1)
        self.admin.get(f"/ingest/{token}")             # 心跳=online 恢复
        self.assertEqual(
            self.admin.get("/api/alerts?state=firing").json()["active_total"],
            0)

    def test_k_first_contact_grace(self):
        """新建从未上报:overdue_check 首联宽限保持未检测"""
        flagged = self.env.ctx.ingest.overdue_check()
        self.assertEqual(flagged, [])
        state = self.env.ctx.devices.state_of(self.push["id"])
        self.assertEqual(state["status"], "unchecked")


class TestRbacAndContract(unittest.TestCase):
    """RBAC 与 API 响应契约。"""

    def test_k_rbac_matrix(self):
        """auditor 只读;operator 可检测不可删;admin 全权"""
        env = NvrEnv()
        try:
            admin = env.login("admin")
            device = env.create_device(admin, "NVR-A", "10.0.0.1")
            auditor = env.login("auditor")
            self.assertEqual(auditor.get("/api/devices").status_code, 200)
            denied = auditor.request(
                "POST", "/api/devices",
                raw_body=b'{"name":"x"}', content_type="application/json")
            self.assertEqual(denied.status_code, 403)
            operator = env.login("operator")
            self.assertEqual(
                operator.post(
                    f"/api/devices/{device['id']}/check").status_code, 200)
            self.assertEqual(
                operator.request(
                    "DELETE",
                    f"/api/devices/{device['id']}").status_code, 403)
            # 同一 SSO 账号切回 admin(逐请求回库角色即刻生效,H03 §3)
            env.ctx.accounts.set_role("alice", "admin")
            self.assertEqual(
                admin.request(
                    "DELETE",
                    f"/api/devices/{device['id']}").status_code, 200)
            self.assertEqual(env.ctx.devices.get(device["id"]), None)
        finally:
            env.close()

    def test_k_device_password_never_returned_and_rotation(self):
        """任何接口不返回密码;PUT password=轮换"""
        env = NvrEnv()
        try:
            admin = env.login("admin")
            device = env.create_device(admin, "NVR-A", "10.0.0.1")
            listing = admin.get("/api/devices").json()["devices"][0]
            self.assertNotIn("password", listing)
            self.assertNotIn("password_ct", listing)
            old = env.ctx.devices.open_password(device["id"])
            admin.request("PUT", f"/api/devices/{device['id']}",
                          raw_body=json.dumps(
                              {"password": "newpwd!"}).encode(),
                          content_type="application/json")
            self.assertNotEqual(env.ctx.devices.open_password(device["id"]),
                                old)
            self.assertEqual(env.ctx.devices.open_password(device["id"]),
                             "newpwd!")
        finally:
            env.close()


if __name__ == "__main__":
    unittest.main()
