#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@file    auth_availability_benchmark.py
@brief   B2 认证可用性基线(H10 §四):登录上下文重启存活(C08)与过期续签
         (C09)成功率、口令登录端到端时延;v2 扩展(13-R-IDP-2):套件迁移
         吞吐(对象/秒)与双写窗口写入开销比。离线一键复现,种子固定,
         数据表落 benchmarks/data/b2_auth_availability.csv。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import os
import random
import sys
import tempfile
import time

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "packages"))
sys.path.insert(0, REPO_ROOT)

from benchmarks.common import environment_fingerprint, write_table  # noqa: E402
from tests.idp_env import IdpEnv, TEST_IP, USER_ACCOUNT, USER_PASSWORD  # noqa: E402
from gd_crypto import encrypt_envelope  # noqa: E402
from gd_crypto.migrate import run_migration  # noqa: E402
from gd_crypto.suites import get_suite, SUITE_GM, SUITE_INTL  # noqa: E402

SEED = 20260720
LOGIN_ROUNDS = 20
RESTART_ROUNDS = 10
MIGRATE_OBJECTS = 40
DUAL_WRITE_ROUNDS = 30


def bench_login_latency(env: IdpEnv) -> float:
    """@brief 口令登录路径平均时延(毫秒)"""
    started = time.perf_counter()
    for _ in range(LOGIN_ROUNDS):
        outcome, _ = env.ctx.accounts.password_login_step(
            USER_ACCOUNT, USER_PASSWORD, env.ctx.profile, TEST_IP)
        assert outcome != "failed"
    return (time.perf_counter() - started) * 1000 / LOGIN_ROUNDS


def bench_context_survival(env: IdpEnv) -> float:
    """@brief C08 重启存活率:登录上下文令牌跨"重启"验签成功比例"""
    from gd_crypto import issue_context, verify_context
    survived = 0
    for _ in range(RESTART_ROUNDS):
        token = issue_context({"account": USER_ACCOUNT, "step": "password_ok"},
                              env.ctx.ring.current_key, env.ctx.suite,
                              ttl_seconds=600)
        env.restart()
        try:
            verify_context(token, env.ctx.ring.current_key, env.ctx.suite)
            survived += 1
        except Exception:  # noqa: BLE001 基准统计:任何异常计为不存活
            pass
    return survived / RESTART_ROUNDS


def bench_migration_throughput(env: IdpEnv) -> float:
    """@brief 套件迁移吞吐(对象/秒):intl 播种 → 迁 gm 计时(13-R-IDP-2)"""
    for i in range(MIGRATE_OBJECTS):
        env.ctx.accounts.create_user(f"b2u{i}", f"基准{i}", USER_PASSWORD,
                                     env.ctx.profile, "bench", TEST_IP,
                                     phone=f"1380000{i:04d}", force_change=False)
    started = time.perf_counter()
    run_migration(env.ctx.db, env.ctx.ring, get_suite(SUITE_GM), env.ctx.audit,
                  state_file=tempfile.mktemp(suffix=".json"))
    elapsed = time.perf_counter() - started
    return MIGRATE_OBJECTS / elapsed


def bench_dual_write_overhead(env: IdpEnv) -> float:
    """@brief 双写窗口写入开销比(dual/单写耗时,13-R-IDP-2)"""
    suite = get_suite(SUITE_INTL)
    payload = b"13800001111"
    started = time.perf_counter()
    for _ in range(DUAL_WRITE_ROUNDS):
        encrypt_envelope(payload, env.ctx.ring, suite, environ={})
    single = time.perf_counter() - started
    started = time.perf_counter()
    for _ in range(DUAL_WRITE_ROUNDS):
        encrypt_envelope(payload, env.ctx.ring, suite,
                         environ={"CRYPTO_DUAL_WRITE": SUITE_GM})
    dual = time.perf_counter() - started
    return dual / single


def main():
    """@brief 运行 B2 全部维度并落数据表"""
    random.seed(SEED)
    env = IdpEnv()
    env.seed_admin_and_user()
    rows = [
        ("password_login_latency_ms", f"{bench_login_latency(env):.1f}"),
        ("context_restart_survival_rate", f"{bench_context_survival(env):.2f}"),
        ("suite_migration_throughput_obj_per_s",
         f"{bench_migration_throughput(env):.1f}"),
        ("dual_write_overhead_ratio", f"{bench_dual_write_overhead(env):.2f}"),
    ]
    env.close()
    path = write_table("b2_auth_availability", ["metric", "value"], rows,
                       environment_fingerprint(SEED))
    print(f"B2 数据表已落盘: {path}")
    for metric, value in rows:
        print(f"  {metric} = {value}")


if __name__ == "__main__":
    main()
