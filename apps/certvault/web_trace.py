# -*- coding: utf-8 -*-
"""
@file    web_trace.py
@brief   溯源与备案区(L02 §3):/trace(cv2 解码失败 400;命中人话 message;
         审计 hit/engine/tried;反馈回流)、/records、独立备案、
         13-R-CV-5 撤销(管理员/发证人,留审计)、笔记与存档下载
         (仅发证人/管理员,越权 403)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from gd_common.errors import PolicyValidationError
from gd_crypto import decrypt_envelope, envelope_from_json
from gd_storage import events
from apps.certvault.recommend import VALID_MEDIA
from apps.rp_common.forms import read_any_form
from apps.rp_common.multipart import first_file

NOTE_AAD = b"cv_note"


def _hit_message(record: dict, engine_name: str, revoked: bool) -> str:
    """@brief 命中人话消息(L02 契约文案)"""
    message = (f"该文件于 {record['created_at'][:19]} 由『用户#{record['issuer_id']}』"
               f"生成,交付对象『{record['recipient'] or '未登记'}』,"
               f"用途『{record['purpose'] or '未登记'}』,"
               f"有效期『{record['validity'] or '未登记'}』({engine_name}命中)")
    if revoked:
        message += "。注意:该备案已作废(已撤销)"
    return message


def build_trace_router(ctx) -> "APIRouter":
    """@brief 溯源与备案路由(CvContext 注入)"""
    router = APIRouter()

    @router.post("/trace")
    async def trace(request: Request):
        """@brief 溯源识别(引擎顺序契约在 TraceService)"""
        user, _, error = ctx.bearer_user(request)
        if error:
            return error
        fields, files = await read_any_form(request)
        file_bytes = first_file(files, "file")
        if file_bytes is None:
            return JSONResponse({"error": "缺少 file 文件字段"}, status_code=400)
        result = ctx.tracer.trace(file_bytes)
        if result.get("decode_failed"):
            return JSONResponse({"error": "文件无法解码为图像"}, status_code=400)
        medium = fields.get("medium", "")
        medium = medium if medium in VALID_MEDIA else ""
        if result["found"]:
            record = result["record"]
            result["message"] = _hit_message(record,
                                             result["engine_name"],
                                             result["revoked"])
            ctx.records.add_engine_feedback(result["tracer_id"],
                                            result["engine"], medium, True)
            if result["confidence"] == "conflict":
                ctx.audit.append(user["username"], events.CERT_TRACED,
                                 {"system": "certvault", "hit": True,
                                  "conflict": True,
                                  "votes": result["vote_detail"]}, "0.0.0.0")
        ctx.audit.append(user["username"], events.CERT_TRACED,
                         {"system": "certvault", "hit": result["found"],
                          "engine": result.get("engine", ""),
                          "tried": result["tried_engines"]}, "0.0.0.0")
        return result

    @router.get("/records")
    def list_records(request: Request):
        """@brief 备案台账(发证人可见自己的;管理员全量)"""
        user, _, error = ctx.bearer_user(request)
        if error:
            return error
        return {"records": ctx.records.list_records(
            user["id"], user["role"] == "admin"),
            "id_space": ctx.records.id_space_usage()}

    @router.post("/records/standalone")
    async def standalone(request: Request):
        """@brief 独立备案(不生成水印不可溯源,候选自动剔除)"""
        user, _, error = ctx.bearer_user(request)
        if error:
            return error
        fields, files = await read_any_form(request)
        file_bytes = first_file(files, "file")
        if file_bytes is None:
            return JSONResponse({"error": "缺少 file 文件字段"}, status_code=400)
        pseudo = ctx.records.add_standalone(user["id"], file_bytes, fields)
        ctx.audit.append(user["username"], events.OBJECT_UPLOADED,
                         {"system": "certvault", "standalone": pseudo},
                         "0.0.0.0")
        return {"standalone_id": pseudo}

    @router.post("/records/{tracer_hex}/revoke")
    def revoke(tracer_hex: str, request: Request):
        """@brief 13-R-CV-5 撤销(管理员/发证人;留审计)"""
        user, _, error = ctx.bearer_user(request)
        if error:
            return error
        record = ctx.records.get_by_tracer(tracer_hex)
        if record is None:
            return JSONResponse({"error": "备案不存在"}, status_code=404)
        if record["issuer_id"] != user["id"] and user["role"] != "admin":
            return JSONResponse({"error": "仅发证人或管理员可撤销"},
                                status_code=403)
        try:
            ctx.records.revoke(tracer_hex, user["username"])
        except PolicyValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        ctx.audit.append(user["username"], events.RECORD_REVOKED,
                         {"system": "certvault", "tracer_id": tracer_hex},
                         "0.0.0.0")
        return {"revoked": tracer_hex}

    def _record_gate(tracer_hex: str, request: Request):
        """@brief 备案私有资源闸门(仅发证人/管理员,越权 403)"""
        user, _, error = ctx.bearer_user(request)
        if error:
            return None, None, error
        record = ctx.records.get_by_tracer(tracer_hex)
        if record is None:
            return None, None, JSONResponse({"error": "备案不存在"},
                                            status_code=404)
        if record["issuer_id"] != user["id"] and user["role"] != "admin":
            return None, None, JSONResponse({"error": "无权访问该备案"},
                                            status_code=403)
        return user, record, None

    @router.get("/records/{tracer_hex}/note")
    def record_note(tracer_hex: str, request: Request):
        """@brief 发证笔记(解密返回;仅发证人/管理员)"""
        user, record, error = _record_gate(tracer_hex, request)
        if error:
            return error
        note = ctx.records.get_note(record["id"])
        if note is None:
            return {"note": None}
        return {"note": {"location": _open_text(note["location_ct"]),
                         "text": _open_text(note["text_ct"]),
                         "note_id": note["id"]}}

    @router.get("/records/{tracer_hex}/download")
    def record_download(tracer_hex: str, request: Request):
        """@brief 存档成品下载(内存解密直出)"""
        user, record, error = _record_gate(tracer_hex, request)
        if error:
            return error
        payload = ctx.records.read_archive(record)
        try:
            return Response(content=payload, media_type="image/jpeg")
        finally:
            del payload

    @router.get("/records/note_image/{image_id}")
    def note_image(image_id: int, request: Request):
        """@brief 笔记图(越权 403:经笔记→备案回查归属)"""
        user, _, error = ctx.bearer_user(request)
        if error:
            return error
        rows = ctx.db.query(
            "SELECT ni.blob_path, r.issuer_id FROM cv_note_images ni"
            " JOIN cv_notes n ON n.id = ni.note_id"
            " JOIN cv_records r ON r.id = n.record_id WHERE ni.id = ?",
            (image_id,))
        if not rows:
            return JSONResponse({"error": "笔记图不存在"}, status_code=404)
        blob_path, issuer_id = rows[0]
        if issuer_id != user["id"] and user["role"] != "admin":
            return JSONResponse({"error": "无权访问该笔记图"}, status_code=403)
        payload = ctx.store.open_blob(blob_path, NOTE_AAD)
        try:
            return Response(content=payload, media_type="image/jpeg")
        finally:
            del payload

    def _open_text(ciphertext: str) -> str:
        """@brief 笔记文本解密(空串直通)"""
        if not ciphertext:
            return ""
        return decrypt_envelope(envelope_from_json(ciphertext), ctx.ring,
                                aad=NOTE_AAD).decode()

    return router
