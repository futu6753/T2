# -*- coding: utf-8 -*-
"""
@file    simulator.py
@brief   遥测模拟器(L01 §8 SIMULATOR_SNS):按 SN 确定性生成模拟快照
         (SIM-UAV-* → uav、SIM-ROBOT-* → cleaning_robot、其余 unknown),
         与真实快照合并时真实优先。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import hashlib

from apps.adapter.core.model import UnifiedOsd


def _kind_of(sn: str) -> str:
    """@brief SN 前缀 → 设备类型"""
    upper = sn.upper()
    if "UAV" in upper:
        return "uav"
    if "ROBOT" in upper:
        return "cleaning_robot"
    if "CART" in upper:
        return "flycart"
    return "unknown"


def simulated_osd(sn: str, now_iso: str) -> UnifiedOsd:
    """@brief 单台模拟快照(SN 摘要派生确定性数值,可复现)"""
    digest = hashlib.sha256(sn.encode("utf-8")).digest()
    return UnifiedOsd(
        sn=sn, source="simulator", device_kind=_kind_of(sn), online=True,
        updated_at=now_iso,
        longitude=round(118.0 + digest[0] / 255.0, 6),
        latitude=round(24.4 + digest[1] / 255.0, 6),
        altitude=float(digest[2] % 120),
        battery_percent=float(30 + digest[3] % 70),
        speed=round(digest[4] % 15 / 1.0, 1),
        heading=float(digest[5] % 360),
        mode_code=str(digest[6] % 5),
        extra={"simulated": True})


def seed_simulator(sink, sns: list, now_iso: str):
    """@brief 把模拟快照灌入 sink(启动装配时调用)"""
    for sn in sns:
        sink.emit_osd(simulated_osd(sn, now_iso))
