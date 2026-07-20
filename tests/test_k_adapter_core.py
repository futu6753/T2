# -*- coding: utf-8 -*-
"""
@file    test_k_adapter_core.py
@brief   里程碑 8 适配器 core 验收(H09 §二 K / L01 语义等价复现):
         yamlite 子集解析、DSL 字段路径/单位换算/枚举/模板、CompositeSink
         真实优先合并与去重双限、dispatch 条件必填矩阵与幂等与 reply 语义、
         Forwarder 批量/退避/死信/出站签名、Poller 门控与单任务隔离、
         feature 门控、双厂商验签器、M17 env 硬化、六边形纯净度
         (core 零第三方依赖)。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import subprocess
import sys
import unittest

from tests.base import REPO_ROOT
from tests.ad_env import (FakeClock, ScriptedTransport, make_settings,
                          siyun_command_transport, skysys_command_transport,
                          specs, timeout_transport)

from apps.adapter.core import yamlite
from apps.adapter.core.config import (ensure_header_safe, load_settings,
                                      parse_env_text)
from apps.adapter.core.dispatch import (CommandDispatcher, validate_xingluo,
                                        validate_flycart,
                                        validate_cleaning_robot)
from apps.adapter.core.dsl import (MappingSpec, get_path, render_template,
                                   translate_osd)
from apps.adapter.core.errors import (ConfigError, FieldError,
                                      FeatureDisabledError,
                                      IdempotencyConflict, ReplyTimeout,
                                      SignatureError, UpstreamRejected)
from apps.adapter.core.features import FeatureRegistry
from apps.adapter.core.forwarder import Forwarder, sign_downstream
from apps.adapter.core.model import UnifiedEvent, UnifiedOsd
from apps.adapter.core.poller import Poller
from apps.adapter.core.sink import CompositeSink, DedupeCache
from apps.adapter.core.vendors.siyun import SiyunClient, td022_signature
from apps.adapter.core.vendors.skysys import SkysysClient
from apps.adapter.core.vendors.zhiguang import ZhiguangClient
import hashlib
import hmac
import json


def _event(event_id: str, source: str = "zhiguang",
           event_type: str = "alarm") -> UnifiedEvent:
    """@brief 事件工厂"""
    return UnifiedEvent(event_id=event_id, source=source,
                        event_type=event_type)


class TestYamliteAndDsl(unittest.TestCase):
    """DSL 文件解析与解释执行。"""

    def test_yamlite_subset_roundtrip(self):
        """受限子集:嵌套映射/列表项映射/引号键/标量类型/整行注释"""
        doc = yamlite.loads(
            "# 注释\n"
            "vendor: demo\n"
            "count: 3\n"
            "ratio: 0.5\n"
            "flag: true\n"
            "empty:\n"
            "table:\n"
            "  \"1\": info\n"
            "  \"2\": warn\n"
            "items:\n"
            "  - name: a\n"
            "    deep:\n"
            "      x: 1\n"
            "  - name: b\n"
            "  - plain\n")
        self.assertEqual(doc["vendor"], "demo")
        self.assertEqual((doc["count"], doc["ratio"], doc["flag"]),
                         (3, 0.5, True))
        self.assertIsNone(doc["empty"])
        self.assertEqual(doc["table"], {"1": "info", "2": "warn"})
        self.assertEqual(doc["items"][0], {"name": "a", "deep": {"x": 1}})
        self.assertEqual(doc["items"][2], "plain")
        with self.assertRaises(yamlite.YamliteError):
            yamlite.loads("a:\n\tb: 1")

    def test_dsl_path_units_enum_template(self):
        """字段路径(含列表下标)/scale+offset/枚举/默认/点路径模板"""
        raw = {"a": {"b": [{"c": 7}]}, "battery": 0.42, "state": "9"}
        self.assertEqual(get_path(raw, "a.b.0.c"), 7)
        self.assertIsNone(get_path(raw, "a.b.3.c"))
        rule_spec = MappingSpec({"vendor": "t", "device_kind": "unknown",
                                 "osd": {"sn": {"path": "a.b.0.c"},
                                         "battery_percent": {
                                             "path": "battery", "scale": 100,
                                             "offset": 0, "round": 1},
                                         "online": {"path": "state",
                                                    "enum": {"9": True},
                                                    "default": False}}})
        osd = translate_osd(rule_spec, raw, "now")
        self.assertEqual(osd.sn, "7")
        self.assertEqual(osd.battery_percent, 42.0)
        self.assertTrue(osd.online)
        rendered = render_template("zhiguang:alarm:{data.id}:{data.status}",
                                   {"data": {"id": 5, "status": 2}})
        self.assertEqual(rendered, "zhiguang:alarm:5:2")   # 自实现点路径渲染
        self.assertEqual(render_template("x-{miss}-y", {}), "x--y")
        with self.assertRaises(FieldError):
            render_template("{unclosed", {})

    def test_dsl_nan_and_bad_number_fallback(self):
        """L1-08:数值换算失败/NaN 回退默认,不产危险输出"""
        spec = MappingSpec({"vendor": "t", "device_kind": "unknown",
                            "osd": {"sn": {"path": "sn"},
                                    "speed": {"path": "v", "scale": 10,
                                              "default": None}}})
        osd = translate_osd(spec, {"sn": "s1", "v": "abc"}, "now")
        self.assertIsNone(osd.speed)


class TestSinkSemantics(unittest.TestCase):
    """CompositeSink:合并/环形缓冲/去重双限。"""

    def test_snapshot_merge_real_priority_and_note(self):
        """真实优先合并、双计数、空快照 note"""
        sink = CompositeSink()
        self.assertIn("note", sink.devices_view())
        sink.emit_osd(UnifiedOsd(sn="X", source="simulator", online=False))
        sink.emit_osd(UnifiedOsd(sn="X", source="zhiguang", online=True))
        sink.emit_osd(UnifiedOsd(sn="Y", source="simulator"))
        view = sink.devices_view()
        self.assertEqual((view["real_count"], view["simulated_count"]), (1, 2))
        row = {dev["sn"]: dev for dev in view["devices"]}["X"]
        self.assertEqual(row["source"], "zhiguang")        # 真实覆盖模拟
        self.assertNotIn("note", view)

    def test_recent_ring_clamp_filters_no_consume(self):
        """limit 钳制 1..容量、newest-first、过滤、不消费外发队列"""
        sink = CompositeSink(recent_maxlen=5)
        for index in range(8):
            sink.emit(_event(f"e{index}",
                             source="siyun" if index % 2 else "zhiguang"))
        view = sink.recent_view(limit=999)
        self.assertEqual(view["count"], 5)                 # 钳到容量
        self.assertEqual(view["events"][0]["event_id"], "e7")
        self.assertEqual(sink.recent_view(limit=-3)["count"], 1)
        only = sink.recent_view(limit=10, source="siyun")
        self.assertTrue(all(ev["source"] == "siyun" for ev in only["events"]))
        before = len(sink.outbound)
        sink.recent_view(limit=10)
        self.assertEqual(len(sink.outbound), before)       # 旁路只读

    def test_dedupe_ttl_capacity_and_poll_push_mutex(self):
        """同键互斥;TTL 过期重新接收;容量满淘汰最旧"""
        clock = FakeClock()
        sink = CompositeSink(dedupe_ttl_s=100.0, clock=clock)
        self.assertTrue(sink.emit(_event("zhiguang:alarm:1:1")))
        self.assertFalse(sink.emit(_event("zhiguang:alarm:1:1")))  # 推送重键
        self.assertEqual(sink.stats["events_deduped"], 1)
        clock.now += 101
        self.assertTrue(sink.emit(_event("zhiguang:alarm:1:1")))
        cache = DedupeCache(ttl_s=1000, capacity=2, clock=clock)
        self.assertFalse(cache.seen("a"))
        self.assertFalse(cache.seen("b"))
        self.assertFalse(cache.seen("c"))                  # 挤掉 a
        self.assertFalse(cache.seen("a"))


class TestDispatchSemantics(unittest.TestCase):
    """命令下行:条件必填矩阵/幂等/reply 语义。"""

    def _dispatcher(self):
        """@brief 假时钟调度器"""
        clock = FakeClock()
        return CommandDispatcher(make_settings(), clock=clock,
                                 sleeper=clock.sleep), clock

    def test_conditional_required_matrix_400(self):
        """L01 §4 条件必填全矩阵 → FieldError(400 语义)"""
        cases = [
            (validate_xingluo, {"command": "hover"}),
            (validate_xingluo, {"command": "takeoff", "site_id": "s"}),
            (validate_xingluo, {"command": "pause"}),
            (validate_flycart, {"command": "create_task"}),
            (validate_flycart, {"device_sn": "d", "command": "create_task"}),
            (validate_flycart, {"device_sn": "d", "command": "create_task",
                                "task": "not-dict"}),
            (validate_flycart, {"device_sn": "d",
                                "command": "edit_task_status",
                                "task_id": "t"}),
            (validate_flycart, {"device_sn": "d", "command": "raw_cmd",
                                "cmd": 3}),
            (validate_cleaning_robot, {"command": "forced_inbound",
                                       "robot_id": "r", "status": "maybe"}),
            (validate_cleaning_robot, {"command": "temporary_cleaning",
                                       "robot_id": "r",
                                       "scheduling_method": "specifiedTime"}),
            (validate_cleaning_robot, {"command": "temporary_cleaning",
                                       "robot_id": "r",
                                       "scheduling_method": "specifiedTime",
                                       "scheduled_cleaning_at": "明天下午"}),
        ]
        for validator, payload in cases:
            with self.assertRaises(FieldError, msg=payload):
                validator(payload)
        self.assertEqual(validate_xingluo(
            {"command": "takeoff", "site_id": "s", "mission_id": "m"}),
            "takeoff")
        self.assertEqual(validate_cleaning_robot(
            {"command": "temporary_cleaning", "robot_id": "r",
             "scheduling_method": "specifiedTime",
             "scheduled_cleaning_at": "2026-07-20 14:30:00"}),
            "temporary_cleaning")

    def test_idempotency_replay_conflict_ttl(self):
        """同键同体重放缓存;同键异体 409;TTL 过期后重发"""
        dispatcher, clock = self._dispatcher()
        settings = make_settings()
        client = SkysysClient(settings, skysys_command_transport(["3"]),
                              clock=clock)
        spec = specs()["skysys"]
        payload = {"command": "pause", "uav_id": "U1",
                   "idempotency_key": "K1"}
        first = dispatcher.dispatch(client, spec, payload)
        sent_after_first = dispatcher.stats["sent"]
        second = dispatcher.dispatch(client, spec, dict(payload))
        self.assertEqual(first, second)
        self.assertEqual(dispatcher.stats["sent"], sent_after_first)
        self.assertEqual(dispatcher.stats["replayed"], 1)
        with self.assertRaises(IdempotencyConflict):
            dispatcher.dispatch(client, spec,
                                {"command": "resume", "uav_id": "U1",
                                 "idempotency_key": "K1"})
        clock.now += settings.idempotency_ttl_s + 1
        third = dispatcher.dispatch(client, spec, dict(payload))
        self.assertEqual(dispatcher.stats["sent"], sent_after_first + 1)
        self.assertEqual(third["status"], "succeeded")

    def test_reply_ack_timeout_504(self):
        """ack 超时 → ReplyTimeout(504)"""
        dispatcher, clock = self._dispatcher()
        client = SkysysClient(make_settings(), ScriptedTransport({
            "/auth/token": {"accessToken": "tok"},
            "/mission/command": TransportTimeoutFactory(),
        }), clock=clock)
        with self.assertRaises(ReplyTimeout):
            dispatcher.dispatch(client, specs()["skysys"],
                                {"command": "pause", "uav_id": "U1"})
        self.assertEqual(dispatcher.stats["timeout"], 1)

    def test_reply_polite_poll_to_succeeded(self):
        """ack 到 → 每 2s 礼貌轮询,终态确认 → succeeded"""
        dispatcher, clock = self._dispatcher()
        client = SkysysClient(make_settings(),
                              skysys_command_transport(["1", "2", "3"]),
                              clock=clock)
        outcome = dispatcher.dispatch(client, specs()["skysys"],
                                      {"command": "takeoff", "site_id": "s",
                                       "mission_id": "m"})
        self.assertEqual(outcome["status"], "succeeded")
        self.assertEqual(outcome["handle"], "MB-77")
        self.assertTrue(all(step <= 2.0 for step in clock.sleeps))

    def test_reply_budget_exhausted_accepted(self):
        """预算内未确认终态 → accepted(200 受理语义)"""
        dispatcher, clock = self._dispatcher()
        client = SiyunClient(make_settings(),
                             siyun_command_transport(["running"]))
        outcome = dispatcher.dispatch(client, specs()["siyun"],
                                      {"device_sn": "d",
                                       "command": "start_task",
                                       "task_id": "T-1"})
        self.assertEqual(outcome["status"], "accepted")
        self.assertGreaterEqual(clock.now - 1000.0, 30.0)  # 预算耗尽

    def test_reply_terminal_failed_409(self):
        """上游终态失败/明确拒绝 → UpstreamRejected(409)"""
        dispatcher, _clock = self._dispatcher()
        client = SiyunClient(make_settings(),
                             siyun_command_transport(["failed"]))
        with self.assertRaises(UpstreamRejected):
            dispatcher.dispatch(client, specs()["siyun"],
                                {"device_sn": "d", "command": "start_task",
                                 "task_id": "T-1"})
        reject_client = SiyunClient(make_settings(), ScriptedTransport({
            "/openapi/v1/commands": {"code": 4001, "message": "禁飞时段"}}))
        with self.assertRaises(UpstreamRejected):
            dispatcher.dispatch(reject_client, specs()["siyun"],
                                {"device_sn": "d", "command": "raw_cmd",
                                 "cmd": {"m": 1}})


class TransportTimeoutFactory:
    """可放入脚本表的超时应答。"""

    def __call__(self, *_args, **_kwargs):
        """@brief 触发超时"""
        return timeout_transport()


class TestForwarderAndPoller(unittest.TestCase):
    """外发器与轮询器。"""

    def test_forwarder_batch_sign_backoff_deadletter(self):
        """批 ≤50/出站签名可验/指数退避/超限死信有界并放行下一批"""
        clock = FakeClock()
        settings = make_settings(downstream_url="http://down.test/hook",
                                 downstream_secret="dsec",
                                 forward_max_retries=3,
                                 dead_letter_maxlen=4)
        sink = CompositeSink(clock=clock)
        seen = {"bodies": [], "fail": True}

        def downstream(_method, _url, headers, body):
            """@brief 首批失败,恢复后校验签名"""
            if seen["fail"]:
                return (503, {"err": 1})
            canonical = body.decode("utf-8")
            expected = sign_downstream("dsec", canonical,
                                       headers["X-Adapter-Timestamp"],
                                       headers["X-Adapter-Nonce"])
            assert hmac.compare_digest(expected,
                                       headers["X-Adapter-Signature"])
            seen["bodies"].append(json.loads(canonical))
            return {"ok": 1}

        forwarder = Forwarder(settings, sink,
                              ScriptedTransport({"/hook": downstream}),
                              clock=clock, sleeper=clock.sleep)
        for index in range(60):
            sink.emit(_event(f"fw{index}"))
        forwarder.flush()                                   # 全失败入死信
        self.assertEqual(forwarder.stats["dead_lettered"], 60)
        self.assertEqual(len(forwarder.dead_letters), 4)    # 有界
        self.assertEqual(clock.sleeps[:2], [1.0, 2.0])      # base*2^(n-1)
        seen["fail"] = False
        sink.emit(_event("fw-next"))
        forwarder.flush()                                   # 死信不阻塞后续
        self.assertEqual(seen["bodies"][-1]["events"][0]["event_id"],
                         "fw-next")
        digest_probe = hashlib.sha256(b"x").hexdigest()
        self.assertEqual(len(digest_probe), 64)

    def test_poller_gating_isolation_stats(self):
        """未配置门控不发请求;单任务异常隔离;成功/失败/最近错误统计"""
        clock = FakeClock()
        poller = Poller(clock=clock)
        hits = {"ok": 0}

        def ok_job():
            """@brief 正常任务"""
            hits["ok"] += 1

        def bad_job():
            """@brief 异常任务"""
            raise RuntimeError("boom")

        poller.add_job("ok", 10, ok_job)
        poller.add_job("bad", 10, bad_job)
        poller.add_job("gated", 10, ok_job, gate=lambda: False)
        poller.run_pending(clock.now)
        views = {row["name"]: row for row in poller.jobs_view()}
        self.assertEqual(hits["ok"], 1)
        self.assertEqual(views["bad"]["fail"], 1)
        self.assertIn("boom", views["bad"]["last_error"])
        self.assertTrue(views["gated"]["gated"])
        clock.now += 10
        poller.run_pending(clock.now)
        self.assertEqual(hits["ok"], 2)                     # 异常不杀循环

    def test_feature_gating_planned_501(self):
        """feature 门控:planned/未知 → FeatureDisabledError(501)"""
        registry = FeatureRegistry("")
        registry.ensure("commands_xingluo")
        with self.assertRaises(FeatureDisabledError):
            registry.ensure("flighthub_sync")
        with self.assertRaises(FeatureDisabledError):
            registry.ensure("no_such_feature")


class TestVendorSecurity(unittest.TestCase):
    """厂商验签器与 env 硬化。"""

    def test_zhiguang_signer_modes(self):
        """strict 失败 401 / log 只记结论 / off 放行;覆盖原始字节"""
        settings = make_settings(zg_verify_webhook="strict")
        client = ZhiguangClient(settings, ScriptedTransport())
        body = b'{"id": 1}'
        good = client.signer.sign(body)
        self.assertTrue(client.verify_webhook(body, good))
        with self.assertRaises(SignatureError):
            client.verify_webhook(body, "bad")
        settings.zg_verify_webhook = "log"
        self.assertFalse(client.verify_webhook(body, "bad"))
        settings.zg_verify_webhook = "off"
        self.assertTrue(client.verify_webhook(body, "bad"))
        self.assertNotEqual(client.signer.sign(b'{"id": 1} '), good)

    def test_siyun_td022_formula(self):
        """TD-022:HmacSHA256(AK+ts+nonce+event_type+sub_type, SK) 逐字"""
        expected = hmac.new(b"sk", b"ak100nonceEVSUB",
                            hashlib.sha256).hexdigest()
        self.assertEqual(td022_signature("ak", "sk", "100", "nonce",
                                         "EV", "SUB"), expected)

    def test_skysys_token_ttl_and_batch_parse(self):
        """token TTL 内复用;批次候选解析,缺失显式报错"""
        clock = FakeClock()
        transport = ScriptedTransport({
            "/auth/token": {"data": {"accessToken": "tok-2"}},
            "/mission/batch/active": {"data": []}})
        client = SkysysClient(make_settings(), transport, clock=clock)
        client.fetch_active_batches()
        client.fetch_active_batches()
        token_calls = [call for call in transport.calls
                       if call["url"].endswith("/auth/token")]
        self.assertEqual(len(token_calls), 1)               # TTL 内复用
        clock.now += make_settings().skysys_token_ttl_s + 1
        client.fetch_active_batches()
        token_calls = [call for call in transport.calls
                       if call["url"].endswith("/auth/token")]
        self.assertEqual(len(token_calls), 2)
        self.assertEqual(client.extract_batch(
            {"data": {"batchId": 55}}), "55")
        with self.assertRaises(Exception):
            client.extract_batch({"data": {}})

    def test_m17_env_hardening(self):
        """M17:行内注释保留进值并告警;非 latin-1 请求头人话 ConfigError"""
        values, warnings = parse_env_text(
            "# 整行注释合法\n"
            "ZG_BASE_URL=http://zg.test # 行内注释是事故源\n"
            "ZG_APP_KEY=\"quoted-key\"\n")
        self.assertIn("# 行内注释是事故源", values["ZG_BASE_URL"])
        self.assertEqual(values["ZG_APP_KEY"], "quoted-key")
        self.assertTrue(any("行内注释" in warning for warning in warnings))
        settings = load_settings(values, extra_warnings=warnings)
        self.assertTrue(settings.warnings)
        with self.assertRaises(ConfigError) as caught:
            ensure_header_safe("X-ZG-App-Key", "键值带中文注释")
        self.assertIn("注释必须独立成行", caught.exception.message)


class TestHexagonPurity(unittest.TestCase):
    """六边形架构纯净度。"""

    def test_core_zero_third_party_imports(self):
        """core 全模块在第三方导入被封禁的解释器中可导入(L01 §2)"""
        probe = (
            "import sys\n"
            "class Blocker:\n"
            "    BANNED = {'fastapi', 'pydantic', 'starlette', 'uvicorn',\n"
            "              'yaml', 'requests', 'httpx', 'redis'}\n"
            "    def find_module(self, name, path=None):\n"
            "        return self if name.split('.')[0] in self.BANNED else None\n"
            "    def load_module(self, name):\n"
            "        raise ImportError('third-party banned: ' + name)\n"
            "sys.meta_path.insert(0, Blocker())\n"
            "import apps.adapter.core.config, apps.adapter.core.dispatch\n"
            "import apps.adapter.core.dsl, apps.adapter.core.errors\n"
            "import apps.adapter.core.features, apps.adapter.core.forwarder\n"
            "import apps.adapter.core.ingest, apps.adapter.core.model\n"
            "import apps.adapter.core.poller, apps.adapter.core.simulator\n"
            "import apps.adapter.core.sink, apps.adapter.core.tracing\n"
            "import apps.adapter.core.yamlite\n"
            "import apps.adapter.core.vendors.transport\n"
            "import apps.adapter.core.vendors.zhiguang\n"
            "import apps.adapter.core.vendors.skysys\n"
            "import apps.adapter.core.vendors.siyun\n"
            "import apps.adapter.core.vendors.flighthub\n"
            "print('PURE')\n")
        result = subprocess.run([sys.executable, "-c", probe],
                                capture_output=True, text=True,
                                cwd=REPO_ROOT, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PURE", result.stdout)


from apps.adapter.core.vendors.transport import TransportTimeout  # noqa: E402


if __name__ == "__main__":
    unittest.main()
