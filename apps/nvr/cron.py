# -*- coding: utf-8 -*-
"""
@file    cron.py
@brief   5 段 UTC cron 解析与下次触发计算(L04 §2 weekly_report.cron):
         字段序 分 时 日 月 周(0-6,0=周日;7 归一为 0),支持 * , - /;
         日与周同时受限时按标准 cron 语义任一匹配(双限取或);
         保存时校验(设置页 PUT 前置)。零第三方依赖。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
from datetime import datetime, timedelta, timezone

from gd_common.errors import PolicyValidationError

_FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
_FIELD_NAMES = ("分", "时", "日", "月", "周")


def _parse_field(text: str, low: int, high: int, name: str) -> frozenset:
    """@brief 解析单字段 → 允许值集合(* , - / 组合)"""
    allowed = set()
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            try:
                step = int(step_text)
            except ValueError:
                raise PolicyValidationError(f"cron {name} 字段步长非法: {step_text}")
            if step < 1:
                raise PolicyValidationError(f"cron {name} 字段步长须 ≥1")
        if part in ("*", ""):
            start, end = low, high
        elif "-" in part:
            try:
                start_text, end_text = part.split("-", 1)
                start, end = int(start_text), int(end_text)
            except ValueError:
                raise PolicyValidationError(f"cron {name} 字段区间非法: {part}")
        else:
            try:
                start = end = int(part)
            except ValueError:
                raise PolicyValidationError(f"cron {name} 字段非法: {part}")
        if name == "周":                              # 7 归一为 0(周日)
            start, end = (0 if start == 7 else start), (0 if end == 7 else end)
        if not (low <= start <= high and low <= end <= high and start <= end):
            raise PolicyValidationError(
                f"cron {name} 字段越界: {part}(允许 {low}-{high})")
        allowed.update(range(start, end + 1, step))
    return frozenset(allowed)


class CronSpec:
    """已解析的 cron 表达式。"""

    def __init__(self, expression: str):
        """@brief 解析 5 段表达式(非法抛 PolicyValidationError)"""
        fields = expression.split()
        if len(fields) != 5:
            raise PolicyValidationError(
                f"cron 须为 5 段(分 时 日 月 周),得到 {len(fields)} 段")
        self.expression = expression
        (self.minutes, self.hours, self.days, self.months,
         self.weekdays) = (
            _parse_field(field, low, high, name)
            for field, (low, high), name
            in zip(fields, _FIELD_RANGES, _FIELD_NAMES))
        self._day_restricted = fields[2] != "*"
        self._weekday_restricted = fields[4] != "*"

    def matches(self, moment: datetime) -> bool:
        """@brief 时刻匹配(日/周双限取或=标准 cron 语义)"""
        if moment.minute not in self.minutes or moment.hour not in self.hours \
                or moment.month not in self.months:
            return False
        day_ok = moment.day in self.days
        weekday_ok = ((moment.weekday() + 1) % 7) in self.weekdays  # 0=周日
        if self._day_restricted and self._weekday_restricted:
            return day_ok or weekday_ok
        if self._day_restricted:
            return day_ok
        if self._weekday_restricted:
            return weekday_ok
        return True

    def next_run(self, after: datetime = None) -> datetime:
        """@brief 严格晚于 after 的下次触发(UTC;逐分钟推进,上限 366 天)"""
        moment = (after or datetime.now(timezone.utc)) \
            .replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(366 * 24 * 60):
            if self.matches(moment):
                return moment
            moment += timedelta(minutes=1)
        raise PolicyValidationError("cron 一年内无触发点")


def validate_cron(expression: str) -> str:
    """@brief 保存时校验入口 @return 原表达式;非法抛 PolicyValidationError"""
    CronSpec(expression)
    return expression
