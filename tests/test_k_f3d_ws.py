# -*- coding: utf-8 -*-
"""
@file    test_k_f3d_ws.py
@brief   WS 实时通道真连接验收(uvicorn LiveServer + websockets 客户端):
         ① 首帧 snapshot → 周期 update 同构全量契约;
         ② {"type":"fps"} 回报驱动降级阶梯,档位变更随 update 推达
           (13-R-F3D-1 端到端闭环)。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import asyncio
import json
import unittest

import websockets

from tests.e2e.live import LiveServer
from tests.f3d_env import F3dEnv


class F3dWsTest(unittest.TestCase):
    """真 WS 连接契约。"""

    def setUp(self):
        """@brief 起 F3D 应用真服务(推送周期压到 0.5s 加速)"""
        self.env = F3dEnv()
        self.env.ctx.settings.set_override(
            "f3d_push_interval_seconds", 0.5, "tester", "0.0.0.0")
        self.server = LiveServer(self.env.app)   # 构造即启动
        self.url = f"ws://127.0.0.1:{self.server.port}/ws"

    def tearDown(self):
        """@brief 关服务"""
        self.server.stop()

    def test_ws_snapshot_then_update_contract(self):
        """首帧 snapshot 全量;随后周期 update 同构(ver/data_rev/kpi/devices)"""
        async def scenario():
            async with websockets.connect(self.url) as socket:
                first = json.loads(await asyncio.wait_for(socket.recv(), 5))
                second = json.loads(await asyncio.wait_for(socket.recv(), 5))
                return first, second
        first, second = asyncio.run(scenario())
        self.assertEqual(first["type"], "snapshot")
        self.assertEqual(second["type"], "update")
        for frame in (first, second):
            self.assertEqual(frame["ver"], "5.0.0-m6")
            self.assertEqual(frame["kpi"]["total"], 23)
            self.assertEqual(len(frame["devices"]), 23)
            self.assertEqual(frame["site"], "云枢智造产业园")
            self.assertIn(frame["tier"], ("full", "no_shadow", "low_tex",
                                          "low_push"))
            self.assertIn("alarms", frame)
            self.assertIn("data_rev", frame)
        payload = json.dumps(second, ensure_ascii=False).encode()
        self.assertLess(len(payload), 5 * 1024)     # 22+1 台帧预算(L03 §1)

    def test_ws_fps_feedback_drives_tier(self):
        """连续低帧回报 → 档位下沉并随 update 推达;高帧回报 → 回升"""
        async def scenario():
            async with websockets.connect(self.url) as socket:
                await asyncio.wait_for(socket.recv(), 5)      # snapshot
                for _ in range(4):
                    await socket.send(json.dumps({"type": "fps", "value": 8}))
                degraded = None
                for _ in range(6):
                    frame = json.loads(await asyncio.wait_for(socket.recv(), 5))
                    if frame["tier"] != "full":
                        degraded = frame["tier"]
                        break
                    for _ in range(3):
                        await socket.send(
                            json.dumps({"type": "fps", "value": 8}))
                for _ in range(12):
                    await socket.send(json.dumps({"type": "fps", "value": 60}))
                recovered = None
                for _ in range(8):
                    frame = json.loads(await asyncio.wait_for(socket.recv(), 5))
                    if frame["tier"] == "full":
                        recovered = frame["tier"]
                        break
                    for _ in range(3):
                        await socket.send(
                            json.dumps({"type": "fps", "value": 60}))
                return degraded, recovered
        degraded, recovered = asyncio.run(scenario())
        self.assertIn(degraded, ("no_shadow", "low_tex", "low_push"))
        self.assertEqual(recovered, "full")


if __name__ == "__main__":
    unittest.main()
