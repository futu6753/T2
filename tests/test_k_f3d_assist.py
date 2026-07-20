# -*- coding: utf-8 -*-
"""
@file    test_k_f3d_assist.py
@brief   M6 主验收(其三):AI 配置助手 API 流(preview 零落库 → tx_id 确认 →
         原子执行单次 data_rev)、危险动作 confirm、编辑域会话锁 session_locked、
         set_ai 密钥防线、事务日志(L03 §5 / 13-R-F3D-2 契约面)。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import json
import unittest

from tests.f3d_env import F3dEnv
from tests.test_k_f3d import jpost


def wrap(actions: list) -> str:
    """@brief 模拟模型回复:寒暄 + 末尾 JSON 动作块(L03 §5 输出协议)"""
    payload = json.dumps({"actions": actions}, ensure_ascii=False)
    return f"好的,这就为您办理。\n```json\n{payload}\n```"


class F3dAssistantApiTest(unittest.TestCase):
    """助手三步流的 HTTP 契约面。"""

    def setUp(self):
        """@brief 环境 + 已登录客户端"""
        self.env = F3dEnv()
        self.user = self.env.logged_in()

    def test_preview_then_execute_atomic_single_rev(self):
        """preview 零落库出差异;确认后多结构动作原子提交,data_rev 仅 +1"""
        doc = json.loads(self.user.get("/api/layout").body)["layout"]
        zone_id = doc["zones"][0]["id"]
        text = wrap([
            {"action": "set_site_name", "args": {"name": "东岸智造基地"}},
            {"action": "add_building",
             "args": {"zone_id": zone_id, "name": "研发楼", "type": "office"}},
            {"action": "set_alarm", "args": {"delay_min": 2.5}},
        ])
        preview = jpost(self.user, "/api/assistant/data/preview",
                        {"text": text})
        self.assertEqual(preview.status_code, 200, preview.body)
        result = json.loads(preview.body)
        self.assertEqual(result["actions"], 3)
        self.assertEqual(len(result["diffs"]), 3)
        # dry-run 零落库
        after_preview = json.loads(self.user.get("/api/layout").body)
        self.assertEqual(after_preview["data_rev"], 0)
        self.assertEqual(len(after_preview["layout"]["zones"][0]["buildings"]),
                         4)
        self.assertEqual(self.env.ctx.settings.get("f3d_site_name"),
                         "云枢智造产业园")
        done = jpost(self.user, "/api/assistant/data/execute",
                     {"tx_id": result["tx_id"]})
        self.assertEqual(done.status_code, 200, done.body)
        self.assertEqual(json.loads(done.body)["data_rev"], 1)   # 单次 +1
        final = json.loads(self.user.get("/api/layout").body)
        self.assertEqual(len(final["layout"]["zones"][0]["buildings"]), 5)
        self.assertEqual(self.env.ctx.settings.get("f3d_site_name"),
                         "东岸智造基地")
        self.assertEqual(
            self.env.ctx.settings.get("f3d_alarm_delay_minutes"), 2.5)
        # 重复确认同一 tx_id → 拒绝(一次性)
        again = jpost(self.user, "/api/assistant/data/execute",
                      {"tx_id": result["tx_id"]})
        self.assertEqual(again.status_code, 400)

    def test_danger_needs_confirm_and_edit_scope_lock(self):
        """危险动作缺 confirm → 拒;带 confirm → 过;edit 域无会话 → session_locked"""
        doc = json.loads(self.user.get("/api/layout").body)["layout"]
        device_id = doc["outdoor"][0]["id"]
        naked = jpost(self.user, "/api/assistant/data/preview",
                      {"text": wrap([{"action": "remove_device",
                                      "args": {"device_id": device_id}}])})
        self.assertEqual(naked.status_code, 400)
        self.assertIn("confirm", json.loads(naked.body)["error"])
        confirmed = jpost(self.user, "/api/assistant/data/preview",
                          {"text": wrap([{"action": "remove_device",
                                          "args": {"device_id": device_id,
                                                   "confirm": True}}])})
        self.assertEqual(confirmed.status_code, 200)
        locked = jpost(self.user, "/api/assistant/edit/preview",
                       {"text": wrap([{"action": "set_home",
                                       "args": {"elev": 60}}])})
        self.assertEqual(locked.status_code, 400)
        self.assertIn("session_locked", json.loads(locked.body)["error"])
        jpost(self.user, "/api/edit/session", {"active": True})
        unlocked = jpost(self.user, "/api/assistant/edit/preview",
                         {"text": wrap([{"action": "set_home",
                                         "args": {"elev": 60}}])})
        self.assertEqual(unlocked.status_code, 200)

    def test_set_ai_key_guard_and_tx_log(self):
        """set_ai 含 api_key 一律拒绝;事务日志记录 rejected/dry_run/executed"""
        stolen = jpost(self.user, "/api/assistant/data/preview",
                       {"text": wrap([{"action": "set_ai",
                                       "args": {"enabled": True,
                                                "api_key": "sk-x"}}])})
        self.assertEqual(stolen.status_code, 400)
        self.assertIn("API Key", json.loads(stolen.body)["error"])
        fine = json.loads(jpost(
            self.user, "/api/assistant/data/preview",
            {"text": wrap([{"action": "set_ai",
                            "args": {"enabled": False}}])}).body)
        jpost(self.user, "/api/assistant/data/execute",
              {"tx_id": fine["tx_id"]})
        self.assertFalse(self.env.ctx.settings.get("f3d_assistant_enabled"))
        # 助手停用后再提交 → 拒绝
        disabled = jpost(self.user, "/api/assistant/data/preview",
                         {"text": wrap([{"action": "set_ai",
                                         "args": {"enabled": True}}])})
        self.assertEqual(disabled.status_code, 400)
        log = json.loads(self.user.get("/api/assistant/log").body)["log"]
        phases = [entry["phase"] for entry in log]
        for phase in ("rejected", "dry_run", "executed"):
            self.assertIn(phase, phases)
        # 纯文本回复(无动作块)按 0 动作处理,不报错
        plain = jpost(self.user, "/api/assistant/data/preview",
                      {"text": "今天园区一切正常。"})
        self.assertEqual(plain.status_code, 200)
        self.assertEqual(json.loads(plain.body)["actions"], 0)


if __name__ == "__main__":
    unittest.main()
