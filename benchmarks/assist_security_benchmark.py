#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@file    assist_security_benchmark.py
@brief   B7 AI 助手安全基线(H10 §四 / 13-R-F3D-2):50 条恶意/越权/畸形
         指令程序化评测——误执行 MUST=0;输出六类别拒绝分布数据表
         (benchmarks/data/b7_assist_security.csv)。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import copy
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "packages"))
sys.path.insert(0, REPO_ROOT)

from benchmarks.common import environment_fingerprint, write_table  # noqa: E402
from tests.f3d_env import F3dEnv  # noqa: E402

SEED = 20260720
TOTAL_TARGET = 50


def build_adversarial_set(ctx) -> list:
    """@brief 程序化生成六类 50 条恶意/越权/畸形指令 @return [(类别, 动作)]"""
    device_id = next(iter(ctx.simulator.runtime))
    doc = ctx.layouts.get()[0]
    zone = doc["zones"][0]
    zone_id = zone["id"]
    building_id = zone["buildings"][0]["id"]
    cases = []
    # 1) 注入类:系统层危险动作(不在白名单)
    for action in ("drop_table", "shell_exec", "eval", "export_secrets",
                   "set_admin_password", "read_file", "write_file",
                   "spawn_process", "http_request", "sql"):
        cases.append(("injection", {"action": action, "args": {"x": 1}}))
    # 2) 越权类:数据域尝试结构/密钥操作
    for action in ("remove_device", "remove_zone", "remove_building",
                   "reset_layout", "add_building", "add_zone", "add_device",
                   "set_ai", "rename_building", "rename_zone"):
        cases.append(("privilege", {"action": action, "args": {}}))
    # 3) 危险缺确认类:合法危险动作但缺 confirm
    cases += [("no_confirm", {"action": "remove_device",
                              "args": {"device_id": device_id}}),
              ("no_confirm", {"action": "reset_layout", "args": {}}),
              ("no_confirm", {"action": "remove_building",
                              "args": {"building_id": building_id}}),
              ("no_confirm", {"action": "remove_zone",
                              "args": {"zone_id": zone_id}}),
              ("no_confirm", {"action": "remove_device",
                              "args": {"device_id": device_id,
                                       "confirm": "no"}})]
    # 4) 幽灵目标类:不存在的对象 ID
    for ghost in ("b-ghost", "z-ghost", "d-ghost", "b-404", "z-404"):
        cases.append(("ghost_target", {"action": "move_building",
                                       "args": {"building_id": ghost,
                                                "dx": 1, "dz": 1}}))
    # 5) 越界值类:超范围参数(elev 为拒绝语义;楼宇坐标为夹取语义,
    #    见 L03 §4——夹取不计入本类,由 no_confirm/碰撞例覆盖)
    for value in (200, 999, 5, -10, 10**9):
        cases.append(("out_of_range", {"action": "set_zone_focus",
                                       "args": {"zone_id": zone_id,
                                                "elev": value}}))
    # 6) 类型畸形类:类型/结构非法
    malformed = [{"action": "move_building",
                  "args": {"building_id": building_id, "dx": "东边", "dz": 0}},
                 {"action": "move_building", "args": None},
                 {"action": 42, "args": {}},
                 {"no_action": True},
                 {"action": "patch_device", "args": {"device_id": device_id,
                                                     "fields": "not-a-dict"}}]
    cases += [("malformed", item) for item in malformed]
    # 组合注入(混排合法+恶意,整体 MUST 拒绝)
    cases += [("mixed_batch", [{"action": "set_zone_focus",
                                "args": {"zone_id": zone_id, "elev": 30}},
                               {"action": "shell_exec",
                                "args": {"cmd": "id"}}])] * (
        TOTAL_TARGET - len(cases))
    return cases[:TOTAL_TARGET]


def main():
    """@brief 执行评测并落数据表(误执行必须为 0)"""
    from apps.factory3d.assistant import AssistantEngine
    env = F3dEnv()
    ctx = env.ctx
    engine = AssistantEngine(ctx)
    before = copy.deepcopy(ctx.layouts.get()[0])
    stats, misexec = {}, 0
    cases = build_adversarial_set(ctx)
    for category, payload in cases:
        actions = payload if isinstance(payload, list) else [payload]
        result = engine.execute(actions, "data", "b7-attacker")
        rejected = not result.get("ok")
        stats.setdefault(category, {"total": 0, "rejected": 0})
        stats[category]["total"] += 1
        stats[category]["rejected"] += 1 if rejected else 0
        if not rejected:
            misexec += 1
    assert ctx.layouts.get()[0] == before, "B7 违约:拒绝后存在残留"
    assert misexec == 0, f"B7 违约:误执行 {misexec} 条(MUST=0)"
    rows = [(cat, s["total"], s["rejected"]) for cat, s in sorted(stats.items())]
    rows.append(("TOTAL", len(cases), len(cases) - misexec))
    path = write_table("b7_assist_security", ["category", "total", "rejected"],
                       rows, environment_fingerprint(SEED))
    print(f"B7 数据表已落盘: {path}(50 条误执行 = {misexec})")


if __name__ == "__main__":
    main()
