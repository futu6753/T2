# -*- coding: utf-8 -*-
"""
@file    test_r_ad.py
@brief   M8 固定回归锚点(H09 K.2,名称不可改):
         13-R-AD-1 映射 DSL 正确性——测试内独立手写参考翻译器与 DSL
                    在固定种子金样上逐样等价 + 契约工件零漂移;
         13-R-AD-3 死信闭环——失败入死信、导出、修复后重放,下游对
                    每事件恰见一次,重复重放/已投递混入全部跳过;
         13-R-AD-4 接入成本——B9 基准双条件跑通:DSL 零新增代码,
                    硬编码条件有代码成本,两条件逐样等价且耗时留痕。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import random
import unittest

from tests.ad_env import AdapterEnv, ScriptedTransport, make_settings, specs

from apps.adapter.core.dsl import translate_events, translate_osd
from apps.adapter.core.forwarder import canonical_json
from apps.adapter.core.model import UnifiedEvent, UnifiedOsd
from benchmarks import adapter_onboard_benchmark as bench
from scripts import adapter_contract

NOW = "2026-07-20T09:30:00+08:00"


def _siyun_samples(count: int = 40, seed: int = 42) -> list:
    """@brief 固定种子司运金样(单位换算/枚举/模板/告警混合)"""
    rng = random.Random(seed)
    rows = []
    for index in range(count):
        raw = {"deviceSn": f"CART-{index:03d}", "online": rng.random() < 0.8,
               "battery": round(rng.uniform(0.0, 1.0), 4),
               "lng": round(118.0 + rng.uniform(0, 0.5), 7),
               "lat": round(24.0 + rng.uniform(0, 0.5), 7),
               "height": rng.randrange(0, 1200),
               "speed": round(rng.uniform(0, 15), 2),
               "heading": rng.randrange(0, 360),
               "mode": rng.choice([0, 1, 5]), "updateTime": NOW,
               "time": NOW}
        if index % 4 == 0:
            raw.update({"event_type": "hms", "sub_type": "battery",
                        "id": f"E-{index}",
                        "severity": rng.choice(["info", "warn"])})
        elif index % 4 == 2:
            raw.update({"taskId": f"T-{index}",
                        "status": rng.choice(["ok", "failed"])})
            if rng.random() < 0.5:
                raw["severity_hint"] = "warn"
        rows.append(raw)
    return rows


def _reference_osd(raw: dict) -> UnifiedOsd:
    """@brief 参考实现(独立手写,不经 DSL 引擎)"""
    return UnifiedOsd(
        sn=str(raw["deviceSn"]), source="siyun", device_kind="flycart",
        online=bool(raw.get("online", False)),
        battery_percent=round(float(raw["battery"]) * 100, 1),
        longitude=round(float(raw["lng"]), 6),
        latitude=round(float(raw["lat"]), 6),
        altitude=round(float(raw["height"]) * 0.1, 1),
        speed=float(raw["speed"]), heading=float(raw["heading"]),
        mode_code=str(raw["mode"]), updated_at=str(raw["updateTime"]))


def _reference_events(raw: dict) -> list:
    """@brief 参考事件实现:推送优先于任务,首个命中即止"""
    if raw.get("event_type") is not None:
        return [UnifiedEvent(
            event_id=f"siyun:{raw['event_type']}:{raw['sub_type']}"
                     f":{raw['id']}",
            source="siyun", event_type=str(raw["event_type"]),
            severity=str(raw.get("severity", "info")), ts=str(raw["time"]),
            sn=str(raw["deviceSn"]), data=raw)]
    if raw.get("taskId") is not None:
        severity = "warn" if raw.get("severity_hint") == "warn" else "info"
        return [UnifiedEvent(
            event_id=f"siyun:task:{raw['taskId']}:{raw['status']}",
            source="siyun", event_type="flycart_task", severity=severity,
            ts=str(raw["time"]), sn=str(raw["deviceSn"]), data=raw)]
    return []


class TestRAdAnchors(unittest.TestCase):
    """M8 三固定锚点。"""

    def test_r_ad1_dsl(self):
        """13-R-AD-1:金样逐样等价 + 契约(openapi+映射锁)零漂移"""
        spec = specs()["siyun"]
        for raw in _siyun_samples():
            self.assertEqual(translate_osd(spec, raw, NOW).to_dict(),
                             _reference_osd(raw).to_dict(), raw)
            self.assertEqual(
                [event.to_dict()
                 for event in translate_events(spec, raw, NOW)],
                [event.to_dict() for event in _reference_events(raw)], raw)
        self.assertEqual(adapter_contract.diff(), 0)

    def test_r_ad3_replay(self):
        """13-R-AD-3:死信导出/重放,下游恰一次;重复与已投递全跳过"""
        downstream = ScriptedTransport({"/ingest": (503, {"err": "down"})})
        env = AdapterEnv(
            transports={"downstream": downstream,
                        "default": ScriptedTransport()},
            settings=make_settings(downstream_url="http://down.test/ingest",
                                   forward_max_retries=1))
        forwarder, sink = env.ctx["forwarder"], env.ctx["sink"]
        for index in range(2):
            sink.emit(UnifiedEvent(event_id=f"zhiguang:alarm:D-{index}:open",
                                   source="zhiguang", event_type="alarm"))
        forwarder.flush()
        self.assertEqual(len(forwarder.dead_letters), 2)
        exported = forwarder.export_dead_letters()
        self.assertEqual(len(exported.splitlines()), 2)
        downstream.handlers["/ingest"] = {"code": 0}
        self.assertEqual(forwarder.replay(exported),
                         {"enqueued": 2, "skipped": 0})
        fixed_at = len(downstream.calls)
        forwarder.flush()
        delivered = downstream.calls[fixed_at:]
        for index in range(2):
            hits = [call for call in delivered
                    if f"D-{index}".encode() in call["body"]]
            self.assertEqual(len(hits), 1, f"D-{index} 下游应恰见一次")
        self.assertEqual(forwarder.replay(exported),
                         {"enqueued": 0, "skipped": 2})
        ghost = UnifiedEvent(event_id="zhiguang:alarm:D-99:open",
                             source="zhiguang", event_type="alarm")
        mixed = exported + "\n" + canonical_json(ghost.to_dict())
        self.assertEqual(forwarder.replay(mixed),
                         {"enqueued": 1, "skipped": 2})
        forwarder.flush()
        self.assertEqual(len(downstream.calls[fixed_at:]), 2)

    def test_r_ad4_cost(self):
        """13-R-AD-4:B9 双条件——DSL 零代码/硬编码有代码,逐样等价"""
        rows = bench.run(samples=60, seed=7)
        by_condition = {row["condition"]: row for row in rows}
        dsl, hard = by_condition["dsl"], by_condition["hardcoded"]
        self.assertGreater(dsl["mapping_lines"], 0)
        self.assertEqual(dsl["new_code_lines"], 0)
        self.assertEqual(hard["mapping_lines"], 0)
        self.assertGreater(hard["new_code_lines"], 0)
        for row in rows:
            self.assertEqual(row["checks_passed"], row["checks_total"], row)
            self.assertIsInstance(row["elapsed_ms"], float)
        print_fp = bench.fingerprint(seed=7)
        self.assertEqual(print_fp["seed"], 7)
        self.assertIn("python", print_fp)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
