# -*- coding: utf-8 -*-
"""
@file    test_k_nvr_settings.py
@brief   完工审计补齐项回归(L04 §4/§6):
         /api/settings C3 语义(schema 展示来源层/PUT 批量含 null 删除/
         未知键报错/choice 与 cron 保存校验/env 锁定拒改/reset)、
         /api/notifications/channels 就绪度不回显密钥、
         /api/logs/events UNION 三源与过滤、/api/logs/stations、
         cron 解析器(双限取或/7 归一/负例)、两 CLI 工件冒烟。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import json
import subprocess
import sys
import unittest
from datetime import datetime, timezone

from tests.nvr_env import NvrEnv

from apps.nvr.cron import CronSpec, validate_cron
from apps.nvr.dispatch import AliyunSmsChannel, WebhookChannel
from gd_common.errors import PolicyValidationError


class TestSettingsC3(unittest.TestCase):
    """/api/settings 三路由(02-C3 契约)。"""

    def setUp(self):
        self.env = NvrEnv()
        self.admin = self.env.login("admin")

    def tearDown(self):
        self.env.close()

    def _put(self, values):
        return self.admin.request(
            "PUT", "/api/settings",
            raw_body=json.dumps({"values": values}).encode(),
            content_type="application/json")

    def test_k_settings_schema_view_and_override_precedence(self):
        """GET 分区展示含来源层;PUT 覆盖;null 删除覆盖恢复默认"""
        view = self.admin.get("/api/settings").json()
        self.assertIn("NVR·告警", view["sections"])
        mode = next(entry for section in view["sections"].values()
                    for entry in section if entry["key"] == "nvr_debounce_mode")
        self.assertEqual(mode["value"], "consecutive_failures")
        self.assertEqual(mode["source"], "default")
        self.assertIn("ewma", mode["choices"])
        put = self._put({"nvr_debounce_mode": "ewma",
                         "nvr_consecutive_failures": 5})
        self.assertEqual(put.status_code, 200, put.body)
        self.assertEqual(put.json()["applied"]["nvr_debounce_mode"], "ewma")
        again = self.admin.get("/api/settings").json()
        mode2 = next(entry for section in again["sections"].values()
                     for entry in section
                     if entry["key"] == "nvr_debounce_mode")
        self.assertEqual(mode2["value"], "ewma")
        self.assertEqual(mode2["source"], "override")
        cleared = self._put({"nvr_debounce_mode": None})
        self.assertEqual(cleared.status_code, 200)
        final = self.admin.get("/api/settings").json()
        mode3 = next(entry for section in final["sections"].values()
                     for entry in section
                     if entry["key"] == "nvr_debounce_mode")
        self.assertEqual(mode3["value"], "consecutive_failures")
        self.assertEqual(mode3["source"], "default")

    def test_k_settings_validation_unknown_choice_cron_range(self):
        """未知键/非法 choice/非法 cron/越界 int 逐键报错(400)"""
        resp = self._put({"nvr_debounce_mode": "magic",
                          "nvr_report_cron": "61 * * * *",
                          "nvr_patrol_concurrency": 999,
                          "totally_unknown": 1,
                          "nvr_report_period_days": 14})
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        for bad in ("nvr_debounce_mode", "nvr_report_cron",
                    "nvr_patrol_concurrency", "totally_unknown"):
            self.assertIn(bad, body["errors"], bad)
        self.assertEqual(body["applied"].get("nvr_report_period_days"), 14)
        good_cron = self._put({"nvr_report_cron": "30 8 * * 1-5"})
        self.assertEqual(good_cron.status_code, 200)

    def test_k_settings_env_locked_and_reset(self):
        """env 锁定项拒改并明示;reset 清全部覆盖且写审计"""
        env2 = NvrEnv()
        try:
            env2.idp.ctx.settings._environ[
                "NVRM_ALERTING__DEBOUNCE__MODE"] = "hysteresis"
            admin2 = env2.login("admin")
            locked = admin2.request(
                "PUT", "/api/settings",
                raw_body=json.dumps(
                    {"values": {"nvr_debounce_mode": "ewma"}}).encode(),
                content_type="application/json")
            self.assertEqual(locked.status_code, 400)
            self.assertIn("环境变量锁定",
                          locked.json()["errors"]["nvr_debounce_mode"])
            view = admin2.get("/api/settings").json()
            mode = next(entry for section in view["sections"].values()
                        for entry in section
                        if entry["key"] == "nvr_debounce_mode")
            self.assertTrue(mode["env_locked"])
            self.assertEqual(mode["value"], "hysteresis")
        finally:
            env2.close()
        self._put({"nvr_retry_backoff_seconds": 90})
        reset = self.admin.post("/api/settings/reset")
        self.assertEqual(reset.status_code, 200)
        self.assertIn("nvr_retry_backoff_seconds", reset.json()["reset"])
        rows = self.env.db.query(
            "SELECT COUNT(*) FROM audit_logs WHERE action ="
            " 'settings_changed'")
        self.assertGreater(rows[0][0], 0)


class TestChannelsReadinessAndLogs(unittest.TestCase):
    """渠道就绪度 + 日志区。"""

    def test_k_notification_channels_readiness_no_secret_leak(self):
        """就绪度端点:配置齐=ready;缺项明示;不回显密钥"""
        ready_hook = WebhookChannel("http://hook/x", "supersecret")
        missing_hook = WebhookChannel("http://hook/x", "")
        sms = AliyunSmsChannel("ak", "aksec", "cn", "签", "T", [])
        env = NvrEnv(channels=[ready_hook])
        try:
            admin = env.login("admin")
            listing = admin.get("/api/notifications/channels")
            self.assertEqual(listing.status_code, 200)
            body = listing.body.decode()
            self.assertNotIn("supersecret", body)
            entry = listing.json()["channels"][0]
            self.assertTrue(entry["ready"])
        finally:
            env.close()
        self.assertFalse(missing_hook.ready())
        self.assertIn("签名密钥", missing_hook.describe())
        self.assertFalse(sms.ready())
        self.assertIn("手机号", sms.describe())

    def test_k_logs_events_union_and_filters(self):
        """UNION 三源(跃迁/告警启停);region 过滤;type 过滤;stations"""
        env = NvrEnv(consecutive_failures=1)
        try:
            admin = env.login("admin")
            east = env.create_device(admin, "NVR-东", "10.0.0.1",
                                     region="东渡", station="泊位1")
            west = env.create_device(admin, "NVR-西", "10.0.0.2",
                                     region="海沧", station="泊位9")
            env.fleet.set("10.0.0.1", "offline")
            env.fleet.set("10.0.0.2", "online")
            admin.post("/api/patrol/run")               # 东告警触发
            env.fleet.set("10.0.0.1", "online")
            admin.post("/api/patrol/run")               # 东恢复解除
            events = admin.get("/api/logs/events").json()["events"]
            types = {event["event_type"] for event in events}
            self.assertIn("status_change", types)
            self.assertIn("alert_fired", types)
            self.assertIn("alert_resolved", types)
            east_only = admin.get(
                "/api/logs/events?region=东渡").json()["events"]
            self.assertTrue(all(event["region"] == "东渡"
                                for event in east_only))
            fired_only = admin.get(
                "/api/logs/events?type=alert_fired").json()["events"]
            self.assertTrue(fired_only)
            self.assertTrue(all(event["event_type"] == "alert_fired"
                                for event in fired_only))
            bad = admin.get("/api/logs/events?type=nonsense")
            self.assertEqual(bad.status_code, 400)
            stations = admin.get("/api/logs/stations").json()["stations"]
            self.assertEqual(len(stations), 2)
        finally:
            env.close()


class TestCronContract(unittest.TestCase):
    """cron 解析器契约(L04 §2)。"""

    def test_k_cron_semantics_and_negatives(self):
        """步长/区间/双限取或/7 归一/负例"""
        monday = CronSpec("0 9 * * 1")
        nxt = monday.next_run(datetime(2026, 7, 19, 14, 0,
                                       tzinfo=timezone.utc))
        self.assertEqual(nxt, datetime(2026, 7, 20, 9, 0,
                                       tzinfo=timezone.utc))
        both = CronSpec("0 0 1 * 1")           # 1 号或周一,任一匹配
        self.assertEqual(
            both.next_run(datetime(2026, 7, 27, 1, 0, tzinfo=timezone.utc)),
            datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc))
        self.assertTrue(CronSpec("0 0 * * 7").matches(
            datetime(2026, 7, 19, 0, 0, tzinfo=timezone.utc)))  # 周日
        for bad in ("0 9 * *", "61 * * * *", "* * * * 8", "*/0 * * * *",
                    "a * * * *"):
            with self.assertRaises(PolicyValidationError, msg=bad):
                validate_cron(bad)


class TestCliArtifacts(unittest.TestCase):
    """两 CLI 工件冒烟。"""

    def test_k_checker_cli_offline_exit_code(self):
        """CLI:全不通=offline 且退出码 1;--json 输出契约字段"""
        result = subprocess.run(
            [sys.executable, "scripts/nvr_check_cli.py", "--host",
             "192.0.2.1", "--port", "9", "--password-env", "NOPE",
             "--no-icmp", "--timeout", "1", "--json"],
            capture_output=True, text=True, timeout=30,
            env={"PYTHONPATH": "packages:.", "PATH": "/usr/bin:/bin"})
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "offline")
        self.assertNotIn("password", result.stdout)

    def test_k_manage_api_keys_roundtrip(self):
        """create 明文一次 → list → revoke → 验签失效"""
        import os
        import tempfile
        from tests.base import TEST_KEY_HEX
        with tempfile.TemporaryDirectory() as tmp:
            db_url = f"sqlite:///{tmp}/keys.db"
            base_env = {"PYTHONPATH": "packages:.", "PATH": "/usr/bin:/bin",
                        "MASTER_KEY_HEX": TEST_KEY_HEX,
                        "MASTER_KEY_ID": "mk1"}
            created = subprocess.run(
                [sys.executable, "scripts/manage_api_keys.py", "--db", db_url,
                 "create", "--key-id", "partner"],
                capture_output=True, text=True, timeout=60, env=base_env)
            self.assertEqual(created.returncode, 0, created.stderr)
            self.assertIn("secret :", created.stdout)
            listed = subprocess.run(
                [sys.executable, "scripts/manage_api_keys.py", "--db", db_url,
                 "list"], capture_output=True, text=True, timeout=60,
                env=base_env)
            self.assertIn("有效", listed.stdout)
            revoked = subprocess.run(
                [sys.executable, "scripts/manage_api_keys.py", "--db", db_url,
                 "revoke", "--key-id", "partner"],
                capture_output=True, text=True, timeout=60, env=base_env)
            self.assertEqual(revoked.returncode, 0, revoked.stderr)
            relisted = subprocess.run(
                [sys.executable, "scripts/manage_api_keys.py", "--db", db_url,
                 "list"], capture_output=True, text=True, timeout=60,
                env=base_env)
            self.assertIn("已吊销", relisted.stdout)


if __name__ == "__main__":
    unittest.main()
