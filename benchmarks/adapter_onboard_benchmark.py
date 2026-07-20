# -*- coding: utf-8 -*-
"""
@file    adapter_onboard_benchmark.py
@brief   13-B9 新厂商接入成本基准(标准任务:曜阳储能,模拟第五家
         厂商):同一批固定种子金样报文,分别经 A. DSL 映射声明
         (harness/mappings/yaoyang.yaml,引擎零新增代码)与 B. 硬编码
         翻译器(本文件 HARDCODED 标记段)翻译,对照三项成本/质量
         指标——业务侧改动行数、逐样输出等价、耗时;含环境指纹
         (H09:python/平台/种子),供 13-R-AD-4 断言。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
用法:PYTHONPATH=packages:. python3 benchmarks/adapter_onboard_benchmark.py
"""
import os
import platform
import random
import sys
import time

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "packages"))
sys.path.insert(0, REPO_ROOT)

from apps.adapter.core.dsl import (MappingSpec,                # noqa: E402
                                   translate_events, translate_osd)
from apps.adapter.core.model import UnifiedEvent, UnifiedOsd   # noqa: E402
from apps.adapter.core.yamlite import loads as yamlite_loads   # noqa: E402

MAPPING_PATH = os.path.join(REPO_ROOT, "harness", "mappings", "yaoyang.yaml")
SAMPLES = 200
SEED = 20260720
NOW = "2026-07-20T08:00:00+08:00"
_LEVELS = {"1": "info", "2": "warn", "3": "critical"}


def golden_samples(count: int = SAMPLES, seed: int = SEED) -> list:
    """@brief 固定种子生成曜阳原始报文金样(约 1/3 附告警字段)"""
    rng = random.Random(seed)
    rows = []
    for index in range(count):
        raw = {"cabinetSn": f"YY-{index:04d}",
               "comm": rng.choice(["1", "0"]),
               "soc": round(rng.uniform(0.05, 0.99), 4),
               "lon": round(118.0 + rng.uniform(0, 0.2), 7),
               "lat": round(24.4 + rng.uniform(0, 0.2), 7),
               "mode": rng.choice([0, 1, 2]),
               "ts": NOW}
        if index % 3 == 0:
            raw.update({"alarmId": f"AL-{index}",
                        "state": rng.choice(["open", "closed"]),
                        "level": rng.choice(["1", "2", "3", "9"])})
        rows.append(raw)
    return rows


# HARDCODED-BEGIN 硬编码翻译器(对照条件 B:接一家厂商要新写的代码)
def hardcoded_osd(raw: dict) -> UnifiedOsd:
    """@brief 曜阳快照翻译(手写等价物)"""
    return UnifiedOsd(
        sn=str(raw["cabinetSn"]), source="yaoyang", device_kind="unknown",
        online={"1": True, "0": False}.get(str(raw.get("comm")), False),
        battery_percent=round(float(raw["soc"]) * 100, 1),
        longitude=round(float(raw["lon"]), 6),
        latitude=round(float(raw["lat"]), 6),
        mode_code=str(raw["mode"]), updated_at=str(raw["ts"]))


def hardcoded_events(raw: dict) -> list:
    """@brief 曜阳告警事件翻译(手写等价物)"""
    if raw.get("alarmId") is None:
        return []
    return [UnifiedEvent(
        event_id=f"yaoyang:alarm:{raw['alarmId']}:{raw['state']}",
        source="yaoyang", event_type="energy_alarm",
        severity=_LEVELS.get(str(raw.get("level")), "warn"),
        ts=str(raw["ts"]), sn=str(raw["cabinetSn"]), data=raw)]
# HARDCODED-END


def _load_spec() -> MappingSpec:
    """@brief 读取曜阳映射声明(契约工件,入 diff 防漂移)"""
    with open(MAPPING_PATH, "r", encoding="utf-8") as handle:
        return MappingSpec(yamlite_loads(handle.read()))


def mapping_line_count() -> int:
    """@brief 条件 A 成本:映射声明有效行数(去注释/空行)"""
    with open(MAPPING_PATH, "r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    return len([line for line in lines
                if line.strip() and not line.strip().startswith("#")])


def hardcoded_line_count() -> int:
    """@brief 条件 B 成本:HARDCODED 标记段代码行数(去空行)"""
    with open(os.path.abspath(__file__), "r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    begin = next(i for i, line in enumerate(lines)
                 if "HARDCODED-BEGIN" in line)
    end = next(i for i, line in enumerate(lines) if "HARDCODED-END" in line)
    return len([line for line in lines[begin + 1:end] if line.strip()])


def _translate_all(condition: str, spec: MappingSpec, samples: list) -> list:
    """@brief 逐样翻译 → (osd_dict, [event_dict]) 序列"""
    outputs = []
    for raw in samples:
        if condition == "dsl":
            osd = translate_osd(spec, raw, NOW)
            events = translate_events(spec, raw, NOW)
        else:
            osd = hardcoded_osd(raw)
            events = hardcoded_events(raw)
        outputs.append((osd.to_dict(),
                        [event.to_dict() for event in events]))
    return outputs


def run(samples: int = SAMPLES, seed: int = SEED) -> list:
    """@brief 双条件跑标准任务,返回对照行(13-R-AD-4 断言入口)"""
    rows_raw = golden_samples(samples, seed)
    spec = _load_spec()
    golden = _translate_all("hardcoded", spec, rows_raw)
    rows = []
    for condition in ("dsl", "hardcoded"):
        started = time.perf_counter()
        outputs = _translate_all(condition, spec, rows_raw)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        checks = sum(1 for got, want in zip(outputs, golden)
                     if got == want)
        rows.append({
            "condition": condition, "samples": samples,
            "checks_passed": checks, "checks_total": samples,
            "mapping_lines": mapping_line_count()
            if condition == "dsl" else 0,
            "new_code_lines": 0 if condition == "dsl"
            else hardcoded_line_count(),
            "elapsed_ms": elapsed_ms})
    return rows


def fingerprint(seed: int = SEED) -> dict:
    """@brief H09 环境指纹:结果附带可复现上下文"""
    return {"python": platform.python_version(),
            "platform": platform.platform(terse=True),
            "seed": seed, "samples": SAMPLES, "mapping": "yaoyang.yaml"}


def main():
    """@brief 打印对照表(B9 证据:DSL 零新代码,行数成本约为硬编码
    的映射声明行数;两条件逐样等价)"""
    print(f"环境指纹:{fingerprint()}")
    rows = run()
    print("条件        样本  等价通过  映射行数  新代码行数  耗时 ms")
    for row in rows:
        print(f"{row['condition']:<9}  {row['samples']:>5}"
              f"  {row['checks_passed']:>7}/{row['checks_total']}"
              f"  {row['mapping_lines']:>7}  {row['new_code_lines']:>9}"
              f"  {row['elapsed_ms']:>8}")
    dsl, hard = rows[0], rows[1]
    print(f"\n结论:接入曜阳,DSL 条件业务侧改动 = {dsl['mapping_lines']} 行"
          f"映射声明 + 0 行代码;硬编码条件 = {hard['new_code_lines']} 行"
          f"新代码;两条件 {dsl['checks_passed']}/{dsl['checks_total']} "
          f"逐样等价。")


if __name__ == "__main__":
    main()
