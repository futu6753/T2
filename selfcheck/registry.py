# -*- coding: utf-8 -*-
"""
@file    registry.py
@brief   自检检查函数注册表与成对断言执行器(H05 §4 / 13-R-IDP-3):
         demo_items.yaml 的每个 check id 在此登记观测函数;
         selfcheck_prod 与回归测试共用本执行器(同一份 DSL,同一套函数)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import os

import yaml

from gd_common.errors import ConfigError
from gd_policy.profile import is_test_code_accepted, SecurityProfile, TEST_TOTP_SMS_CODE

DSL_FILE = os.path.join(os.path.dirname(__file__), "demo_items.yaml")
LEVEL_PROFILE = "profile"
LEVEL_HTTP = "http"
PHASE_DEMO = "demo"
PHASE_PROD = "prod"
EXPECTED_ITEM_IDS = tuple(f"D{i}" for i in range(1, 10))   # D1–D9 全量(H05 §2)

# 检查函数注册表:check id → 观测函数(profile → 观测值)
PROFILE_CHECKS = {
    "seed_accounts_enabled": lambda p: p.seed_accounts_enabled,
    "test_code_123456_accepted": lambda p: is_test_code_accepted(p, TEST_TOTP_SMS_CODE),
    "sms_echo_enabled": lambda p: p.sms_echo_enabled,
    "cookie_secure_required": lambda p: p.cookie_secure_required,
    "enforce_password_age": lambda p: p.enforce_password_age,
    "rate_multiplier": lambda p: p.rate_multiplier,
    "banner_text": lambda p: p.banner_text,
}

# http 级检查:check id → (client → 观测状态码);client 为进程内 ASGI 客户端,
# selfcheck_prod 与回归测试同源执行(GAP-02 已解除)
HTTP_CHECKS = {
    "http_cert_demo_status": lambda client: client.get("/login/cert-demo").status_code,
    "http_wechat_mock_status": lambda client: client.get("/wx/scan").status_code,
}


def run_http_assertions(client, phase: str, items: list = None) -> tuple:
    """
    @brief  执行 http 级成对断言的指定相位(D4/D5 端点行为,05 §2)
    @param  client 进程内 ASGI 客户端(selfcheck.asgi.AsgiClient)
    @param  phase  PHASE_DEMO | PHASE_PROD
    @return (results, failures)
    """
    if phase not in (PHASE_DEMO, PHASE_PROD):
        raise ConfigError(f"非法自检相位: {phase}")
    items = load_items() if items is None else items
    results, failures = [], []
    for item in items:
        if item["level"] != LEVEL_HTTP:
            continue
        if item["check"] not in HTTP_CHECKS:
            raise ConfigError(f"http 检查函数 {item['check']} 未登记")
        observed = HTTP_CHECKS[item["check"]](client)
        expected = item[f"{phase}_expect"]
        entry = {"id": item["id"], "name": item["name"], "expected": expected,
                 "observed": observed, "ok": observed == expected}
        results.append(entry)
        if not entry["ok"]:
            failures.append(entry)
    return results, failures


def load_items(path: str = DSL_FILE) -> list:
    """
    @brief  装载并结构校验 DSL 条目(缺 D 项 / 未登记 check / 缺成对期望即报错)
    @return 条目列表
    """
    with open(path, "r", encoding="utf-8") as handle:
        items = yaml.safe_load(handle)
    if not isinstance(items, list):
        raise ConfigError("demo_items.yaml 顶层必须是条目列表")
    found_ids = {item.get("id") for item in items}
    missing = [item_id for item_id in EXPECTED_ITEM_IDS if item_id not in found_ids]
    if missing:
        raise ConfigError(f"自检 DSL 缺少简化项声明: {missing}(D1–D9 必须全量)")
    for item in items:
        for field in ("id", "name", "level", "check", "demo_expect", "prod_expect"):
            if field not in item:
                raise ConfigError(f"自检 DSL 条目 {item.get('id')} 缺字段 {field}")
        registry = PROFILE_CHECKS if item["level"] == LEVEL_PROFILE else HTTP_CHECKS
        if item["check"] not in registry:
            raise ConfigError(f"自检 DSL 条目 {item['id']} 的检查函数 {item['check']} 未登记")
    return items


def run_profile_assertions(profile: SecurityProfile, phase: str,
                           items: list = None) -> tuple:
    """
    @brief  执行 profile 级成对断言的指定相位(demo 生效断言 / prod 失效断言)
    @param  profile 生效策略快照
    @param  phase   PHASE_DEMO | PHASE_PROD
    @param  items   DSL 条目(缺省从文件装载)
    @return (results, failures, pending):逐项结果、失败清单、待落地清单(GAP)
    """
    if phase not in (PHASE_DEMO, PHASE_PROD):
        raise ConfigError(f"非法自检相位: {phase}")
    items = load_items() if items is None else items
    results, failures, pending = [], [], []
    for item in items:
        if item["level"] != LEVEL_PROFILE:
            # http 级条目由 run_http_assertions 执行(GAP-02 已解除,不再计 pending);
            # 仍标注 pending 字段的条目视为未落地项保留上报
            if item.get("pending"):
                pending.append({"id": item["id"], "name": item["name"],
                                "gap": item["pending"]})
            continue
        observed = PROFILE_CHECKS[item["check"]](profile)
        expected = item[f"{phase}_expect"]
        is_ok = observed == expected
        entry = {"id": item["id"], "name": item["name"], "expected": expected,
                 "observed": observed, "ok": is_ok}
        results.append(entry)
        if not is_ok:
            failures.append(entry)
    return results, failures, pending
