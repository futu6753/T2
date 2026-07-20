#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@file    mode_switch_benchmark.py
@brief   B4 演示⇄生产切换基线(H10 §四):恢复清单执行耗时、清单项计数、
         自检结论,并输出一份自检报告样例(论文/交底书素材)。
         离线一键复现,数据表落 benchmarks/data/b4_mode_switch.csv。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import json
import os
import sys
import time

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "packages"))
sys.path.insert(0, REPO_ROOT)

from benchmarks.common import environment_fingerprint, write_table  # noqa: E402
from tests.idp_env import IdpEnv, TEST_IP  # noqa: E402
from apps.idp.mode import ModeService  # noqa: E402

SEED = 20260720
ROUNDS = 3


def one_round(index: int) -> dict:
    """@brief 单轮:DEMO 经覆盖层开启(热切形态)→ 切生产计时 → 采集报告"""
    env = IdpEnv()
    env.seed_admin_and_user()
    env.ctx.settings.set_override("demo_mode", True, "system", TEST_IP)
    env.ctx.refresh_profile()
    env.ctx.accounts.seed_demo_accounts(env.ctx.profile, TEST_IP)
    started = time.perf_counter()
    report = ModeService(env.ctx).switch_to_prod("op_admin", TEST_IP)
    elapsed_ms = (time.perf_counter() - started) * 1000
    env.close()
    return {"round": index, "elapsed_ms": elapsed_ms, "report": report}


def main():
    """@brief 三轮切换取样并落数据表 + 自检报告样例"""
    rows, sample = [], None
    for index in range(1, ROUNDS + 1):
        outcome = one_round(index)
        sample = outcome["report"]
        rows.append((outcome["round"], f"{outcome['elapsed_ms']:.1f}",
                     sample.get("demo_accounts_disabled", 0),
                     sample.get("demo_sessions_revoked", 0),
                     sample.get("selfcheck", "")))
    path = write_table(
        "b4_mode_switch",
        ["round", "switch_elapsed_ms", "demo_accounts_disabled",
         "demo_sessions_revoked", "selfcheck"],
        rows, environment_fingerprint(SEED))
    print(f"B4 数据表已落盘: {path}")
    sample_path = os.path.join(os.path.dirname(path), "b4_selfcheck_sample.json")
    with open(sample_path, "w", encoding="utf-8") as handle:
        json.dump(sample, handle, ensure_ascii=False, indent=2)
    print(f"自检报告样例: {sample_path}")


if __name__ == "__main__":
    main()
