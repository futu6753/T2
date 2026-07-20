# -*- coding: utf-8 -*-
"""
@file    web_issue.py
@brief   发证区(L02 §3 /issue Form 全参数):引擎解析校验 400 人话 →
         tracer+distort_seed → 内存解密 → process_certificate → finally del
         明文 → 备案(参数快照+成品密文存档)→ 笔记密文 → 审计 → 响应;
         v2 新增 medium 介质参数与推荐引擎回显(13-R-CV-2);/engines。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import base64
import secrets

import numpy as np
import cv2
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from gd_common.errors import PolicyValidationError
from gd_crypto import encrypt_envelope, envelope_to_json
from gd_storage import events
from apps.certvault.recommend import recommend_engine
from apps.certvault.wm.payload import new_tracer_id
from apps.certvault.wm.pipeline import process_certificate
from apps.rp_common.forms import form_bool, form_float, form_int, read_any_form
from apps.rp_common.multipart import first_file

NOTE_AAD = b"cv_note"
MAX_NOTE_IMAGES = 3
MAX_NOTE_IMAGE_BYTES = 5 * 1024 * 1024


def _auto_visible_text(recipient: str, purpose: str, validity: str) -> str:
    """@brief 留空自动拼装「限{对象}{用途} …有效」(L02)"""
    target = recipient or "指定对象"
    usage = purpose or "指定用途"
    return f"限{target}{usage} {validity}"


def build_issue_router(ctx) -> "APIRouter":
    """@brief 发证路由(CvContext 注入)"""
    router = APIRouter()

    @router.get("/engines")
    def list_engines():
        """@brief 各引擎 available/detail/recommended_strength/default"""
        return {"engines": ctx.registry.describe_all(),
                "default": ctx.registry.default_engine}

    @router.get("/engines/recommend")
    def engine_recommend(request: Request, cert_type: str = "",
                         medium: str = ""):
        """@brief 13-R-CV-2 推荐器(表单展示理由,用户可覆盖)"""
        user, _, error = ctx.bearer_user(request)
        if error:
            return error
        return recommend_engine(ctx.registry, ctx.records, cert_type, medium)

    @router.post("/issue")
    async def issue(request: Request):
        """@brief 发证全流程(顺序与响应契约见模块 docstring)"""
        user, _, error = ctx.bearer_user(request)
        if error:
            return error
        fields, files = await read_any_form(request)
        cert_id = form_int(fields, "cert_id", 0)
        cert = ctx.store.get_cert(cert_id)
        if cert is None:
            return JSONResponse({"error": "证件不存在"}, status_code=400)
        if cert["owner_id"] != user["id"] and user["role"] != "admin":
            return JSONResponse({"error": "无权使用该证件发证"},
                                status_code=403)
        # 引擎解析与可用性校验(不可用 → 400 人话原因)
        medium = fields.get("medium", "")
        recommendation = recommend_engine(ctx.registry, ctx.records,
                                          cert["cert_type"], medium)
        try:
            engine_id = ctx.registry.resolve(fields.get("engine", ""))
        except PolicyValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if form_bool(fields, "stega_native") and "+" in engine_id:
            return JSONResponse(
                {"error": "组合模式禁用 stega_native(避免截断 bw 通道)"},
                status_code=400)
        # 文案与参数
        validity = fields.get("validity", "") or "当日有效"
        visible_text = fields.get("visible_text_override", "") \
            or _auto_visible_text(fields.get("recipient", ""),
                                  fields.get("purpose", ""), validity)
        tracer_id = new_tracer_id()
        distort_seed = secrets.randbits(31)
        options = {
            "opacity": form_float(fields, "opacity", 0.18),
            "color": (form_int(fields, "color_r", 90),
                      form_int(fields, "color_g", 120),
                      form_int(fields, "color_b", 130)),
            "density": form_float(fields, "density", 1.0),
            "distort_amplitude": form_float(fields, "distort_amplitude", 1.2),
            "distort_seed": distort_seed,
            "export_width": form_int(fields, "export_width", 0),
            "wm_strength": form_float(fields, "wm_strength", -1),
            "guilloche": form_bool(fields, "guilloche", True),
            "smart_anchor": form_bool(fields, "smart_anchor", True),
        }
        # 内存解密 → 流水线 → finally del(02-B3 明文短暂存在)
        plain_bytes = ctx.store.read_cert_image(cert)
        try:
            plain = cv2.imdecode(np.frombuffer(plain_bytes, np.uint8),
                                 cv2.IMREAD_COLOR)
            if plain is None:
                return JSONResponse({"error": "证件密文解码失败"},
                                    status_code=500)
            artifacts = process_certificate(plain, tracer_id, ctx.registry,
                                            engine_id, visible_text, options)
        finally:
            del plain_bytes
        form_snapshot = {
            "recipient": fields.get("recipient", ""),
            "purpose": fields.get("purpose", ""), "validity": validity,
            "visible_text": visible_text, "medium": medium,
            "distort_seed": distort_seed,
            "options": {key: value for key, value in options.items()
                        if key != "color"},
            "recommended_engine": recommendation["engine"],
        }
        record_id = ctx.records.add_issuance(
            tracer_id, engine_id, cert, user["id"], form_snapshot, artifacts,
            artifacts["jpeg_bytes"])
        _save_notes(ctx, record_id, fields, files)
        ctx.audit.append(user["username"], events.CERT_ISSUED,
                         {"system": "certvault",
                          "tracer_id": f"{tracer_id:012x}",
                          "engine": engine_id, "medium": medium}, "0.0.0.0")
        return {"issuance_id": record_id, "tracer_id": f"{tracer_id:012x}",
                "engine": engine_id,
                "engine_name": " + ".join(
                    ctx.registry.get(member).name
                    for member in ctx.registry.members(engine_id)),
                "visible_text": visible_text,
                "image_b64": base64.b64encode(
                    artifacts["jpeg_bytes"]).decode("ascii"),
                "size": len(artifacts["jpeg_bytes"]),
                "embed_shape": [artifacts["embed_h"], artifacts["embed_w"]],
                "recommendation": recommendation}

    def _save_notes(ctx_ref, record_id: int, fields: dict, files: dict):
        """@brief 发证笔记密文(定位/备注/≤3 张图各 ≤5MB)"""
        location = fields.get("note_location", "")
        text = fields.get("note_text", "")
        images = (files.get("note_images") or [])[:MAX_NOTE_IMAGES]
        if not (location or text or images):
            return
        ctx_ref.records.save_note(
            record_id,
            _seal_text(ctx_ref, location), _seal_text(ctx_ref, text))
        note = ctx_ref.records.get_note(record_id)
        for _, image_bytes in images:
            if len(image_bytes) > MAX_NOTE_IMAGE_BYTES:
                continue
            blob_path, digest = ctx_ref.store.seal_blob(image_bytes, NOTE_AAD)
            ctx_ref.records.add_note_image(note["id"], blob_path, digest)

    def _seal_text(ctx_ref, text: str) -> str:
        """@brief 笔记文本信封加密"""
        if not text:
            return ""
        envelope = encrypt_envelope(text.encode(), ctx_ref.ring,
                                    ctx_ref.suite, aad=NOTE_AAD)
        return envelope_to_json(envelope)

    return router
