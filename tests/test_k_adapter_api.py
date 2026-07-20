# -*- coding: utf-8 -*-
"""
@file    test_k_adapter_api.py
@brief   M8 API 级测试:路由 operation_id 契约表、状态/事件面、三命令链
         (含幂等/超时/拒绝)、三 Webhook 验签矩阵、R9 全局错误信封逐
         分支 mock 活体、X-Request-Id 贯通、死信导出/重放 API、控制台
         前端路径 ⊆ 后端路由静态锁定。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import hashlib
import hmac
import json
import re
import unittest

from tests.ad_env import (AdapterEnv, ScriptedTransport, make_settings,
                          siyun_command_transport, skysys_command_transport,
                          timeout_transport)
from apps.adapter.core.vendors.siyun import td022_signature

ROUTE_TABLE = {
    ("GET", "/healthz"): "healthz",
    ("GET", "/api/v1/status/features"): "status_features",
    ("GET", "/api/v1/status/runtime"): "status_runtime",
    ("GET", "/api/v1/status/devices"): "status_devices",
    ("GET", "/api/v1/events/recent"): "events_recent",
    ("GET", "/console"): "console",
    ("GET", "/api/v1/deadletters/export"): "deadletters_export",
    ("POST", "/api/v1/deadletters/replay"): "deadletters_replay",
    ("POST", "/api/v1/commands/xingluo"): "commands_xingluo",
    ("POST", "/api/v1/commands/flycart"): "commands_flycart",
    ("POST", "/api/v1/commands/cleaning-robot"): "commands_cleaning_robot",
    ("POST", "/api/v1/webhooks/zhiguang"): "webhook_zhiguang",
    ("POST", "/api/v1/webhooks/siyun"): "webhook_siyun",
    ("POST", "/api/v1/webhooks/flighthub-sync"): "webhook_flighthub_sync",
}


def zg_signature(raw: bytes, secret: str = "zg-secret") -> str:
    """@brief 织光 webhook 签名(与服务端 hmac_v1 同公式)"""
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def siyun_headers(event_type: str, sub_type: str) -> dict:
    """@brief 合法 TD-022 三头"""
    ts, nonce = "1700000000", "N-1"
    return {"x-dji-timestamp": ts, "x-dji-nonce": nonce,
            "x-dji-signature": td022_signature("dji-ak", "dji-sk", ts, nonce,
                                               event_type, sub_type)}


def post_zg(env, client, payload: dict, signature: str = None):
    """@brief 织光 webhook 便捷 POST(默认带合法签名)"""
    raw = json.dumps(payload).encode()
    sig = zg_signature(raw) if signature is None else signature
    return client.request("POST", "/api/v1/webhooks/zhiguang", raw_body=raw,
                          headers={"X-Zg-Signature": sig},
                          content_type="application/json")


class TestStatusSurfaces(unittest.TestCase):
    """状态面:路由契约表 / 特性清单 / 设备合并 / 事件查询。"""

    def test_route_operation_id_table(self):
        """14 路由 operation_id 逐一锁定;console 不进 OpenAPI"""
        env = AdapterEnv()
        seen = {}
        for route in env.app.routes:
            operation_id = getattr(route, "operation_id", None)
            if not operation_id:
                continue
            for method in route.methods - {"HEAD", "OPTIONS"}:
                seen[(method, route.path)] = operation_id
        self.assertEqual(seen, ROUTE_TABLE)
        paths = env.client().get("/openapi.json").json()["paths"]
        self.assertNotIn("/console", paths)
        self.assertEqual(len(paths), 13)

    def test_healthz_features_env_warnings(self):
        """healthz 仅存活;features 10 项 flighthub=planned;M17 告警上面板"""
        settings = make_settings()
        settings.warnings.append("环境变量 ZG_APP_KEY 含疑似行内注释")
        env = AdapterEnv(settings=settings)
        client = env.client()
        self.assertEqual(client.get("/healthz").json(), {"status": "ok"})
        features = client.get("/api/v1/status/features").json()["features"]
        self.assertEqual(len(features), 10)
        by_id = {item["id"]: item["status"] for item in features}
        self.assertEqual(by_id["flighthub_sync"], "planned")
        self.assertEqual(by_id["commands_xingluo"], "enabled")
        runtime = client.get("/api/v1/status/runtime").json()
        self.assertIn("疑似行内注释", "".join(runtime["env_warnings"]))
        self.assertTrue(runtime["providers"]["zhiguang"]["configured"])

    def test_devices_note_then_merge_real_priority(self):
        """空快照带 note;模拟器灌入后消失;真实 OSD 同 SN 覆盖模拟"""
        empty = AdapterEnv().client().get("/api/v1/status/devices").json()
        self.assertEqual(empty["devices"], [])
        self.assertIn("暂无遥测", empty["note"])
        env = AdapterEnv(settings=make_settings(
            simulator_sns="UAV-001,CART-9"))
        client = env.client()
        snap = client.get("/api/v1/status/devices").json()
        self.assertEqual((snap["real_count"], snap["simulated_count"]),
                         (0, 2))
        payload = {"event_type": "hms", "sub_type": "battery",
                   "deviceSn": "CART-9", "battery": 0.42, "height": 335,
                   "lng": 118.1, "lat": 24.5, "online": True}
        resp = env.post_json(client, "/api/v1/webhooks/siyun", payload,
                             headers=siyun_headers("hms", "battery"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["osd_updated"])
        snap = client.get("/api/v1/status/devices").json()
        self.assertEqual(snap["real_count"], 1)
        cart = {row["sn"]: row for row in snap["devices"]}["CART-9"]
        self.assertEqual(cart["source"], "siyun")
        self.assertEqual(cart["battery_percent"], 42.0)
        self.assertEqual(cart["altitude"], 33.5)

    def test_events_recent_clamp_and_filters(self):
        """limit 钳制 1..容量;source/event_type 过滤;newest-first"""
        env = AdapterEnv()
        client = env.client()
        for index in range(3):
            resp = post_zg(env, client,
                           {"robotSn": "R-1", "id": f"A-{index}",
                            "alarmType": "alarm", "level": 2,
                            "status": "open", "time": "2026-07-20 09:00:00"})
            self.assertEqual(resp.status_code, 200)
        env.post_json(client, "/api/v1/webhooks/siyun",
                      {"event_type": "hms", "sub_type": "x", "id": "E-9"},
                      headers=siyun_headers("hms", "x"))
        doc = client.get("/api/v1/events/recent?limit=0").json()
        self.assertEqual(len(doc["events"]), 1)
        self.assertEqual(doc["events"][0]["source"], "siyun")
        doc = client.get("/api/v1/events/recent?source=zhiguang").json()
        self.assertEqual({row["source"] for row in doc["events"]},
                         {"zhiguang"})
        self.assertEqual(doc["events"][0]["event_id"],
                         "zhiguang:alarm:A-2:open")
        doc = client.get("/api/v1/events/recent?event_type=hms").json()
        self.assertEqual([row["event_id"] for row in doc["events"]],
                         ["siyun:hms:x:E-9"])


class TestCommandChains(unittest.TestCase):
    """三命令链:成功/超时/拒绝/幂等,厂商载荷透传。"""

    def test_xingluo_takeoff_full_chain(self):
        """takeoff 礼貌轮询至终态;批次登记续跟;camera_lens 透传上游"""
        transport = skysys_command_transport(["1", "1", "3"])
        env = AdapterEnv(transports={"skysys": transport})
        resp = env.post_json(env.client(), "/api/v1/commands/xingluo",
                             {"command": "takeoff", "site_id": "S1",
                              "mission_id": "M1", "camera_lens": "ir"})
        self.assertEqual(resp.status_code, 200)
        doc = resp.json()
        self.assertEqual((doc["command"], doc["status"],
                          doc["mission_batch"]),
                         ("takeoff", "succeeded", "MB-77"))
        self.assertIn("MB-77", env.ctx["ingest"].batches.active())
        self.assertTrue(resp.headers.get("x-request-id"))
        sent = [call for call in transport.calls
                if call["url"].endswith("/mission/command")]
        self.assertIn("ir", sent[0]["body"].decode("utf-8"))

    def test_xingluo_timeout_504_reject_409_upstream_502(self):
        """R9 上游三分支:ack 超时 504 / 明确拒绝 409 / HTTP 异常 502"""
        cases = [
            ({"default": timeout_transport}, 504, "reply_timeout"),
            ({"skysys": ScriptedTransport({
                "/auth/token": {"accessToken": "t"},
                "/mission/command": {"code": 1001, "message": "余额不足"},
            })}, 409, "upstream_rejected"),
            ({"skysys": ScriptedTransport({
                "/auth/token": {"accessToken": "t"},
                "/mission/command": (500, {"oops": 1}),
            })}, 502, "upstream_error"),
        ]
        for transports, status, code in cases:
            env = AdapterEnv(transports=transports)
            resp = env.post_json(env.client(), "/api/v1/commands/xingluo",
                                 {"command": "takeoff", "site_id": "S1",
                                  "mission_id": "M1"})
            self.assertEqual(resp.status_code, status)
            doc = resp.json()
            self.assertEqual((doc["code"], doc["data"]["error"]),
                             (status, code))
            self.assertTrue(doc["request_id"])

    def test_flycart_chain_idempotency_replay_and_conflict(self):
        """bid/task_id 回填;同键同体重放不重发;同键异体 409"""
        transport = siyun_command_transport(["running", "ok"])
        env = AdapterEnv(transports={"siyun": transport})
        client = env.client()
        payload = {"device_sn": "CART-9", "command": "start_task",
                   "task_id": "T-1", "idempotency_key": "IK-1"}
        doc = env.post_json(client, "/api/v1/commands/flycart",
                            payload).json()
        self.assertEqual((doc["status"], doc["bid"], doc["task_id"]),
                         ("succeeded", "BID-9", "T-1"))
        calls_before = len(transport.calls)
        replay = env.post_json(client, "/api/v1/commands/flycart",
                               payload).json()
        self.assertEqual(replay["bid"], "BID-9")
        self.assertEqual(len(transport.calls), calls_before)
        conflict = env.post_json(client, "/api/v1/commands/flycart",
                                 dict(payload, task_id="T-2"))
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["data"]["error"],
                         "idempotency_conflict")

    def test_cleaning_robot_accepted_and_time_format_400(self):
        """织光无终态查询→受理语义;排期时间格式非法→400 人话"""
        env = AdapterEnv()
        client = env.client()
        doc = env.post_json(client, "/api/v1/commands/cleaning-robot",
                            {"robot_id": "R-1", "command": "forced_inbound",
                             "status": "open"}).json()
        self.assertEqual((doc["robot_id"], doc["status"]),
                         ("R-1", "accepted"))
        resp = env.post_json(client, "/api/v1/commands/cleaning-robot",
                             {"robot_id": "R-1",
                              "command": "temporary_cleaning",
                              "scheduling_method": "specifiedTime",
                              "scheduled_cleaning_at": "2026/07/20 09:00"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("scheduled_cleaning_at", resp.json()["message"])


class TestWebhooks(unittest.TestCase):
    """入站推送:验签矩阵、缺字段、feature 门控、死信 API 闭环。"""

    def test_zhiguang_strict_log_and_bad_json(self):
        """strict 错签 401 信封;log 模式 200 且 signature_valid=false;
        坏 JSON 400"""
        env = AdapterEnv()
        client = env.client()
        resp = post_zg(env, client, {"robotSn": "R-1", "id": "A-1",
                                     "status": "open"}, signature="bad")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["data"]["error"], "signature_invalid")
        good = post_zg(env, client, {"robotSn": "R-1", "id": "A-1",
                                     "status": "open"})
        self.assertEqual(good.status_code, 200)
        self.assertTrue(good.json()["signature_valid"])
        self.assertEqual(good.json()["event_id"], "zhiguang:alarm:A-1:open")
        log_env = AdapterEnv(settings=make_settings(
            zg_verify_webhook="log"))
        resp = post_zg(log_env, log_env.client(),
                       {"id": "P-1", "robotSn": "R-1", "taskId": "T-1",
                        "taskState": "done"}, signature="bad")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["signature_valid"])
        raw = b"{not-json"
        resp = client.request("POST", "/api/v1/webhooks/zhiguang",
                              raw_body=raw,
                              headers={"X-Zg-Signature": zg_signature(raw)},
                              content_type="application/json")
        self.assertEqual(resp.status_code, 400)

    def test_siyun_valid_missing_field_and_bad_signature(self):
        """TD-022 合法 200(OSD+事件);缺 sub_type 400;错签 401"""
        env = AdapterEnv()
        client = env.client()
        payload = {"event_type": "task", "sub_type": "progress",
                   "deviceSn": "CART-9", "taskId": "T-7", "status": "done",
                   "battery": 0.9}
        doc = env.post_json(client, "/api/v1/webhooks/siyun", payload,
                            headers=siyun_headers("task", "progress")).json()
        self.assertTrue(doc["osd_updated"])
        self.assertEqual(doc["event_id"], "siyun:task:progress:N-1")
        resp = env.post_json(client, "/api/v1/webhooks/siyun",
                             {"event_type": "task"},
                             headers=siyun_headers("task", ""))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("event_type/sub_type", resp.json()["message"])
        bad = dict(siyun_headers("task", "progress"),
                   **{"x-dji-signature": "forged"})
        resp = env.post_json(client, "/api/v1/webhooks/siyun", payload,
                             headers=bad)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["data"]["error"], "signature_invalid")

    def test_flighthub_planned_501_then_enabled_200(self):
        """规划中 501 带 feature 数据;临时启用后走通用信封链路 200"""
        env = AdapterEnv()
        client = env.client()
        resp = env.post_json(client, "/api/v1/webhooks/flighthub-sync",
                             {"id": "S-1"})
        self.assertEqual(resp.status_code, 501)
        doc = resp.json()
        self.assertEqual(doc["data"],
                         {"feature": "flighthub_sync", "status": "planned",
                          "error": "feature_disabled"})
        for item in env.ctx["features"].features:
            if item["id"] == "flighthub_sync":
                item["status"] = "enabled"
        resp = env.post_json(client, "/api/v1/webhooks/flighthub-sync",
                             {"id": "S-1"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["event_id"], "flighthub:sync:S-1")

    def test_deadletter_export_replay_api_roundtrip(self):
        """下游宕→死信;导出 JSONL;修复后重放恰一次;二次重放全 skip"""
        downstream = ScriptedTransport({"/ingest": (503, {"err": "down"})})
        env = AdapterEnv(
            transports={"downstream": downstream,
                        "default": ScriptedTransport()},
            settings=make_settings(downstream_url="http://down.test/ingest",
                                   forward_max_retries=1))
        client = env.client()
        post_zg(env, client, {"robotSn": "R-1", "id": "A-1",
                              "status": "open"})
        env.ctx["forwarder"].flush()
        export = client.get("/api/v1/deadletters/export").json()
        self.assertEqual(export["count"], 1)
        self.assertIn("zhiguang:alarm:A-1:open", export["jsonl"])
        downstream.handlers["/ingest"] = {"code": 0}
        replay = env.post_json(client, "/api/v1/deadletters/replay",
                               {"jsonl": export["jsonl"]}).json()
        self.assertEqual(replay, {"enqueued": 1, "skipped": 0})
        env.ctx["forwarder"].flush()
        delivered = [call for call in downstream.calls
                     if b"A-1" in (call["body"] or b"")]
        self.assertEqual(len(delivered), 2)
        again = env.post_json(client, "/api/v1/deadletters/replay",
                              {"jsonl": export["jsonl"]}).json()
        self.assertEqual(again, {"enqueued": 0, "skipped": 1})


class TestEnvelopeAndConsole(unittest.TestCase):
    """R9 信封形状一致性、X-Request-Id 贯通、控制台静态锁定。"""

    def test_request_id_inbound_echo_and_error_body(self):
        """入站 X-Request-Id 原样贯通响应头与错误信封体"""
        env = AdapterEnv()
        resp = env.post_json(env.client(), "/api/v1/commands/xingluo",
                             {"command": "takeoff"},
                             headers={"X-Request-Id": "rid-fixed-1"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.headers["x-request-id"], "rid-fixed-1")
        self.assertEqual(resp.json()["request_id"], "rid-fixed-1")
        self.assertIn("command=takeoff", resp.json()["message"])

    def test_console_static_lockstep_with_backend(self):
        """控制台 200、含自动刷新;前端引用路径 ⊆ 后端路由(防漂移)"""
        env = AdapterEnv()
        resp = env.client().get("/console")
        self.assertEqual(resp.status_code, 200)
        html = resp.text
        self.assertIn("自动刷新", html)
        referenced = set(re.findall(r"['\"](/(?:api/v1|healthz)[^'\"?]*)",
                                    html))
        backend = {route.path for route in env.app.routes}
        self.assertTrue(referenced)
        missing = {path for path in referenced if path not in backend}
        self.assertEqual(missing, set())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
