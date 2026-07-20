# -*- coding: utf-8 -*-
"""
@file    web_certs.py
@brief   证件库四路由(L02 §3):multipart 上传(类型/大小/可解码三重校验)、
         列表含缩略图、原图内存解密直出、删除连带销毁密文 blob。
         owner 隔离,越权 403(管理员例外,H03 §5)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import base64

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from gd_common.errors import PolicyValidationError
from gd_storage import events
from apps.rp_common.forms import read_any_form
from apps.certvault.store import CERT_TYPES
from apps.rp_common.multipart import first_file


# SPA 类型输入为自由文本(placeholder"如:出入证"),后端枚举严格——
# web 层归一化承接(浏览器全链路 E2E 发现,里程碑 10):
# 关键词映射到既有枚举,未识别归 other 并将原文并入 label 保留语义。
_CERT_TYPE_KEYWORDS = (("身份证", "idcard"), ("驾驶", "driver"),
                       ("驾照", "driver"), ("行驶", "vehicle"),
                       ("执照", "license"), ("许可", "license"),
                       ("资格", "license"))


def normalize_cert_type(raw: str, label: str) -> tuple:
    """@brief 自由文本类型 → (枚举类型, label) @return 归一化结果"""
    value = (raw or "").strip()
    if value in CERT_TYPES:
        return value, label
    for keyword, enum_type in _CERT_TYPE_KEYWORDS:
        if keyword in value:
            return enum_type, label
    merged = label if not value else (f"{value}·{label}" if label else value)
    return "other", merged


def build_certs_router(ctx) -> "APIRouter":
    """@brief 证件库路由(CvContext 注入)"""
    router = APIRouter()

    @router.post("/certs/upload")
    async def upload_cert(request: Request):
        """@brief 上传证件(multipart: cert_type/label/file)"""
        user, _, error = ctx.bearer_user(request)
        if error:
            return error
        fields, files = await read_any_form(request)
        file_bytes = first_file(files, "file")
        if file_bytes is None:
            return JSONResponse({"error": "缺少 file 文件字段"}, status_code=400)
        cert_type, label = normalize_cert_type(fields.get("cert_type", ""),
                                               fields.get("label", ""))
        try:
            cert = ctx.store.upload_cert(user["id"], cert_type, label,
                                         file_bytes)
        except PolicyValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        ctx.audit.append(user["username"], events.OBJECT_UPLOADED,
                         {"system": "certvault", "cert_id": cert["id"],
                          "cert_type": cert["cert_type"]}, "0.0.0.0")
        return cert

    @router.get("/certs")
    def list_certs(request: Request):
        """@brief 列表含缩略图(owner 隔离,管理员全量)"""
        user, _, error = ctx.bearer_user(request)
        if error:
            return error
        return {"certs": ctx.store.list_certs(user["id"],
                                              user["role"] == "admin")}

    @router.get("/certs/{cert_id}/image")
    def cert_image(cert_id: int, request: Request):
        """@brief 原图内存解密直出(不落明文临时文件,02-B3)"""
        user, _, error = ctx.bearer_user(request)
        if error:
            return error
        cert = ctx.store.get_cert(cert_id)
        if cert is None:
            return JSONResponse({"error": "证件不存在"}, status_code=404)
        if cert["owner_id"] != user["id"] and user["role"] != "admin":
            return JSONResponse({"error": "无权访问该证件"}, status_code=403)
        plain = ctx.store.read_cert_image(cert)
        try:
            return Response(content=plain, media_type="image/jpeg")
        finally:
            del plain                      # 明文即用即释(等保剩余信息保护)

    @router.delete("/certs/{cert_id}")
    def delete_cert(cert_id: int, request: Request):
        """@brief 删除证件连带销毁密文 blob(H04 §六)"""
        user, _, error = ctx.bearer_user(request)
        if error:
            return error
        cert = ctx.store.get_cert(cert_id)
        if cert is None:
            return JSONResponse({"error": "证件不存在"}, status_code=404)
        if cert["owner_id"] != user["id"] and user["role"] != "admin":
            return JSONResponse({"error": "无权删除该证件"}, status_code=403)
        ctx.store.delete_cert(cert)
        ctx.audit.append(user["username"], events.OBJECT_DELETED,
                         {"system": "certvault", "cert_id": cert_id}, "0.0.0.0")
        return {"deleted": cert_id}

    return router
