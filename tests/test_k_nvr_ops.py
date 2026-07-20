# -*- coding: utf-8 -*-
"""
@file    test_k_nvr_ops.py
@brief   H09 §二 K(NVR 组)运维线:Webhook/阿里云签名逐字锁定、重试队列
         线性退避与持久化续跑、13-R-NVR-3 周报三态(无 Key/锚点通过/锚点
         缺失降级)与请求形态锁定、/metrics 契约(登录/Bearer 常数时间)、
         对外 HMAC 五行待签串(验签/篡改/时间容差/吊销/一律 401)、
         保留期清理、去抖回放脚本冒烟。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import json
import time
import unittest
from datetime import datetime, timedelta, timezone

from tests.nvr_env import NvrEnv

from apps.nvr.dispatch import (
    AliyunSmsChannel, Dispatcher, WebhookChannel, aliyun_sms_signature,
    build_sms_params, sign_webhook,
)
from apps.nvr.report import build_claude_request


class TestSignatureContracts(unittest.TestCase):
    """签名逐字锁定(L04 §7)。"""

    def test_k_webhook_signature_verbatim(self):
        """Webhook:sha256=HMAC(secret, "{ts}."+body) 固定输入固定输出"""
        signature = sign_webhook("topsecret", "1700000000",
                                 '{"kind":"firing"}')
        self.assertEqual(
            signature,
            "sha256=66807e2b1bb9155e1735a7ffd1f140eb1721ce05ded54fc4379b"
            "bb267cde7d63")

    def test_k_aliyun_rpc_signature_verbatim(self):
        """阿里云 RPC HMAC-SHA1:固定参数集 → 固定 Base64 签名"""
        params = build_sms_params(
            "testId", "cn-hangzhou", "港电", "SMS_001", "13800000000",
            "东渡泊位1-NVR", "offline", nonce="fixednonce",
            timestamp="2026-07-19T00:00:00Z")
        self.assertEqual(aliyun_sms_signature("testSecret", "GET", params),
                         "TKEopyjQUVVFjtITnU0rjBgvomE=")

    def test_k_sms_template_variables_truncated_to_20(self):
        """模板两变量超 20 字自动截断"""
        params = build_sms_params(
            "id", "cn", "签名", "T", "138", "超" * 30, "状" * 25)
        variables = json.loads(params["TemplateParam"])
        self.assertEqual(len(variables["device"]), 20)
        self.assertEqual(len(variables["status"]), 20)

    def test_k_aliyun_channel_requires_code_ok(self):
        """短信应答须 Code:OK,否则抛错交重试"""
        good = AliyunSmsChannel("id", "sec", "cn", "签", "T", ["138"],
                                transport=lambda url: (200, '{"Code":"OK"}'))
        good.send({"device_name": "d", "status_text": "s"})   # 不抛
        bad = AliyunSmsChannel("id", "sec", "cn", "签", "T", ["138"],
                               transport=lambda url: (
                                   200, '{"Code":"isv.MOBILE_NUMBER_ILLEGAL"}'))
        with self.assertRaises(RuntimeError):
            bad.send({"device_name": "d", "status_text": "s"})


class TestDispatchRetryPersistence(unittest.TestCase):
    """重试队列:线性退避 + 持久化续跑 + abandoned。"""

    def test_k_retry_backoff_and_restart_resume(self):
        """失败→failed(next_attempt 线性)→新派发器(重启)续跑→sent"""
        env = NvrEnv(consecutive_failures=1)
        try:
            admin = env.login("admin")
            device = env.create_device(admin, "NVR-A", "10.0.0.1")
            attempts = {"count": 0}

            def flaky(url, headers, body):
                attempts["count"] += 1
                return (500, "") if attempts["count"] < 2 else (200, "ok")
            channel = WebhookChannel("http://hook/x", "sec", transport=flaky)
            dispatcher = Dispatcher(env.db, [channel], max_attempts=3,
                                    backoff_seconds=60)
            env.ctx.alerts._dispatcher = dispatcher
            env.fleet.set("10.0.0.1", "offline")
            admin.post("/api/patrol/run")               # 触发→首发失败
            note = dispatcher.list_notifications()[0]
            self.assertEqual(note["state"], "failed")
            self.assertIsNotNone(note["next_attempt_at"])
            # "重启":新派发器从库续跑(到期时间注入)
            resumed = Dispatcher(env.db, [channel], max_attempts=3,
                                 backoff_seconds=60)
            future = datetime.now(timezone.utc) + timedelta(seconds=61)
            resumed.process_pending(now=future)
            self.assertEqual(
                resumed.list_notifications()[0]["state"], "sent")
        finally:
            env.close()

    def test_k_abandoned_after_max_attempts(self):
        """max_attempts(含首发)用尽 → abandoned 且不再重试"""
        env = NvrEnv(consecutive_failures=1)
        try:
            admin = env.login("admin")
            env.create_device(admin, "NVR-A", "10.0.0.1")
            always_fail = WebhookChannel(
                "http://hook/x", "sec",
                transport=lambda url, headers, body: (503, ""))
            dispatcher = Dispatcher(env.db, [always_fail], max_attempts=2,
                                    backoff_seconds=1)
            env.ctx.alerts._dispatcher = dispatcher
            env.fleet.set("10.0.0.1", "offline")
            admin.post("/api/patrol/run")
            future = datetime.now(timezone.utc) + timedelta(seconds=5)
            dispatcher.process_pending(now=future)
            note = dispatcher.list_notifications()[0]
            self.assertEqual(note["state"], "abandoned")
            self.assertEqual(note["attempts"], 2)
            self.assertIsNone(note["next_attempt_at"])
        finally:
            env.close()


class TestReportHttp(unittest.TestCase):
    """13-R-NVR-3 周报事实层(HTTP 级)。"""

    def test_k_report_three_states_and_request_shape(self):
        """无 Key=template;锚点通过=claude;锚点缺失=template+原因"""
        env = NvrEnv()
        try:
            admin = env.login("admin")
            device = env.create_device(admin, "NVR-A", "10.0.0.1")
            env.fleet.set("10.0.0.1", "online")
            admin.post("/api/patrol/run")
            first = admin.request(
                "POST", "/api/reports/generate",
                raw_body=b'{"period_days": 7}',
                content_type="application/json").json()
            self.assertEqual(first["generated_by"], "template")
            self.assertIn("未配置", first["reason"])
            # 注入含锚点的 fake Claude
            captured = {}

            def good_transport(request_body):
                captured.update(request_body)
                facts = json.loads(
                    request_body["messages"][0]["content"].split("\n", 1)[1])
                return (f"周报:采样 {facts['sample_total']} 次,"
                        f"告警 {facts['alerts']['total']} 起,"
                        f"可用率 {facts['availability'] * 100:.1f}%。")
            env.ctx.reports._api_key = "k"
            env.ctx.reports._transport = good_transport
            second = admin.request(
                "POST", "/api/reports/generate", raw_body=b"{}",
                content_type="application/json").json()
            self.assertEqual(second["generated_by"], "claude")
            self.assertEqual(captured["model"], "claude-sonnet-4-6")
            self.assertIn("不得推算或虚构",
                          captured["messages"][0]["content"])
            # 无锚点 fake → 降级
            env.ctx.reports._transport = lambda req: "一切良好。"
            third = admin.request(
                "POST", "/api/reports/generate", raw_body=b"{}",
                content_type="application/json").json()
            self.assertEqual(third["generated_by"], "template")
            self.assertIn("锚点缺失", third["reason"])
            latest = admin.get("/api/reports/latest").json()
            self.assertEqual(latest["id"], third["id"])
            self.assertIn("facts", latest)          # 聚合数据随报告落库
            listing = admin.get("/api/reports").json()["reports"]
            self.assertEqual(len(listing), 3)
        finally:
            env.close()


class TestMetricsAndPublicApi(unittest.TestCase):
    """/metrics 契约 + 对外 HMAC。"""

    def setUp(self):
        self.env = NvrEnv(metrics_token="mtok")
        self.admin = self.env.login("admin")
        self.device = self.env.create_device(self.admin, "NVR-A", "10.0.0.1",
                                             region="东渡", station="泊位1")
        self.env.fleet.set("10.0.0.1", "online")
        self.admin.post("/api/patrol/run")

    def tearDown(self):
        self.env.close()

    def test_k_metrics_auth_and_format(self):
        """未登录 401;Bearer 正确 200;格式 0.0.4 含标签级指标"""
        from selfcheck.asgi import AsgiClient
        anonymous = AsgiClient(self.env.app)
        self.assertEqual(anonymous.get("/metrics").status_code, 401)
        wrong = anonymous.get("/metrics",
                              headers={"Authorization": "Bearer bad"})
        self.assertEqual(wrong.status_code, 401)
        bearer = anonymous.get("/metrics",
                               headers={"Authorization": "Bearer mtok"})
        self.assertEqual(bearer.status_code, 200)
        text = bearer.body.decode()
        self.assertIn('nvrm_device_up{device="NVR-A"', text)
        self.assertIn("nvrm_devices_total{", text)
        self.assertIn("process_start_time_seconds", text)
        session = self.admin.get("/metrics")            # 登录会话同样放行
        self.assertEqual(session.status_code, 200)

    def test_k_public_api_hmac_contract(self):
        """五行待签串验签;篡改/过期/吊销/缺头一律 401「鉴权失败」"""
        secret = self.env.ctx.public_guard.create_key("partner")
        ok = self.env.public_get(self.admin, "/public/v1/status/devices",
                                 secret, "partner", {"region": "东渡"})
        self.assertEqual(ok.status_code, 200)
        device = ok.json()["devices"][0]
        self.assertNotIn("host", device)               # 脱敏
        self.assertNotIn("push_token", device)
        tampered = self.env.public_get(self.admin, "/public/v1/alerts",
                                       "wrong-secret", "partner")
        self.assertEqual(tampered.status_code, 401)
        self.assertEqual(tampered.json()["detail"], "鉴权失败")
        from apps.nvr.exposition import sign_public_request
        stale_ts = str(time.time() - 400)
        stale = self.admin.get(
            "/public/v1/status/overview",
            headers={"X-API-Key-Id": "partner",
                     "X-API-Timestamp": stale_ts,
                     "X-API-Signature": sign_public_request(
                         secret, "GET", "/public/v1/status/overview", {},
                         stale_ts)})
        self.assertEqual(stale.status_code, 401)
        self.env.ctx.public_guard.revoke_key("partner")
        revoked = self.env.public_get(self.admin,
                                      "/public/v1/status/overview",
                                      secret, "partner")
        self.assertEqual(revoked.status_code, 401)
        rows = self.env.db.query(
            "SELECT COUNT(*) FROM audit_logs"
            " WHERE detail LIKE '%public_auth_failed%'")
        self.assertGreaterEqual(rows[0][0], 3)


class TestRetentionAndOverview(unittest.TestCase):
    """保留期清理 + overview by_kind 分桶。"""

    def test_k_retention_prune_keeps_timeline(self):
        """过期明细清理;时间线不清"""
        env = NvrEnv()
        try:
            admin = env.login("admin")
            device = env.create_device(admin, "NVR-A", "10.0.0.1")
            env.fleet.set("10.0.0.1", "offline")
            admin.post("/api/patrol/run")
            old = (datetime.now(timezone.utc)
                   - timedelta(days=100)).isoformat()
            env.db.execute(
                "UPDATE nvr_check_results SET checked_at = ?", (old,))
            removed = env.ctx.devices.prune(90)
            self.assertEqual(removed, 1)
            timeline = env.db.query(
                "SELECT COUNT(*) FROM nvr_timeline")[0][0]
            self.assertGreaterEqual(timeline, 1)       # 时间线保留
            self.assertEqual(env.ctx.devices.prune(0), 0)   # 0=永久
        finally:
            env.close()

    def test_k_overview_by_kind_buckets(self):
        """by_kind 分桶(nvr/push 各自统计)"""
        env = NvrEnv()
        try:
            admin = env.login("admin")
            env.create_device(admin, "NVR-A", "10.0.0.1")
            push = env.create_device(admin, "光伏箱", "", kind="push")
            env.fleet.set("10.0.0.1", "online")
            admin.post("/api/patrol/run")
            admin.get(f"/ingest/{push['push_token']}")
            overview = admin.get("/api/status/overview").json()
            self.assertEqual(overview["by_kind"]["nvr"].get("online"), 1)
            self.assertEqual(overview["by_kind"]["push"].get("online"), 1)
        finally:
            env.close()


class TestDebounceReplayArtifact(unittest.TestCase):
    """R-NVR-1 回放脚本冒烟(工件可运行)。"""

    def test_k_replay_script_runs(self):
        """回放脚本输出五模式 Pareto 表"""
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, "benchmarks/debounce_replay.py"],
            capture_output=True, text=True, timeout=60,
            env={"PYTHONPATH": "packages:.", "PATH": "/usr/bin:/bin"})
        self.assertEqual(result.returncode, 0, result.stderr)
        for mode in ("consecutive_failures", "ewma", "adaptive"):
            self.assertIn(mode, result.stdout)


if __name__ == "__main__":
    unittest.main()
