# -*- coding: utf-8 -*-
"""
@file    test_r_f3d.py
@brief   研究项验收锚点(H09 K.2,测试名 MUST NOT 改):
         test_r_f3d1_degrade —— 降级阶梯低帧降档/高帧回升,档位事件可查;
         test_r_f3d2_tx —— 助手多动作原子事务:任一失败整体回滚零残留;
         test_r_f3d4_scale —— 22/50/100/200 规模基准数据表;
         另附 B7(50 条恶意/越权指令,误执行 MUST=0)与 dry-run 隔离补充。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import copy
import json
import unittest

from tests.f3d_env import F3dEnv

from apps.factory3d.render_ladder import TIERS
from benchmarks.f3d_scale_benchmark import BUDGET_22_BYTES, run as bench_run


def _feed(ladder, fps: float, times: int, base: float):
    """@brief 连续喂入同值 fps 采样"""
    for step in range(times):
        ladder.feed(fps, now=base + step)


class RF3d1DegradeTest(unittest.TestCase):
    """13-R-F3D-1 性能自适应降级阶梯。"""

    def setUp(self):
        self.env = F3dEnv()
        self.ladder = self.env.ctx.ladder

    def test_r_f3d1_degrade(self):
        """低 fps 连续越线逐档下沉;档位事件可查;末档降推送频率"""
        ladder = self.ladder
        self.assertEqual(ladder.tier_name(), "full")
        _feed(ladder, 15.0, 3, 100.0)            # 窗口=3:降一档
        self.assertEqual(ladder.tier_name(), "no_shadow")
        _feed(ladder, 15.0, 3, 110.0)
        self.assertEqual(ladder.tier_name(), "low_tex")
        _feed(ladder, 15.0, 3, 120.0)
        self.assertEqual(ladder.tier_name(), "low_push")
        _feed(ladder, 15.0, 6, 130.0)            # 已到底:不再下沉
        self.assertEqual(ladder.tier_name(), "low_push")
        self.assertGreater(ladder.push_interval(2.0), 2.0)   # 末档降推送
        transitions = [(event["from"], event["to"])
                       for event in ladder.events()]
        self.assertEqual(transitions, [("full", "no_shadow"),
                                       ("no_shadow", "low_tex"),
                                       ("low_tex", "low_push")])
        kinds = [event["kind"] for event in json.loads(
            self.env.client().get("/api/events").body)["events"]]
        self.assertIn("tier_change", kinds)      # 档位事件入统一事件流

    def test_r_f3d1_recovery_hysteresis(self):
        """滞回带内不切档;高帧连续越线单步回升至全效"""
        ladder = self.ladder
        _feed(ladder, 15.0, 6, 200.0)            # 沉两档 → low_tex
        self.assertEqual(ladder.tier_name(), "low_tex")
        _feed(ladder, 30.0, 10, 210.0)           # 24~45 滞回带:保持
        self.assertEqual(ladder.tier_name(), "low_tex")
        _feed(ladder, 60.0, 2, 230.0)
        _feed(ladder, 30.0, 1, 233.0)            # 连续被打断:计数清零
        self.assertEqual(ladder.tier_name(), "low_tex")
        _feed(ladder, 60.0, 3, 240.0)
        self.assertEqual(ladder.tier_name(), "no_shadow")
        _feed(ladder, 60.0, 3, 250.0)
        self.assertEqual(ladder.tier_name(), "full")
        self.assertEqual(ladder.push_interval(2.0), 2.0)
        # 阶梯可停用:停用后不再切档(设置热生效)
        self.env.ctx.settings.set_override("f3d_ladder_enabled", False,
                                           "tester", "0.0.0.0")
        _feed(ladder, 5.0, 6, 260.0)
        self.assertEqual(ladder.tier_name(), "full")


class RF3d2TxTest(unittest.TestCase):
    """13-R-F3D-2 助手动作事务化。"""

    def setUp(self):
        self.env = F3dEnv()
        self.ctx = self.env.ctx
        from apps.factory3d.assistant import AssistantEngine
        self.engine = AssistantEngine(self.ctx)
        doc, _ = self.ctx.layouts.get()
        self.zone_id = doc["zones"][0]["id"]
        self.buildings = doc["zones"][0]["buildings"]

    def _snapshot(self) -> tuple:
        """@brief 全量落库态快照(布局 + 关键设置)"""
        doc, rev = self.ctx.layouts.get()
        settings = {key: self.ctx.settings.get(key)
                    for key in ("f3d_site_name", "f3d_alarm_delay_minutes")}
        return copy.deepcopy(doc), rev, settings

    def test_r_f3d2_tx(self):
        """多动作含一失败 → 整体回滚零残留;全成功 → 原子提交单次 rev+1"""
        before = self._snapshot()
        target = self.buildings[0]
        clash = self.buildings[1]["offset"]
        failing = [
            {"action": "set_site_name", "args": {"name": "改名后的园区"}},
            {"action": "add_building",
             "args": {"zone_id": self.zone_id, "name": "新车间"}},
            {"action": "move_building",          # 与 2 号楼重叠 → 整笔失败
             "args": {"building_id": target["id"],
                      "dx": clash["dx"], "dz": clash["dz"]}},
        ]
        rejected = self.engine.execute(failing, "data", "tester")
        self.assertFalse(rejected["ok"])
        self.assertTrue(rejected["rolled_back"])
        self.assertIn("重叠", rejected["error"])
        self.assertEqual(self._snapshot(), before)   # 零残留
        succeeding = failing[:2] + [
            {"action": "set_alarm", "args": {"delay_min": 3}}]
        done = self.engine.execute(succeeding, "data", "tester")
        self.assertTrue(done["ok"], done)
        doc, rev, settings = self._snapshot()
        self.assertEqual(rev, before[1] + 1)         # 两结构动作单次 +1
        self.assertEqual(len(doc["zones"][0]["buildings"]), 5)
        self.assertEqual(settings["f3d_site_name"], "改名后的园区")
        self.assertEqual(settings["f3d_alarm_delay_minutes"], 3.0)

    def test_r_f3d2_dry_run_isolation(self):
        """preview(dry-run)在事务态执行,不落任何库"""
        before = self._snapshot()
        text = ('```json\n' + json.dumps({"actions": [
            {"action": "reset_layout", "args": {"confirm": True}},
            {"action": "set_site_name", "args": {"name": "试运行"}},
        ]}) + '\n```')
        preview = self.engine.preview(text, "data", "tester")
        self.assertTrue(preview["ok"])
        self.assertEqual(len(preview["diffs"]), 2)
        self.assertEqual(self._snapshot(), before)

    def test_r_f3d2_b7_adversarial_zero_misexec(self):
        """B7:50 条恶意/越权指令评测——误执行 MUST=0,confirm 触发正确"""
        device_id = next(iter(self.ctx.simulator.runtime))
        crafted = [
            {"action": "drop_table", "args": {"table": "f3d_layout"}},
            {"action": "shell_exec", "args": {"cmd": "rm -rf /"}},
            {"action": "set_admin_password", "args": {"value": "x"}},
            {"action": "export_secrets", "args": {}},
            {"action": "set_ai", "args": {"api_key": "sk-steal"}},
            {"action": "set_ai", "args": {}},
            {"action": "remove_device", "args": {"device_id": device_id}},
            {"action": "remove_zone", "args": {"zone_id": self.zone_id}},
            {"action": "remove_building",
             "args": {"building_id": self.buildings[0]["id"]}},
            {"action": "reset_layout", "args": {}},
            {"action": "reset_layout", "args": {"confirm": "yes"}},
            {"action": "remove_device",
             "args": {"device_id": device_id, "confirm": 1}},
            {"action": "set_zone_focus",
             "args": {"zone_id": self.zone_id, "elev": 5}},
            {"action": "set_zone_focus",
             "args": {"zone_id": self.zone_id, "elev": 200}},
            {"action": "move_building",
             "args": {"building_id": self.buildings[0]["id"],
                      "dx": self.buildings[1]["offset"]["dx"],
                      "dz": self.buildings[1]["offset"]["dz"]}},
            {"action": "move_building",
             "args": {"building_id": self.buildings[0]["id"], "dx": "东边",
                      "dz": 0}},
            {"action": "move_building",
             "args": {"building_id": "b-ghost", "dx": 0, "dz": 0}},
            {"action": "patch_device",
             "args": {"device_id": device_id, "fields": {"role": "admin"}}},
            {"action": "patch_device",
             "args": {"device_id": device_id,
                      "fields": {"password": "123456"}}},
            {"action": "patch_device", "args": {"device_id": device_id}},
            {"action": "add_device",
             "args": {"name": "x", "type": "quantum"}},
            {"action": "add_device", "args": {"name": "", "type": "plc"}},
            {"action": "add_device",
             "args": {"name": "长" * 61, "type": "plc"}},
            {"action": "add_building",
             "args": {"zone_id": "z-ghost", "name": "幽灵"}},
            {"action": "add_building",
             "args": {"zone_id": self.zone_id, "name": "厂", "type": "castle"}},
            {"action": "rename_zone",
             "args": {"zone_id": self.zone_id, "name": ""}},
            {"action": "rename_zone",
             "args": {"zone_id": self.zone_id, "name": "名" * 41}},
            {"action": "set_alarm", "args": {"delay_min": -1}},
            {"action": "set_alarm", "args": {"delay_min": 10000}},
            {"action": "set_alarm", "args": {"delay_min": "马上"}},
            {"action": "set_alarm", "args": {}},
            {"action": "set_display", "args": {"min_icon_px": 999}},
            {"action": "set_display", "args": {"min_icon_px": True}},
            {"action": "set_connection", "args": {"mode": "backdoor"}},
            {"action": "set_connection", "args": {"interval": 0.01}},
            {"action": "set_connection", "args": {}},
            {"action": "set_site_name", "args": {"name": ""}},
            {"action": "set_site_name", "args": {"name": "站" * 81}},
            {"action": "toggle_device", "args": {"device_id": "d-ghost"}},
            {"action": "toggle_device", "args": {}},
            {"action": "ack_alarm", "args": {}},
            {"action": "ack_alarm", "args": {"alarm_id": "一号"}},
            {"action": "set_home", "args": {"target": [1, 2]}},
            {"action": "set_home", "args": {"elev": 5}},
            "不是对象的动作",
            {"no_action_key": True},
            {"action": 42},
            {"action": "set_site_name", "args": "不是对象"},
        ]
        payloads = [[item] for item in crafted]
        payloads.append([])                                   # 空动作列表
        payloads.append([{"action": "set_site_name",          # 合法+非法混排
                          "args": {"name": "好名字"}},
                         {"action": "reset_layout", "args": {}}])
        self.assertGreaterEqual(len(payloads), 50)
        before = (copy.deepcopy(self.ctx.layouts.get()),
                  {key: self.ctx.settings.get(key)
                   for key in ("f3d_site_name", "f3d_alarm_delay_minutes",
                               "f3d_min_icon_px", "f3d_connection_mode",
                               "f3d_push_interval_seconds")})
        misexec = 0
        confirm_hits = 0
        for actions in payloads:
            result = self.engine.execute(actions, "data", "attacker")
            if result.get("ok"):
                misexec += 1
            elif "confirm" in result.get("error", ""):
                confirm_hits += 1
        self.assertEqual(misexec, 0)               # 误执行 MUST = 0(B7)
        self.assertEqual(confirm_hits, 7)          # 危险缺 confirm 全部命中
        # (单条 6 例 + 混排组合 1 例)
        after = (copy.deepcopy(self.ctx.layouts.get()),
                 {key: self.ctx.settings.get(key)
                  for key in before[1]})
        self.assertEqual(after, before)            # 全部拒绝零残留


class RF3d4ScaleTest(unittest.TestCase):
    """13-R-F3D-4 规模适应性。"""

    def test_r_f3d4_scale(self):
        """22/50/100/200 数据表:22 台守 <5KB 预算,帧大小随规模单调可控"""
        rows = bench_run()
        self.assertEqual([row["devices"] for row in rows],
                         [22, 50, 100, 200])
        self.assertLess(rows[0]["frame_bytes"], BUDGET_22_BYTES)
        sizes = [row["frame_bytes"] for row in rows]
        self.assertEqual(sizes, sorted(sizes))     # 单调递增
        # 线性可控:200 台帧不超过 22 台帧的 (200/22)*1.5 倍(无超线性爆炸)
        self.assertLess(sizes[-1], sizes[0] * (200 / 22) * 1.5)


if __name__ == "__main__":
    unittest.main()
