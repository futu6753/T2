# -*- coding: utf-8 -*-
"""
@file    f3d_scale_benchmark.py
@brief   13-R-F3D-4 规模适应性基准:合成 22 / 50 / 100 / 200 台布局,
         度量 WS 全量帧字节数与构建耗时,产出规模-帧大小数据表
         (只提交脚本,不提交产物;22 台 <5KB/帧为 L03 §1 预算锚点)。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
用法:PYTHONPATH=packages:. python3 benchmarks/f3d_scale_benchmark.py
"""
import json
import os
import sys
import time

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "packages"))
sys.path.insert(0, REPO_ROOT)

from apps.factory3d.simulator import Simulator, synthetic_layout  # noqa: E402

SCALES = (22, 50, 100, 200)
BUDGET_22_BYTES = 5 * 1024      # L03 §1:22 台全量一帧 <5KB


def build_frame(simulator: Simulator, data_rev: int) -> dict:
    """@brief 构建与 stream.build_frame 同构的全量帧(离线可复现,无 ctx)"""
    kpi = simulator.kpi()
    kpi["alarm"] = 0
    return {"type": "update", "ver": "5.0.0-m6", "data_rev": data_rev,
            "site": "云枢智造产业园", "tier": "full", "min_icon_px": 24,
            "kpi": kpi, "alarms": {"counts": {"active": 0, "pending": 0,
                                              "acked": 0}, "active": []},
            "devices": simulator.snapshot(), "events": []}


def measure(total_devices: int, ticks: int = 5) -> dict:
    """@brief 单档度量 @return {devices, frame_bytes, build_ms}"""
    simulator = Simulator(synthetic_layout(total_devices))
    for step in range(ticks):
        simulator.tick(now=1000.0 + step)
    started = time.perf_counter()
    frame = build_frame(simulator, data_rev=1)
    payload = json.dumps(frame, ensure_ascii=False).encode()
    elapsed_ms = (time.perf_counter() - started) * 1000
    return {"devices": total_devices, "frame_bytes": len(payload),
            "build_ms": round(elapsed_ms, 3)}


def run() -> list:
    """@brief 四档全量度量 @return 数据表"""
    return [measure(scale) for scale in SCALES]


def main():
    """@brief 打印规模-帧大小数据表(R-F3D-4 证据)"""
    rows = run()
    print("规模(台)  帧大小(B)  构建耗时(ms)")
    for row in rows:
        print(f"{row['devices']:>7}  {row['frame_bytes']:>9}"
              f"  {row['build_ms']:>10}")
    baseline = rows[0]
    print(f"\n预算检查:22 台帧 {baseline['frame_bytes']} B"
          f" {'≤' if baseline['frame_bytes'] <= BUDGET_22_BYTES else '>'}"
          f" {BUDGET_22_BYTES} B(L03 §1)")


if __name__ == "__main__":
    main()
