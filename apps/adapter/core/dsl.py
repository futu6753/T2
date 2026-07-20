# -*- coding: utf-8 -*-
"""
@file    dsl.py
@brief   13-R-AD-1 声明式翻译 DSL:厂商报文 → UnifiedOsd/统一事件的映射以
         YAML 声明(字段路径 path / 单位换算 scale+offset / 枚举映射 enum /
         默认值 default / round 位数),translate 引擎解释执行;
         事件 event_id 用点路径占位模板(自实现渲染——标准库 format 会把
         {data.id} 当属性访问,故不可用);reply 语义参数(ack 超时/轮询
         间隔/终态判定谓词)并入厂商声明(原 R-AD-2)。
         新平台接入 = 写映射文件 + 验签器,translate 引擎零改动。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import math

from apps.adapter.core.errors import FieldError
from apps.adapter.core.model import DEVICE_KINDS, UnifiedEvent, UnifiedOsd

OSD_NUMERIC_FIELDS = ("longitude", "latitude", "altitude",
                      "battery_percent", "speed", "heading")


def get_path(data, path: str):
    """
    @brief  点路径取值:a.b.c,列表下标写作 a.0.b;缺失返回 None
    """
    node = data
    for part in str(path).split("."):
        if isinstance(node, dict):
            node = node.get(part)
        elif isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if node is None:
            return None
    return node


def render_template(template: str, context: dict) -> str:
    """
    @brief  点路径占位渲染:模板中 {a.b.c} 逐个替换为 get_path 结果
            (自实现:str.format 会把 data.id 当属性访问导致 AttributeError)
    """
    out, pos = [], 0
    while pos < len(template):
        char = template[pos]
        if char == "{":
            end = template.find("}", pos)
            if end < 0:
                raise FieldError(f"模板占位未闭合:{template!r}")
            token = template[pos + 1:end].strip()
            value = get_path(context, token)
            out.append("" if value is None else str(value))
            pos = end + 1
        else:
            out.append(char)
            pos += 1
    return "".join(out)


def _apply_rule(rule, raw: dict):
    """@brief 执行单字段映射规则(标量规则=常量;字典规则=path 管道)"""
    if not isinstance(rule, dict):
        return rule
    if "template" in rule:
        value = render_template(str(rule["template"]), raw)
    else:
        value = get_path(raw, rule.get("path", ""))
    if value is None:
        return rule.get("default")
    if "enum" in rule:
        table = rule["enum"] or {}
        value = table.get(str(value), rule.get("default"))
        return value
    if "scale" in rule or "offset" in rule:
        try:
            value = float(value) * float(rule.get("scale", 1)) \
                + float(rule.get("offset", 0))
        except (TypeError, ValueError):
            return rule.get("default")
        if math.isnan(value) or math.isinf(value):    # L1-08 危险输出把关
            return rule.get("default")
    if "round" in rule and isinstance(value, float):
        value = round(value, int(rule["round"]))
    return value


class MappingSpec:
    """单厂商映射声明(harness/mappings/<vendor>.yaml 解析产物)。"""

    def __init__(self, doc: dict):
        """@brief 装配并校验声明结构"""
        self.vendor = doc.get("vendor", "")
        self.source = doc.get("source", self.vendor)
        self.device_kind = doc.get("device_kind", "unknown")
        if self.device_kind not in DEVICE_KINDS:
            raise FieldError(f"映射声明 device_kind 非法:{self.device_kind}")
        self.osd_rules = doc.get("osd") or {}
        self.event_rules = doc.get("events") or []
        self.reply = doc.get("reply") or {}
        if "sn" not in self.osd_rules and self.osd_rules:
            raise FieldError(f"映射声明 {self.vendor} osd 缺少 sn 规则")

    def reply_params(self, defaults: dict) -> dict:
        """@brief reply 语义参数(缺省回退全局默认,R-AD-2 并入)"""
        merged = dict(defaults)
        merged.update({key: value for key, value in self.reply.items()
                       if key != "terminal"})
        merged["terminal"] = self.reply.get("terminal")
        return merged


def translate_osd(spec: MappingSpec, raw: dict, now_iso: str = "") -> UnifiedOsd:
    """@brief 解释执行 osd 映射:厂商报文 → UnifiedOsd"""
    values = {name: _apply_rule(rule, raw)
              for name, rule in spec.osd_rules.items()}
    sn = values.pop("sn", None)
    if not sn:
        raise FieldError(f"{spec.vendor} 报文缺少 SN,无法生成快照")
    osd = UnifiedOsd(sn=str(sn), source=spec.source,
                     device_kind=str(values.pop("device_kind",
                                                spec.device_kind)))
    for name, value in values.items():
        if name in OSD_NUMERIC_FIELDS and value is not None:
            value = float(value)
        if name == "mode_code" and value is not None:
            value = str(value)
        if hasattr(osd, name):
            setattr(osd, name, value)
        else:
            osd.extra[name] = value
    if not osd.updated_at:
        osd.updated_at = now_iso
    osd.online = bool(osd.online)
    return osd


def _event_matches(rule: dict, raw: dict) -> bool:
    """@brief 事件匹配谓词:when.path 存在性/等值判断"""
    when = rule.get("when")
    if not when:
        return True
    value = get_path(raw, when.get("path", ""))
    if "equals" in when:
        return str(value) == str(when["equals"])
    if when.get("exists"):
        return value is not None
    return value is not None


def translate_events(spec: MappingSpec, raw: dict,
                     now_iso: str = "") -> list:
    """@brief 解释执行事件映射:厂商报文 → 统一事件列表(首个命中规则)"""
    events = []
    for rule in spec.event_rules:
        if not _event_matches(rule, raw):
            continue
        event_id = render_template(str(rule.get("event_id", "")), raw)
        if not event_id:
            raise FieldError(f"{spec.vendor} 事件规则缺少 event_id 模板")
        events.append(UnifiedEvent(
            event_id=event_id,
            source=spec.source,
            event_type=str(_apply_rule(rule.get("event_type", "event"), raw)),
            severity=str(_apply_rule(rule.get("severity", "info"), raw)),
            ts=str(_apply_rule(rule.get("ts", {"default": now_iso}), raw)
                   or now_iso),
            sn=str(_apply_rule(rule.get("sn", {"default": ""}), raw) or ""),
            data=raw))
        break
    return events
