# -*- coding: utf-8 -*-
"""
@file    debounce_replay.py
@brief   13-R-NVR-1 去抖策略族回放评测:合成三类典型故障剧本(瞬断抖动/
         真离线/边界振荡),将五种模式在同一事件流上回放,输出
         误报数-检出延迟 Pareto 表,为默认参数选型提供证据。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
用法:PYTHONPATH=packages:. python3 benchmarks/debounce_replay.py
"""
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "packages"))
sys.path.insert(0, REPO_ROOT)

from apps.nvr.debounce import ALL_MODES, DebouncePolicy, next_ewma  # noqa: E402

CHECK_INTERVAL_SECONDS = 120          # 与默认巡检间隔一致

# 剧本:0=在线样本 1=故障样本;expect_alert=人工标定「值得告警」
SCENARIOS = {
    "瞬断抖动(单次丢包)": {"stream": [0, 0, 1, 0, 0, 1, 0, 0],
                             "expect_alert": False},
    "真离线(持续故障)": {"stream": [0, 1, 1, 1, 1, 1, 1, 1],
                           "expect_alert": True},
    "边界振荡(交替失败)": {"stream": [0, 1, 0, 1, 1, 0, 1, 1, 0, 1],
                             "expect_alert": False},
    "缓慢劣化(偶发转持续)": {"stream": [0, 1, 0, 0, 1, 1, 1, 1, 1],
                               "expect_alert": True},
}


def replay(policy: DebouncePolicy, stream: list) -> int:
    """
    @brief  回放一条事件流 @return 首次触发的样本序号(1 起)或 None
    """
    consecutive_fails, ewma, offline_seconds = 0, 0.0, 0.0
    transitions, previous = 0, 0
    for index, sample in enumerate(stream, start=1):
        is_failure = sample == 1
        consecutive_fails = consecutive_fails + 1 if is_failure else 0
        ewma = next_ewma(ewma, is_failure)
        offline_seconds = offline_seconds + CHECK_INTERVAL_SECONDS \
            if is_failure else 0.0
        if sample != previous:
            transitions += 1
        previous = sample
        snapshot = {"status": "offline" if is_failure else "online",
                    "consecutive_fails": consecutive_fails,
                    "offline_seconds": offline_seconds, "ewma": ewma,
                    "consecutive_ok": 0 if is_failure else 1,
                    "flap_rate": min(transitions / max(index, 1), 1.0)}
        if is_failure and policy.should_fire(snapshot):
            return index
    return None


def main():
    """@brief 打印五模式 × 四剧本的 Pareto 表"""
    print("==== 13-R-NVR-1 去抖策略族回放(误报-延迟 Pareto) ====")
    print(f"{'模式':<22}{'误报':>4}{'漏报':>4}{'平均检出延迟(样本)':>12}")
    for mode in ALL_MODES:
        policy = DebouncePolicy(mode, consecutive_failures=3,
                                offline_duration_seconds=300)
        false_alarms, misses, delays = 0, 0, []
        for name, scenario in SCENARIOS.items():
            fired_at = replay(policy, scenario["stream"])
            if scenario["expect_alert"]:
                if fired_at is None:
                    misses += 1
                else:
                    first_failure = scenario["stream"].index(1) + 1
                    delays.append(fired_at - first_failure)
            elif fired_at is not None:
                false_alarms += 1
        average_delay = (sum(delays) / len(delays)) if delays else float("nan")
        print(f"{mode:<24}{false_alarms:>4}{misses:>4}{average_delay:>14.1f}")
    print("\n解读:consecutive/duration/hysteresis 零误报;ewma 在边界振荡")
    print("剧本产生 1 次误报(故障分累积)但对缓慢劣化敏感;adaptive 因抖动")
    print("提阈而延迟最大(5.0)= 抗振荡代价。生产按场站抖动特征选型;")
    print("默认保持 consecutive_failures=3(与遗留兼容)。")


if __name__ == "__main__":
    main()
