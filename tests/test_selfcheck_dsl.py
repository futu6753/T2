# -*- coding: utf-8 -*-
"""
@file    test_selfcheck_dsl.py
@brief   自检 DSL 回归(13-R-IDP-3 / H09 §二 B、K):D1–D9 成对断言由同一份 DSL
         生成并与 selfcheck_prod 同源;改 DSL 即改测试(单一事实来源)。
         测试名 test_r_idp3_dsl 为 H09 K.2 固定契约,MUST NOT 改名。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import copy
import unittest

from tests.base import make_temp_db

from gd_policy import resolve_profile, SettingsService
from selfcheck.registry import (
    EXPECTED_ITEM_IDS,
    LEVEL_HTTP,
    load_items,
    PHASE_DEMO,
    PHASE_PROD,
    run_profile_assertions,
)


def _profile_for(db, is_demo: bool):
    """@brief 构造指定模式的生效策略快照"""
    environ = {"DEMO_MODE": "1"} if is_demo else {}
    settings = SettingsService(db, environ=environ)
    return resolve_profile(settings, environ=environ)


class TestSelfCheckDsl(unittest.TestCase):
    """H05 §4 声明式自检 DSL。"""

    def setUp(self):
        self.db = make_temp_db()
        self.items = load_items()

    def tearDown(self):
        self.db.close()

    def test_r_idp3_dsl(self):
        """D1–D9 全量声明;DEMO 生效断言与生产失效断言成对全绿(同源生成)"""
        found_ids = {item["id"] for item in self.items}
        for item_id in EXPECTED_ITEM_IDS:
            self.assertIn(item_id, found_ids)
        demo_profile = _profile_for(self.db, is_demo=True)
        _, demo_failures, _ = run_profile_assertions(demo_profile, PHASE_DEMO, self.items)
        self.assertEqual(demo_failures, [], "DEMO 生效断言存在失败项")
        prod_profile = _profile_for(self.db, is_demo=False)
        _, prod_failures, pending = run_profile_assertions(prod_profile, PHASE_PROD,
                                                           self.items)
        self.assertEqual(prod_failures, [], "生产失效断言存在失败项")
        # GAP-02 已解除:http 级断言全部激活,不允许再有待落地项
        self.assertEqual(pending, [], "http 级自检不得再有 pending 项(GAP-02 已解除)")

    def test_r_idp3_http_assertions_demo_and_prod(self):
        """D4/D5 端点行为成对断言(进程内 ASGI 执行,与 selfcheck_prod 同源)"""
        from tests.idp_env import IdpEnv
        from selfcheck.registry import run_http_assertions
        demo = IdpEnv(is_demo=True)
        try:
            _, failures = run_http_assertions(demo.client(), PHASE_DEMO, self.items)
            self.assertEqual(failures, [], "DEMO 态 http 断言失败")
        finally:
            demo.close()
        prod = IdpEnv(is_demo=False)
        try:
            _, failures = run_http_assertions(prod.client(), PHASE_PROD, self.items)
            self.assertEqual(failures, [], "生产态 http 断言失败")
        finally:
            prod.close()

    def test_r_idp3_dsl_single_source(self):
        """改 DSL 期望即改测试判定(证明测试与清单不会脱节)"""
        mutated = copy.deepcopy(self.items)
        for item in mutated:
            if item["id"] == "D8":
                item["prod_expect"] = 999   # 人为制造与实现不符的声明
        prod_profile = _profile_for(self.db, is_demo=False)
        _, failures, _ = run_profile_assertions(prod_profile, PHASE_PROD, mutated)
        self.assertEqual([entry["id"] for entry in failures], ["D8"])

    def test_r_idp3_pairs_are_distinguishing(self):
        """每个已落地项的 demo/prod 期望值必须不同(否则成对断言无意义)"""
        for item in self.items:
            if item["level"] != LEVEL_HTTP:
                self.assertNotEqual(item["demo_expect"], item["prod_expect"],
                                    f"{item['id']} 成对期望相同")


if __name__ == "__main__":
    unittest.main()
