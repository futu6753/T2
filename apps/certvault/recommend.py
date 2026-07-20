# -*- coding: utf-8 -*-
"""
@file    recommend.py
@brief   13-R-CV-2 引擎自动推荐器:按证件类型、预期流转介质(电子/打印/翻拍)
         与历史溯源命中反馈(cv_engine_feedback)推荐引擎与强度;表单展示
         推荐理由,用户可覆盖;推荐器不可用时静默回退系统默认引擎。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
from gd_common.jsonlog import get_logger

_log = get_logger("certvault.recommend")

MEDIUM_ELECTRONIC = "electronic"      # 电子流转
MEDIUM_PRINT = "print"                # 打印
MEDIUM_RECAPTURE = "recapture"        # 翻拍
VALID_MEDIA = (MEDIUM_ELECTRONIC, MEDIUM_PRINT, MEDIUM_RECAPTURE)

# 介质 → 优先引擎序(02-B2 能力矩阵:stega 打印翻拍最强,bw 电子可靠)
_MEDIUM_PREFERENCE = {
    MEDIUM_ELECTRONIC: ("bw", "tm", "stega"),
    MEDIUM_PRINT: ("stega", "tm", "bw"),
    MEDIUM_RECAPTURE: ("stega", "tm", "bw"),
}
_FEEDBACK_MIN_SAMPLES = 5             # 反馈样本低于此不参与调序


def recommend_engine(registry, records, cert_type: str, medium: str) -> dict:
    """
    @brief  产出推荐(引擎+强度+人话理由);任何异常静默回退默认引擎
    @return {engine, strength, reason, fallback}
    """
    try:
        return _recommend(registry, records, cert_type, medium)
    except Exception as exc:                  # 推荐器故障不阻断发证(规约)
        _log.warning("推荐器异常,回退默认引擎",
                     extra={"ctx": {"error": str(exc)}})
        default = registry.default_engine
        return {"engine": default,
                "strength": registry.strength_for(default, -1),
                "reason": "推荐器暂不可用,已回退系统默认引擎", "fallback": True}


def _recommend(registry, records, cert_type: str, medium: str) -> dict:
    """@brief 推荐主逻辑:介质优先序 → 反馈命中率调序 → 可用性过滤"""
    medium_key = medium if medium in VALID_MEDIA else MEDIUM_ELECTRONIC
    preference = list(_MEDIUM_PREFERENCE[medium_key])
    stats = {(entry["engine"], entry["medium"]): entry
             for entry in records.feedback_stats()}
    scored = []
    for engine_id in preference:
        entry = stats.get((engine_id, medium_key))
        hit_rate = None
        if entry and entry["total"] >= _FEEDBACK_MIN_SAMPLES:
            hit_rate = entry["hits"] / entry["total"]
        scored.append((engine_id, hit_rate))
    # 有足量反馈的引擎按命中率重排(无反馈保持介质优先序)
    scored.sort(key=lambda item: (-(item[1] if item[1] is not None else -1),
                                  preference.index(item[0])))
    for engine_id, hit_rate in scored:
        available, _ = registry.get(engine_id).availability()
        if not available:
            continue
        reason = _build_reason(engine_id, medium_key, cert_type, hit_rate)
        return {"engine": engine_id,
                "strength": registry.strength_for(engine_id, -1),
                "reason": reason, "fallback": False}
    default = registry.default_engine
    return {"engine": default, "strength": registry.strength_for(default, -1),
            "reason": "候选引擎均不可用,已回退系统默认引擎", "fallback": True}


def _build_reason(engine_id: str, medium: str, cert_type: str,
                  hit_rate) -> str:
    """@brief 推荐理由人话(表单展示,可覆盖)"""
    medium_names = {MEDIUM_ELECTRONIC: "电子流转", MEDIUM_PRINT: "打印",
                    MEDIUM_RECAPTURE: "翻拍"}
    base = {
        "bw": "频域盲水印在电子链路可靠且毫秒级完成",
        "stega": "深度隐写对打印/翻拍信道存活率最高",
        "tm": "TrustMark 解码最快且视觉痕迹最轻",
    }.get(engine_id, "系统默认引擎")
    reason = (f"按{medium_names.get(medium, medium)}介质推荐:{base}"
              f"(证件类型 {cert_type or '未指定'})")
    if hit_rate is not None:
        reason += f";历史该介质命中率 {hit_rate:.0%}"
    return reason
