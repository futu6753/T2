# -*- coding: utf-8 -*-
"""
@file    trace.py
@brief   溯源识别(02-B1 / L02 §3):候选=全部非独立备案;盲提取顺序
         tm→stega→aliyun(可用且存在该引擎备案才尝试),未命中→bw 逐条
         备案回配(engine_meta 缺失用 wm_bit_len/strength/embed 兜底,
         组合 meta 取 metas.bw);任一引擎异常只记 engine_errors 绝不拖垮
         整体;13-R-CV-3 组合命中交叉校验输出 confidence/vote_detail;
         13-R-CV-5 撤销备案仍命中但明示作废。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import numpy as np
import cv2

from gd_common.jsonlog import get_logger
from apps.certvault.wm.engines import (
    BLIND_TRY_ORDER, ENGINE_BW, EngineRegistry,
)

_log = get_logger("certvault.trace")

CONFIDENCE_STANDARD = "standard"     # 单引擎命中
CONFIDENCE_HIGH = "high"             # 双引擎一致
CONFIDENCE_CONFLICT = "conflict"     # 双引擎不一致(告警+审计)


def _suspect_luma(image_bytes: bytes) -> np.ndarray:
    """@brief 解码可疑件亮度通道;解码失败返回 None(路由层 400)"""
    image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return None
    return cv2.cvtColor(image, cv2.COLOR_BGR2YCrCb)[:, :, 0].astype(np.float64)


class TraceService:
    """多引擎溯源(引擎故障隔离)。"""

    def __init__(self, registry: EngineRegistry, records):
        """@brief 注入引擎注册表与备案台账"""
        self._registry = registry
        self._records = records

    def trace(self, image_bytes: bytes) -> dict:
        """
        @brief  执行完整溯源流程
        @return 命中:{found, tracer_id, engine, confidence, vote_detail,
                tried_engines, engine_errors, record};
                未命中:{found=false, tried_engines, engine_errors, message}
        """
        luma = _suspect_luma(image_bytes)
        if luma is None:
            return {"decode_failed": True}
        candidates = self._records.traceable_candidates()
        tried, errors = [], {}
        hits = {}                      # engine_id → tracer_hex(投票用)
        # ① 盲提取引擎按契约顺序(可用且存在该引擎备案才尝试)
        for engine_id in BLIND_TRY_ORDER:
            if not self._has_candidates(candidates, engine_id):
                continue
            available, _ = self._registry.get(engine_id).availability()
            if not available:
                continue
            tried.append(engine_id)
            hit = self._blind_try(engine_id, luma, candidates, errors)
            if hit:
                hits[engine_id] = hit
                break                  # 任一命中即返(组合成员命中同此)
        # ② 未命中 → bw 逐条备案回配
        if not hits and self._has_candidates(candidates, ENGINE_BW):
            tried.append(ENGINE_BW)
            hit = self._bw_replay(luma, candidates, errors)
            if hit:
                hits[ENGINE_BW] = hit
        if not hits:
            return {"found": False, "tried_engines": tried,
                    "engine_errors": errors,
                    "message": self._miss_message(tried, errors)}
        return self._build_hit(hits, tried, errors, luma, candidates)

    # ---- 内部步骤 -------------------------------------------------------
    def _has_candidates(self, candidates: list, engine_id: str) -> bool:
        """@brief 是否存在含该成员引擎的备案(组合备案对任一成员可命中)"""
        return any(engine_id in self._registry.members(record["engine"])
                   for record in candidates)

    def _blind_try(self, engine_id: str, luma, candidates, errors):
        """@brief 盲提取一次覆盖全部候选;异常仅记 engine_errors"""
        try:
            strength = self._registry.get(engine_id).recommended_strength
            tracer_id = self._registry.get(engine_id).extract(luma, strength)
        except Exception as exc:               # 故障隔离(L02 契约)
            errors[engine_id] = str(exc)
            return None
        if tracer_id is None:
            return None
        tracer_hex = f"{tracer_id:012x}"
        matched = [record for record in candidates
                   if record["tracer_id"] == tracer_hex]
        return tracer_hex if matched else None

    def _bw_replay(self, luma, candidates, errors):
        """@brief bw 逐条备案回配:按备案 embed_w/h 与 strength 还原后提取"""
        for record in candidates:
            if ENGINE_BW not in self._registry.members(record["engine"]):
                continue
            try:
                meta = record["engine_meta"].get(ENGINE_BW, {})
                strength = meta.get("strength") or record["wm_strength"] \
                    or self._registry.get(ENGINE_BW).recommended_strength
                resized = cv2.resize(
                    luma, (record["embed_w"], record["embed_h"])) \
                    if record["embed_w"] and record["embed_h"] else luma
                tracer_id = self._registry.get(ENGINE_BW).extract(
                    resized, float(strength))
            except Exception as exc:
                errors[ENGINE_BW] = str(exc)
                continue
            if tracer_id is not None \
                    and f"{tracer_id:012x}" == record["tracer_id"]:
                return record["tracer_id"]
        return None

    def _build_hit(self, hits: dict, tried, errors, luma, candidates) -> dict:
        """@brief 命中响应:R-CV-3 组合备案双引擎交叉校验置信"""
        primary_engine = next(iter(hits))
        tracer_hex = hits[primary_engine]
        record = self._records.get_by_tracer(tracer_hex)
        vote_detail = {primary_engine: tracer_hex}
        confidence = CONFIDENCE_STANDARD
        members = self._registry.members(record["engine"])
        if len(members) > 1:                   # 组合备案:尝试第二引擎交叉校验
            for member in members:
                if member in vote_detail:
                    continue
                second = self._cross_check(member, luma, record, errors)
                if second is not None:
                    vote_detail[member] = second
            values = set(vote_detail.values())
            if len(vote_detail) >= 2 and len(values) == 1:
                confidence = CONFIDENCE_HIGH
            elif len(values) > 1:
                confidence = CONFIDENCE_CONFLICT
                _log.warning("溯源投票不一致,疑似碰撞或伪造备案",
                             extra={"ctx": {"votes": vote_detail}})
        return {"found": True, "tracer_id": tracer_hex,
                "engine": primary_engine,
                "engine_name": self._registry.get(primary_engine).name,
                "confidence": confidence, "vote_detail": vote_detail,
                "tried_engines": tried, "engine_errors": errors,
                "record": record, "revoked": bool(record["revoked_at"])}

    def _cross_check(self, engine_id: str, luma, record, errors):
        """@brief 第二引擎提取(不可用/异常返回 None,不影响主命中)"""
        available, _ = self._registry.get(engine_id).availability()
        if not available:
            return None
        try:
            if engine_id == ENGINE_BW:
                return self._bw_replay(luma, [record], errors)
            strength = self._registry.get(engine_id).recommended_strength
            tracer_id = self._registry.get(engine_id).extract(luma, strength)
            return f"{tracer_id:012x}" if tracer_id is not None else None
        except Exception as exc:
            errors[engine_id] = str(exc)
            return None

    def _miss_message(self, tried, errors) -> str:
        """@brief 未命中人话消息(附 stega 未启用提示与异常说明,L02)"""
        parts = ["未在备案中找到匹配的溯源标识。"]
        stega_available, stega_detail = self._registry.get("stega").availability()
        if not stega_available:
            parts.append(f"提示:深度隐写引擎未启用({stega_detail})。")
        for engine_id, detail in errors.items():
            parts.append(f"引擎 {engine_id} 异常:{detail}。")
        return "".join(parts)
