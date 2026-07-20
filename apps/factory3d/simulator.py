# -*- coding: utf-8 -*-
"""
@file    simulator.py
@brief   设备模拟器(L03 §3.1 内置模拟器形态):固定种子随机游走指标、
         手动 toggle 后不再自动改(L03 §7 /toggle 契约)、外部注入通道复用
         同一状态入口;规模参数化 22→50→100→200(13-R-F3D-4 基准)。
         MQTT 桥接留 GAP-17(需 paho-mqtt,目标环境挂接)。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import random
import time

from apps.factory3d import layout as lo

STATUS_ONLINE = "online"
STATUS_OFFLINE = "offline"

# 按类型的指标字段与基线值(L03 §7 metrics 按类型字段表语义等价)
METRIC_BASELINES = {
    "plc": {"cycle_ms": 120.0, "load_pct": 45.0},
    "sensor": {"temp_c": 24.0, "humidity_pct": 55.0},
    "camera": {"bitrate_kbps": 2048.0, "fps": 25.0},
    "power": {"voltage_v": 380.0, "current_a": 36.0},
    "hvac": {"temp_c": 26.0, "power_kw": 3.2},
}


def synthetic_layout(total_devices: int) -> dict:
    """@brief 生成 N 台设备的合成布局(规模基准 22/50/100/200,R-F3D-4)"""
    doc = lo.default_layout()
    existing = lo.count_devices(doc)
    types = list(METRIC_BASELINES)
    zone = doc["zones"][0]
    slot = 0
    while existing < total_devices:
        building = zone["buildings"][slot % len(zone["buildings"])]
        dtype = types[slot % len(types)]
        building["devices"].append(
            {"id": f"syn-{slot:04d}", "name": f"扩展设备-{slot:04d}",
             "type": dtype, "icon": None, "show": True, "label": False,
             "room": "扩展区", "ip": f"10.20.{slot // 250}.{slot % 250 + 1}",
             "pos": [0, 0, 0], "seed": {}})
        slot += 1
        existing += 1
    while existing > total_devices and doc["outdoor"]:
        doc["outdoor"].pop()
        existing -= 1
    return doc


class Simulator:
    """确定性设备运行时:seed 固定则序列可复现(H09 K.1 基线可复现)。"""

    def __init__(self, doc: dict, seed: int = 20260719):
        """@brief 按布局构建运行时表"""
        self._rng = random.Random(seed)
        self.runtime = {}
        self.rebuild(doc)

    def rebuild(self, doc: dict):
        """@brief 布局变更后重建运行时(既有设备保留状态,新设备上线)"""
        fresh = {}
        for building, device in lo.iter_devices(doc):
            known = self.runtime.get(device["id"])
            if known is not None:
                known["name"] = device["name"]
                known["type"] = device["type"]
                known["building"] = building["name"] if building else "室外园区"
                fresh[device["id"]] = known
                continue
            baseline = METRIC_BASELINES[device["type"]]
            fresh[device["id"]] = {
                "id": device["id"], "name": device["name"],
                "type": device["type"],
                "building": building["name"] if building else "室外园区",
                "status": STATUS_ONLINE, "manual": False,
                "metrics": dict(baseline), "last_seen": time.time()}
        self.runtime = fresh

    def tick(self, now: float = None):
        """@brief 一个采集周期:非手动设备指标随机游走并刷新心跳"""
        now = time.time() if now is None else now
        for state in self.runtime.values():
            if state["manual"] or state["status"] != STATUS_ONLINE:
                continue
            baseline = METRIC_BASELINES[state["type"]]
            for key, base in baseline.items():
                drift = self._rng.uniform(-0.03, 0.03) * base
                state["metrics"][key] = round(
                    state["metrics"][key] * 0.9 + (base + drift) * 0.1, 2)
            state["last_seen"] = now

    def set_status(self, device_id: str, status: str,
                   manual: bool = False, now: float = None) -> tuple:
        """
        @brief  设置设备状态(toggle/外部注入统一入口)
        @return (old_status, new_status);设备不存在抛 KeyError
        """
        now = time.time() if now is None else now
        state = self.runtime[device_id]
        old = state["status"]
        state["status"] = status
        if manual:
            state["manual"] = True      # 标记手动:模拟器不再自动改(L03 §7)
        if status == STATUS_ONLINE:
            state["last_seen"] = now
        return old, status

    def toggle(self, device_id: str, now: float = None) -> tuple:
        """@brief 模拟掉线/恢复上线(标记手动) @return (old, new)"""
        state = self.runtime[device_id]
        target = (STATUS_ONLINE if state["status"] == STATUS_OFFLINE
                  else STATUS_OFFLINE)
        return self.set_status(device_id, target, manual=True, now=now)

    def inject(self, device_id: str, status: str = None, metrics: dict = None,
               now: float = None) -> tuple:
        """@brief 外部注入(/api/external,签名校验在 web 层) @return (old,new)"""
        now = time.time() if now is None else now
        state = self.runtime[device_id]
        old = state["status"]
        if metrics:
            state["metrics"].update(
                {key: float(value) for key, value in metrics.items()})
            state["last_seen"] = now
        if status in (STATUS_ONLINE, STATUS_OFFLINE):
            self.set_status(device_id, status, now=now)
            return old, status
        return old, old

    def kpi(self) -> dict:
        """@brief KPI 四枚:总数/在线/离线(告警数由告警引擎补齐)"""
        total = len(self.runtime)
        online = sum(1 for state in self.runtime.values()
                     if state["status"] == STATUS_ONLINE)
        return {"total": total, "online": online, "offline": total - online}

    def snapshot(self) -> list:
        """@brief 全量设备帧(WS 每周期全量一帧,22 台 <5KB 预算)"""
        return [{"id": state["id"], "n": state["name"], "t": state["type"],
                 "b": state["building"], "s": state["status"],
                 "m": {key: round(value, 1)
                       for key, value in state["metrics"].items()}}
                for state in self.runtime.values()]
