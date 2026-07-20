# -*- coding: utf-8 -*-
"""
@file    render_ladder.py
@brief   13-R-F3D-1 性能自适应降级阶梯:fps 持续采样 → 滞回分档
         (关阴影 → 降贴图分辨率 → 降推送频率),恢复后自动回升;
         档位与阈值走统一策略层可配;档位事件可查(B8 数据来源)。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import time

TIERS = ("full", "no_shadow", "low_tex", "low_push")
# 各档位对 WS 推送周期的放大系数(仅末档降推送频率,L03/H02-D2)
PUSH_MULTIPLIERS = (1.0, 1.0, 1.0, 2.5)
TIER_LABELS = ("全效", "关阴影", "降贴图", "降推送")


class DegradeLadder:
    """fps 驱动的滞回分档器:连续 N 个采样越线才切档,单调单步切换。"""

    def __init__(self, settings, on_change=None):
        """
        @brief  绑定策略层与档位变更回调
        @param  on_change callable(old_tier, new_tier, fps, now) 档位事件出口
        """
        self._settings = settings
        self._on_change = on_change
        self.tier = 0
        self._low_streak = 0
        self._high_streak = 0
        self._events = []           # 进程内档位事件(研究锚点可查,B8)

    def _enabled(self) -> bool:
        """@brief 阶梯总开关(热生效)"""
        return bool(self._settings.get("f3d_ladder_enabled"))

    def feed(self, fps: float, now: float = None):
        """
        @brief  喂入一个 fps 采样:滞回判定,必要时单步切档
        @return 新档位 int(未切档返回 None)
        """
        now = time.time() if now is None else now
        if not self._enabled():
            return None
        low = float(self._settings.get("f3d_fps_low_threshold"))
        high = float(self._settings.get("f3d_fps_high_threshold"))
        window = int(self._settings.get("f3d_ladder_window"))
        fps = float(fps)
        if fps < low:
            self._low_streak += 1
            self._high_streak = 0
        elif fps > high:
            self._high_streak += 1
            self._low_streak = 0
        else:                        # 滞回带内:双向清零,保持现档
            self._low_streak = 0
            self._high_streak = 0
        if self._low_streak >= window and self.tier < len(TIERS) - 1:
            return self._switch(self.tier + 1, fps, now)
        if self._high_streak >= window and self.tier > 0:
            return self._switch(self.tier - 1, fps, now)
        return None

    def _switch(self, new_tier: int, fps: float, now: float) -> int:
        """@brief 执行切档并发档位事件(单调单步)"""
        old_tier = self.tier
        self.tier = new_tier
        self._low_streak = 0
        self._high_streak = 0
        event = {"ts": now, "from": TIERS[old_tier], "to": TIERS[new_tier],
                 "fps": fps}
        self._events.append(event)
        if self._on_change is not None:
            self._on_change(old_tier, new_tier, fps, now)
        return new_tier

    def push_interval(self, base_seconds: float) -> float:
        """@brief 当前档位下的推送周期(末档降推送频率)"""
        return float(base_seconds) * PUSH_MULTIPLIERS[self.tier]

    def tier_name(self) -> str:
        """@brief 当前档位名(大屏档位指示)"""
        return TIERS[self.tier]

    def events(self) -> list:
        """@brief 档位事件列表(测试锚点 test_r_f3d1_degrade 可查)"""
        return list(self._events)
