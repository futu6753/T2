# -*- coding: utf-8 -*-
"""
@file    pipeline.py
@brief   水印流水线(02-B3,顺序固定 MUST NOT 更改):
         原图(内存解密)→ ①微扭曲 → ②抗擦除明水印(+智能锚定)→ ③暗码
         → JPEG 成品。明文仅存在于处理内存周期;组合双保险 bw 先嵌
         (深度残差其后,成员不可用时组合在 resolve 已被拒)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import numpy as np
import cv2

from apps.certvault.wm import visible
from apps.certvault.wm.engines import ENGINE_BW, EngineRegistry

JPEG_QUALITY = 92                 # 成品导出质量(暗码按 q60 存活设计留余量)
MAX_EMBED_SIDE = 1600             # 嵌入工作图长边上限(记录 embed_w/h 供回配)


def _to_work_size(image: np.ndarray) -> np.ndarray:
    """@brief 过大图缩至工作尺寸(提取回配按备案 embed_w/h 还原)"""
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= MAX_EMBED_SIDE:
        return image
    scale = MAX_EMBED_SIDE / longest
    return cv2.resize(image, (int(width * scale), int(height * scale)),
                      interpolation=cv2.INTER_AREA)


def process_certificate(plain_bgr: np.ndarray, tracer_id: int,
                        registry: EngineRegistry, engine_id: str,
                        visible_text: str, options: dict) -> dict:
    """
    @brief  执行固定顺序流水线并编码 JPEG 成品
    @param  plain_bgr 内存解密的明文原图(调用方 finally del)
    @param  options   opacity/color/density/distort_amplitude/distort_seed/
                      wm_strength/export_width/guilloche/smart_anchor
    @return {jpeg_bytes, embed_w, embed_h, wm_strength, wm_bit_len,
             engine_meta}
    """
    work = _to_work_size(plain_bgr)
    # ① 微扭曲(随机相位由 distort_seed 复现)
    distorted = visible.micro_distort(
        work, float(options.get("distort_amplitude", 1.2)),
        int(options["distort_seed"]))
    # ② 抗擦除明水印 + 智能锚定
    color = tuple(options.get("color", visible.DEFAULT_COLOR))
    marked = visible.visible_watermark(
        distorted, visible_text,
        opacity=float(options.get("opacity", visible.DEFAULT_OPACITY)),
        color=color, density=float(options.get("density", 1.0)),
        guilloche=bool(options.get("guilloche", True)))
    if options.get("smart_anchor", True):
        marked = visible.smart_anchor_lines(
            marked, color, float(options.get("opacity",
                                             visible.DEFAULT_OPACITY)))
    # ③ 暗码(组合双保险:bw 频域先嵌,其余成员其后)
    members = registry.members(engine_id)
    ordered = ([m for m in members if m == ENGINE_BW]
               + [m for m in members if m != ENGINE_BW])
    ycrcb = cv2.cvtColor(marked, cv2.COLOR_BGR2YCrCb).astype(np.float64)
    engine_meta = {}
    strength_used = None
    for member in ordered:
        strength = registry.strength_for(member,
                                         options.get("wm_strength"))
        ycrcb[:, :, 0] = registry.get(member).embed(
            ycrcb[:, :, 0], tracer_id, strength)
        engine_meta[member] = {"strength": strength}
        strength_used = strength if strength_used is None else strength_used
    stamped = cv2.cvtColor(
        np.clip(ycrcb, 0, 255).astype(np.uint8), cv2.COLOR_YCrCb2BGR)
    # 导出分辨率(0=保持嵌入尺寸;放大用 LANCZOS 保暗码)
    export_width = int(options.get("export_width", 0))
    if export_width > 0 and export_width != stamped.shape[1]:
        scale = export_width / stamped.shape[1]
        stamped = cv2.resize(stamped,
                             (export_width, int(stamped.shape[0] * scale)),
                             interpolation=cv2.INTER_LANCZOS4)
    ok, encoded = cv2.imencode(".jpg", stamped,
                               [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise RuntimeError("成品 JPEG 编码失败")
    return {"jpeg_bytes": encoded.tobytes(),
            "embed_w": marked.shape[1], "embed_h": marked.shape[0],
            "wm_strength": strength_used,
            "wm_bit_len": 96,
            "engine_meta": engine_meta}
