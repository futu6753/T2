# -*- coding: utf-8 -*-
"""
@file    test_k_f3d.py
@brief   M6 主验收(其一):鉴权矩阵/内容协商大屏/默认模板/布局 CRUD 与
         data_rev/移动重叠回弹/统一策略层 F3D 参数/编辑会话锁(L03 §2~§4/§7)。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import json
import unittest

from tests.f3d_env import EMERGENCY_TOKEN, F3dEnv

JSON_CT = "application/json"


def jpost(client, path, payload, method="POST"):
    """@brief 便捷 JSON 请求"""
    return client.request(method, path,
                          raw_body=json.dumps(payload).encode(),
                          content_type=JSON_CT)


class F3dCoreTest(unittest.TestCase):
    """大屏公开面 + 管理面契约。"""

    def setUp(self):
        """@brief 每用例独立环境"""
        self.env = F3dEnv()

    def test_auth_matrix_public_screen_admin_locked(self):
        """公开大屏 JSON 契约;/admin* 匿名 401;登录 200;token 应急通道"""
        anon = self.env.client()
        home = anon.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertTrue(json.loads(home.body)["public"])
        self.assertIn(anon.get("/admin/edit").status_code, (302, 401))
        self.assertIn(anon.get("/api/admin/layout").status_code, (302, 401))
        self.assertIn(
            anon.request("POST", "/api/devices/any/toggle").status_code,
            (302, 401))
        user = self.env.logged_in()
        self.assertEqual(user.get("/admin/edit").status_code, 200)
        via_token = anon.request(
            "GET", "/api/admin/layout",
            headers={"X-Admin-Token": EMERGENCY_TOKEN})
        self.assertEqual(via_token.status_code, 200)
        self.assertEqual(json.loads(via_token.body)["operator"],
                         "admin-token-channel")

    def test_big_screen_html_negotiation(self):
        """Accept: text/html → 数据壳大屏(F3D_VER 注入 + 关键元素齐备)"""
        page = self.env.client().get("/", headers={"Accept": "text/html"})
        self.assertEqual(page.status_code, 200)
        text = page.body.decode()
        for needle in ("window.F3D_VER", "云枢智造产业园", 'id="andon"',
                       'id="kpi-total"', 'id="event-list"', 'id="alarm-hud"',
                       'id="tier-chip"', 'id="layout-chip"', 'id="scene"'):
            self.assertIn(needle, text)

    def test_default_template_counts(self):
        """默认模板:1 场区 / 4 栋 / 23 台(21 楼内 + 2 室外);healthz 一致"""
        layout = json.loads(self.env.client().get("/api/layout").body)
        doc = layout["layout"]
        self.assertEqual(len(doc["zones"]), 1)
        self.assertEqual(len(doc["zones"][0]["buildings"]), 4)
        indoor = sum(len(b["devices"]) for b in doc["zones"][0]["buildings"])
        self.assertEqual(indoor, 21)
        self.assertEqual(len(doc["outdoor"]), 2)
        health = json.loads(self.env.client().get("/healthz").body)
        self.assertEqual(health["devices"], 23)
        self.assertEqual(health["version"], "5.0.0-m6")
        self.assertEqual(layout["data_rev"], 0)

    def test_layout_crud_data_rev_and_cascade(self):
        """结构 CRUD:rev 递增;删除楼宇级联清设备;不存在对象人话 400"""
        user = self.env.logged_in()
        made = json.loads(jpost(user, "/api/data/zones",
                                {"name": "二期场区"}).body)
        self.assertEqual(made["data_rev"], 1)
        doc = json.loads(user.get("/api/layout").body)["layout"]
        zone_id = doc["zones"][1]["id"]
        made = json.loads(jpost(
            user, f"/api/data/zones/{zone_id}/buildings",
            {"name": "试制车间", "type": "assembly"}).body)
        self.assertEqual(made["data_rev"], 2)
        doc = json.loads(user.get("/api/layout").body)["layout"]
        building_id = doc["zones"][1]["buildings"][0]["id"]
        made = json.loads(jpost(
            user, f"/api/data/buildings/{building_id}/devices",
            {"name": "试制-PLC-01", "type": "plc"}).body)
        self.assertEqual(made["data_rev"], 3)
        self.assertEqual(
            json.loads(self.env.client().get("/healthz").body)["devices"], 24)
        bad = jpost(user, "/api/data/devices/no-such", {"name": "x"},
                    method="PATCH")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("不存在", json.loads(bad.body)["detail"])
        gone = user.request("DELETE", f"/api/data/buildings/{building_id}")
        self.assertEqual(json.loads(gone.body)["data_rev"], 4)
        self.assertEqual(
            json.loads(self.env.client().get("/healthz").body)["devices"], 23)

    def test_move_building_overlap_rejects_and_rolls_back(self):
        """移动重叠 → 400 回弹(offset 不变);合法移动落库 rev+1"""
        user = self.env.logged_in()
        doc = json.loads(user.get("/api/layout").body)["layout"]
        first, second = doc["zones"][0]["buildings"][:2]
        clash = jpost(user, f"/api/data/buildings/{first['id']}",
                      {"dx": second["offset"]["dx"],
                       "dz": second["offset"]["dz"]}, method="PATCH")
        self.assertEqual(clash.status_code, 400)
        self.assertIn("重叠", json.loads(clash.body)["detail"])
        after = json.loads(user.get("/api/layout").body)
        kept = after["layout"]["zones"][0]["buildings"][0]["offset"]
        self.assertEqual(kept, first["offset"])
        self.assertEqual(after["data_rev"], 0)
        legal = jpost(user, f"/api/data/buildings/{first['id']}",
                      {"dx": -120, "dz": -120}, method="PATCH")
        self.assertEqual(json.loads(legal.body)["data_rev"], 1)

    def test_settings_view_patch_and_error_semantics(self):
        """F3D 参数:视图仅 f3d_ 键;PATCH 逐键校验(未知键/越界/非 f3d 拒绝);
        float 参数热生效(告警延时立即影响状态机)"""
        user = self.env.logged_in()
        view = json.loads(user.get("/api/data/settings").body)
        keys = {item["key"] for item in view["settings"]}
        self.assertEqual(len(keys), 14)
        self.assertTrue(all(key.startswith("f3d_") for key in keys))
        flag = next(item for item in view["settings"]
                    if item["key"] == "f3d_delta_sync_enabled")
        self.assertFalse(flag["value"])          # P2 flag 默认关(H09 K.3)
        bad = jpost(user, "/api/data/settings",
                    {"values": {"f3d_alarm_delay_minutes": 999,
                                "no_such_key": 1,
                                "nvr_patrol_interval_seconds": 60}},
                    method="PATCH")
        self.assertEqual(bad.status_code, 400)
        errors = json.loads(bad.body)["errors"]
        self.assertIn("no_such_key", errors)
        self.assertIn("nvr_patrol_interval_seconds", errors)
        base = 1000000.0
        good = jpost(user, "/api/data/settings",
                     {"values": {"f3d_alarm_delay_minutes": 0.5,
                                 "f3d_push_interval_seconds": 1.5}},
                     method="PATCH")
        self.assertEqual(good.status_code, 200)
        ctx = self.env.ctx
        device_id = next(iter(ctx.simulator.runtime))
        ctx.apply_status(device_id, "offline", "toggle", now=base)
        ctx.tick(now=base + 29)                  # 0.5 分钟未满:仍 pending
        self.assertEqual(ctx.alarms.state_of(device_id), "pending")
        ctx.tick(now=base + 31)                  # 满 30 秒:转正(热生效)
        self.assertEqual(ctx.alarms.state_of(device_id), "active")

    def test_edit_session_guard_and_edit_ops(self):
        """编辑台:未启会话一律 409;开会话后移动/取景生效;越界 elev 400"""
        user = self.env.logged_in()
        doc = json.loads(user.get("/api/layout").body)["layout"]
        zone_id = doc["zones"][0]["id"]
        building_id = doc["zones"][0]["buildings"][0]["id"]
        locked = jpost(user, f"/api/edit/buildings/{building_id}/move",
                       {"dx": -120, "dz": -120})
        self.assertEqual(locked.status_code, 409)
        self.assertIn("session_locked", json.loads(locked.body)["detail"])
        opened = jpost(user, "/api/edit/session", {"active": True})
        self.assertTrue(json.loads(opened.body)["active"])
        moved = jpost(user, f"/api/edit/buildings/{building_id}/move",
                      {"dx": -120, "dz": -120})
        self.assertEqual(moved.status_code, 200)
        self.assertEqual(json.loads(moved.body)["data_rev"], 1)
        bad_elev = jpost(user, f"/api/edit/zones/{zone_id}/focus-from-view",
                         {"elev": 5})
        self.assertEqual(bad_elev.status_code, 400)
        self.assertIn("俯仰角", json.loads(bad_elev.body)["detail"])
        homed = jpost(user, "/api/edit/home/from-view",
                      {"target": [1, 2, 3], "elev": 60, "theta": 40,
                       "radius": 150})
        self.assertEqual(homed.status_code, 200)
        # 视角类为非结构变更:rev 不再递增
        self.assertEqual(json.loads(homed.body)["data_rev"], 1)
        closed = jpost(user, "/api/edit/session", {"active": False})
        self.assertFalse(json.loads(closed.body)["active"])
        relock = jpost(user, f"/api/edit/buildings/{building_id}/move",
                       {"dx": 0, "dz": 0})
        self.assertEqual(relock.status_code, 409)


if __name__ == "__main__":
    unittest.main()
