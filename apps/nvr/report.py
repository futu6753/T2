# -*- coding: utf-8 -*-
"""
@file    report.py
@brief   运维周报(L04 §7 / 13-R-NVR-3 事实层协议):
         ①聚合事实层(可用率按采样/异常最多场站/告警统计含平均与最长故障
         时长/按场站分布/抖动榜/投递失败数),facts 随报告落库;
         ②Claude Messages 直连(x-api-key + anthropic-version:2023-06-01,
         零 SDK,请求形态单测锁定),提示词要求引用事实层锚点数值;
         ③生成后事实校验:关键锚点数值未出现在文稿 → 降级确定性模板
         (generated_by=template+原因);无 Key/请求失败同降级。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import json
import urllib.request
from datetime import datetime, timedelta, timezone

from gd_common.jsonlog import get_logger

_log = get_logger("nvr.report")

ANTHROPIC_ENDPOINT = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


def _now() -> datetime:
    """@brief UTC 当前时间"""
    return datetime.now(timezone.utc)


def aggregate_facts(db, period_days: int) -> dict:
    """@brief 事实层聚合(周报唯一数据来源,13-R-NVR-3)"""
    since = (_now() - timedelta(days=period_days)).isoformat()
    total, online = db.query(
        "SELECT COUNT(*), SUM(CASE WHEN status = 'online' THEN 1 ELSE 0 END)"
        " FROM nvr_check_results WHERE checked_at >= ?", (since,))[0]
    availability = round((online or 0) / total, 4) if total else None
    abnormal_rows = db.query(
        "SELECT d.station, COUNT(*) AS cnt FROM nvr_check_results r"
        " JOIN nvr_devices d ON d.id = r.device_id"
        " WHERE r.checked_at >= ? AND r.status != 'online'"
        " GROUP BY d.station ORDER BY cnt DESC LIMIT 3", (since,))
    alert_rows = db.query(
        "SELECT COUNT(*), AVG(duration_seconds), MAX(duration_seconds)"
        " FROM nvr_alerts WHERE started_at >= ? AND state = 'resolved'",
        (since,))[0]
    station_alerts = db.query(
        "SELECT d.station, COUNT(*) FROM nvr_alerts a"
        " JOIN nvr_devices d ON d.id = a.device_id"
        " WHERE a.started_at >= ? GROUP BY d.station", (since,))
    flap_rows = db.query(
        "SELECT d.name, COUNT(*) AS flips FROM nvr_timeline t"
        " JOIN nvr_devices d ON d.id = t.device_id"
        " WHERE t.occurred_at >= ? AND t.event_type = 'status_change'"
        " GROUP BY d.name ORDER BY flips DESC LIMIT 3", (since,))
    delivery_failed = db.query(
        "SELECT COUNT(*) FROM nvr_notifications"
        " WHERE created_at >= ? AND state = 'abandoned'", (since,))[0][0]
    return {"period_days": period_days, "sample_total": total or 0,
            "availability": availability,
            "worst_stations": [{"station": row[0] or "未登记",
                                "abnormal_samples": row[1]}
                               for row in abnormal_rows],
            "alerts": {"total": alert_rows[0] or 0,
                       "avg_duration_seconds": round(alert_rows[1] or 0, 1),
                       "max_duration_seconds": alert_rows[2] or 0},
            "alerts_by_station": {row[0] or "未登记": row[1]
                                  for row in station_alerts},
            "flap_top": [{"device": row[0], "transitions": row[1]}
                         for row in flap_rows],
            "delivery_failed": delivery_failed}


def build_claude_request(facts: dict, model: str) -> dict:
    """@brief Claude 请求形态(单测锁定;提示词要求引用锚点数值)"""
    return {
        "model": model,
        "max_tokens": 1500,
        "messages": [{
            "role": "user",
            "content": ("你是港电运维周报助手。仅依据下方事实层 JSON 撰写中文"
                        "周报(总结/风险/建议三段),所有数字必须原样引用事实层"
                        ",不得推算或虚构:\n"
                        + json.dumps(facts, ensure_ascii=False)),
        }],
    }


def _fact_anchors(facts: dict) -> list:
    """@brief 事实校验锚点:文稿必须出现的关键数值(13-R-NVR-3)"""
    anchors = [str(facts["sample_total"]), str(facts["alerts"]["total"])]
    if facts["availability"] is not None:
        anchors.append(f"{facts['availability'] * 100:.1f}".rstrip("0")
                       .rstrip("."))
    return anchors


def render_template(facts: dict) -> str:
    """@brief 确定性降级模板(锚点数值内嵌)"""
    availability_text = "无采样数据" if facts["availability"] is None \
        else f"{facts['availability'] * 100:.1f}%"
    worst = "、".join(f"{row['station']}({row['abnormal_samples']} 次异常采样)"
                      for row in facts["worst_stations"]) or "无"
    flap = "、".join(f"{row['device']}({row['transitions']} 次跃迁)"
                     for row in facts["flap_top"]) or "无"
    return (f"# 港电 NVR 运维周报(近 {facts['period_days']} 天)\n\n"
            f"采样总数 {facts['sample_total']},整体可用率 {availability_text}。"
            f"告警 {facts['alerts']['total']} 起,平均故障时长"
            f" {facts['alerts']['avg_duration_seconds']} 秒,最长"
            f" {facts['alerts']['max_duration_seconds']} 秒。\n\n"
            f"异常最多场站:{worst}。抖动榜:{flap}。"
            f"通知投递放弃 {facts['delivery_failed']} 条。\n\n"
            "建议:优先排查异常最多场站的网络与供电;抖动设备建议调整去抖"
            "模式或检查线路。")


class ReportService:
    """周报生成(Claude 优先,校验失败/异常降级模板)。"""

    def __init__(self, db, api_key: str = "", model: str = "claude-sonnet-4-6",
                 transport=None):
        """@brief transport(request_dict)→content_text 可注入(测试 fake)"""
        self._db = db
        self._api_key = api_key
        self._model = model
        self._transport = transport or self._default_transport

    def _default_transport(self, request_body: dict) -> str:
        """@brief Claude Messages 直连(零 SDK,头形态契约)"""
        request = urllib.request.Request(
            ANTHROPIC_ENDPOINT,
            data=json.dumps(request_body).encode(),
            headers={"Content-Type": "application/json",
                     "x-api-key": self._api_key,
                     "anthropic-version": ANTHROPIC_VERSION},
            method="POST")
        with urllib.request.urlopen(request, timeout=60) as resp:
            payload = json.loads(resp.read())
        return "".join(block.get("text", "")
                       for block in payload.get("content", []))

    def generate(self, period_days: int = 7) -> dict:
        """@brief 生成并落库 @return 报告记录"""
        facts = aggregate_facts(self._db, period_days)
        content, generated_by, reason = None, "template", ""
        if self._api_key:
            try:
                content = self._transport(
                    build_claude_request(facts, self._model))
                missing = [anchor for anchor in _fact_anchors(facts)
                           if anchor not in content]
                if missing:
                    reason = f"事实锚点缺失: {missing}"
                    content = None
                else:
                    generated_by = "claude"
            except Exception as exc:
                reason = f"Claude 请求失败: {type(exc).__name__}"
        else:
            reason = "未配置 ANTHROPIC_API_KEY"
        if content is None:
            content = render_template(facts)
        self._db.execute(
            "INSERT INTO nvr_reports(period_days, generated_by, reason,"
            " facts_json, content, created_at) VALUES(?, ?, ?, ?, ?, ?)",
            (period_days, generated_by, reason,
             json.dumps(facts, ensure_ascii=False), content,
             _now().isoformat()))
        if generated_by == "template" and self._api_key:
            _log.warning("周报降级为模板", extra={"ctx": {"reason": reason}})
        return self.latest()

    def latest(self) -> dict:
        """@brief 最新报告"""
        rows = self._db.query(
            "SELECT id, period_days, generated_by, reason, facts_json,"
            " content, created_at FROM nvr_reports ORDER BY id DESC LIMIT 1")
        if not rows:
            return None
        record = dict(zip(("id", "period_days", "generated_by", "reason",
                           "facts_json", "content", "created_at"), rows[0]))
        record["facts"] = json.loads(record.pop("facts_json"))
        return record

    def list(self, limit: int = 20) -> list:
        """@brief 报告列表(不含正文)"""
        rows = self._db.query(
            "SELECT id, period_days, generated_by, created_at FROM nvr_reports"
            " ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(zip(("id", "period_days", "generated_by", "created_at"),
                         row)) for row in rows]
