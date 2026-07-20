# -*- coding: utf-8 -*-
"""
@file    test_k_f3d_alarm.py
@brief   M6 主验收(其二):告警状态机全周期/抖动静默/再掉线新告警/删除清态/
         事件流/手动 toggle 语义/KPI/外部注入 HMAC 密钥全生命周期(L03 §6/§7)。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import hashlib
import hmac
import json
import time
import unittest

from tests.f3d_env import F3dEnv
from tests.test_k_f3d import jpost

BASE = 2000000.0     # 显式时间轴(默认告警延时 1.0 分钟)


class F3dAlarmTest(unittest.TestCase):
    """告警状态机与事件流。"""

    def setUp(self):
        """@brief 独立环境 + 取一台设备"""
        self.env = F3dEnv()
        self.ctx = self.env.ctx
        self.device_id = next(iter(self.ctx.simulator.runtime))

    def test_state_machine_offline_pending_active_ack(self):
        """offline→pending;满延时→active(事件可查);ack→acked;恢复→cleared"""
        ctx = self.ctx
        ctx.apply_status(self.device_id, "offline", "toggle", now=BASE)
        self.assertEqual(ctx.alarms.state_of(self.device_id), "pending")
        self.assertEqual(ctx.tick(now=BASE + 30), [])       # 未满 60s
        promoted = ctx.tick(now=BASE + 61)
        self.assertEqual(promoted, [self.device_id])
        self.assertEqual(ctx.alarms.state_of(self.device_id), "active")
        alarms = json.loads(self.env.client().get("/api/alarms").body)
        self.assertEqual(len(alarms["active"]), 1)
        kinds = [event["kind"] for event in json.loads(
            self.env.client().get("/api/events").body)["events"]]
        self.assertIn("alarm_active", kinds)
        user = self.env.logged_in()
        acked = jpost(user, "/api/alarms/ack", {"device_id": self.device_id})
        self.assertEqual(json.loads(acked.body)["acked"], 1)
        self.assertEqual(ctx.alarms.state_of(self.device_id), "acked")
        self.assertEqual(ctx.alarms.counts()["active"], 0)   # HUD 移出
        ctx.apply_status(self.device_id, "online", "toggle", now=BASE + 300)
        self.assertEqual(ctx.alarms.state_of(self.device_id), "")
        history = ctx.alarms.recent_history()
        self.assertEqual(history[0]["outcome"], "cleared")
        self.assertEqual(history[0]["duration_seconds"], 300)

    def test_jitter_recovery_is_silent(self):
        """pending 期恢复:抖动静默,不算正式告警(L03 §6)"""
        ctx = self.ctx
        ctx.apply_status(self.device_id, "offline", "toggle", now=BASE)
        ctx.apply_status(self.device_id, "online", "toggle", now=BASE + 20)
        self.assertEqual(ctx.alarms.state_of(self.device_id), "")
        history = ctx.alarms.recent_history()
        self.assertEqual(history[0]["outcome"], "silent")
        self.assertEqual(ctx.alarms.counts(),
                         {"active": 0, "pending": 0, "acked": 0})

    def test_reoffline_opens_new_round(self):
        """恢复清零后再次掉线 → 全新一轮告警重走流程"""
        ctx = self.ctx
        ctx.apply_status(self.device_id, "offline", "toggle", now=BASE)
        ctx.tick(now=BASE + 61)
        ctx.apply_status(self.device_id, "online", "toggle", now=BASE + 100)
        ctx.apply_status(self.device_id, "offline", "toggle", now=BASE + 200)
        self.assertEqual(ctx.alarms.state_of(self.device_id), "pending")
        ctx.tick(now=BASE + 261)
        self.assertEqual(ctx.alarms.state_of(self.device_id), "active")
        self.assertEqual(len(ctx.alarms.recent_history()), 1)

    def test_device_removed_clears_alarm_into_history(self):
        """设备删除 → 清告警态;active 记 cleared 入历史"""
        ctx = self.ctx
        ctx.apply_status(self.device_id, "offline", "toggle", now=BASE)
        ctx.tick(now=BASE + 61)
        user = self.env.logged_in()
        gone = user.request("DELETE", f"/api/data/devices/{self.device_id}")
        self.assertEqual(gone.status_code, 200)
        self.assertEqual(ctx.alarms.counts()["active"], 0)
        self.assertEqual(ctx.alarms.recent_history()[0]["outcome"], "cleared")
        self.assertNotIn(self.device_id, ctx.simulator.runtime)

    def test_event_stream_records_transitions(self):
        """事件流:from→to 状态跃迁 + layout 事件,最近 50 条"""
        ctx = self.ctx
        ctx.apply_status(self.device_id, "offline", "toggle", now=BASE)
        ctx.apply_status(self.device_id, "online", "toggle", now=BASE + 5)
        events = json.loads(self.env.client().get("/api/events").body)["events"]
        self.assertLessEqual(len(events), 50)
        latest = events[0]
        self.assertEqual((latest["from"], latest["to"]),
                         ("offline", "online"))
        self.assertEqual(latest["device"], self.device_id)
        self.assertTrue(latest["building"])

    def test_toggle_marks_manual_simulator_hands_off(self):
        """toggle 掉线标记手动:模拟器 tick 不再自动改其状态(L03 §7)"""
        user = self.env.logged_in()
        flip = user.request("POST", f"/api/devices/{self.device_id}/toggle")
        self.assertEqual(json.loads(flip.body)["to"], "offline")
        state = self.ctx.simulator.runtime[self.device_id]
        self.assertTrue(state["manual"])
        before = dict(state["metrics"])
        for _ in range(5):
            self.ctx.simulator.tick(now=time.time())
        self.assertEqual(state["status"], "offline")
        self.assertEqual(state["metrics"], before)   # 离线设备指标冻结

    def test_summary_kpi_consistency(self):
        """KPI 四枚与运行时一致;告警数=active"""
        ctx = self.ctx
        ctx.apply_status(self.device_id, "offline", "toggle", now=BASE)
        ctx.tick(now=BASE + 61)
        kpi = json.loads(self.env.client().get("/api/summary").body)["kpi"]
        self.assertEqual(kpi["total"], 23)
        self.assertEqual(kpi["offline"], 1)
        self.assertEqual(kpi["online"], 22)
        self.assertEqual(kpi["alarm"], 1)

    def test_external_hmac_key_lifecycle(self):
        """密钥明文一次/列表不回显;正签驱动状态机;坏签/时钟偏移/吊销→401"""
        user = self.env.logged_in()
        created = json.loads(jpost(user, "/api/data/external-keys", {}).body)
        key_id, secret = created["key_id"], created["secret"]
        listing = json.loads(user.get("/api/data/external-keys").body)["keys"]
        self.assertEqual(listing[0]["key_id"], key_id)
        self.assertNotIn("secret", listing[0])       # 明文仅创建时一次
        anon = self.env.client()

        def signed(ts: str, body: bytes, key: str = None) -> dict:
            digest = hmac.new((key or secret).encode(),
                              f"{ts}.".encode() + body,
                              hashlib.sha256).hexdigest()
            return {"X-GD-Key-Id": key_id, "X-GD-Timestamp": ts,
                    "X-GD-Signature": f"sha256={digest}",
                    "Content-Type": "application/json"}

        body = json.dumps({"status": "offline",
                           "metrics": {"temp_c": 41.5}}).encode()
        ts = str(time.time())
        good = anon.request("POST", f"/api/external/{self.device_id}",
                            raw_body=body, content_type="application/json",
                            headers=signed(ts, body))
        self.assertEqual(good.status_code, 200, good.body)
        self.assertEqual(self.ctx.alarms.state_of(self.device_id), "pending")
        bad_sig = anon.request("POST", f"/api/external/{self.device_id}",
                               raw_body=body, content_type="application/json",
                               headers=signed(ts, body, key="wrong-key"))
        self.assertEqual(bad_sig.status_code, 401)
        self.assertIn("鉴权失败", json.loads(bad_sig.body)["detail"])
        stale = str(time.time() - 3600)
        old_ts = anon.request("POST", f"/api/external/{self.device_id}",
                              raw_body=body, content_type="application/json",
                              headers=signed(stale, body))
        self.assertEqual(old_ts.status_code, 401)
        jpost(user, f"/api/data/external-keys/{key_id}/revoke", {})
        ts2 = str(time.time())
        revoked = anon.request("POST", f"/api/external/{self.device_id}",
                               raw_body=body, content_type="application/json",
                               headers=signed(ts2, body))
        self.assertEqual(revoked.status_code, 401)


if __name__ == "__main__":
    unittest.main()
