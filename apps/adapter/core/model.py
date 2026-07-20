# -*- coding: utf-8 -*-
"""
@file    model.py
@brief   北向统一数据契约(L01 §9):UnifiedOsd 与统一事件。字段集为契约,
         只允许向后兼容追加(H02 ai_directives)。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
from dataclasses import dataclass, field, asdict

SOURCES = ("zhiguang", "skysys", "siyun", "flighthub", "simulator")
DEVICE_KINDS = ("cleaning_robot", "uav", "dock", "flycart", "unknown")


@dataclass
class UnifiedOsd:
    """统一遥测快照(北向契约,L01 §9 字段全集)。"""

    sn: str
    source: str
    device_kind: str = "unknown"
    online: bool = False
    updated_at: str = ""
    longitude: float = None
    latitude: float = None
    altitude: float = None
    battery_percent: float = None
    speed: float = None
    heading: float = None
    mode_code: str = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """@brief 契约序列化"""
        return asdict(self)


@dataclass
class UnifiedEvent:
    """统一事件(源侧稳定键 event_id,轮询与推送同键互斥去重)。"""

    event_id: str
    source: str
    event_type: str
    severity: str = "info"
    ts: str = ""
    sn: str = ""
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """@brief 契约序列化"""
        return asdict(self)


def merge_snapshots(real: dict, simulated: dict) -> list:
    """
    @brief  真实快照与模拟器快照合并(L01 §4:真实优先——同 SN 以真实覆盖)
    @return UnifiedOsd 字典列表(SN 排序,稳定输出)
    """
    merged = {sn: osd for sn, osd in simulated.items()}
    merged.update(real)
    return [merged[sn].to_dict() for sn in sorted(merged)]
