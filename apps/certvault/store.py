# -*- coding: utf-8 -*-
"""
@file    store.py
@brief   证件库服务(02-B1):图像信封加密落盘 data/blobs/(每对象独立 DEK,
         主密钥包裹,算法随套件),库存相对路径+SHA-256(H12 §一.4);
         预览/原图内存解密直出,不落明文临时文件;删除连带销毁 blob
         (等保剩余信息保护,H04 §六)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import base64
import hashlib
import os
import secrets
from datetime import datetime, timezone

import numpy as np
import cv2

from gd_common.errors import PolicyValidationError
from gd_crypto import (
    decrypt_envelope, encrypt_envelope, envelope_from_json, envelope_to_json,
)

CERT_TYPES = ("idcard", "driver", "vehicle", "license", "other")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024      # 与网关 client_max_body_size 一致(06-E11)
THUMB_WIDTH = 240


def _now() -> str:
    """@brief UTC ISO 时间"""
    return datetime.now(timezone.utc).isoformat()


class CertStore:
    """证件库:密文 blob + 缩略图 + 内存解密。"""

    def __init__(self, db, ring, suite, blob_dir: str):
        """@brief 注入存储依赖;blob 目录自动创建"""
        self._db = db
        self._ring = ring
        self._suite = suite
        self._blob_dir = blob_dir
        os.makedirs(blob_dir, exist_ok=True)

    # ---- 密文 blob 读写 -------------------------------------------------
    def seal_blob(self, plaintext: bytes, aad: bytes) -> tuple:
        """
        @brief  信封加密并落盘 @return (相对路径, 明文 sha256)
        """
        envelope = encrypt_envelope(plaintext, self._ring, self._suite, aad=aad)
        relative = f"{secrets.token_hex(16)}.env"
        with open(os.path.join(self._blob_dir, relative), "w",
                  encoding="utf-8") as handle:
            handle.write(envelope_to_json(envelope))
        return relative, hashlib.sha256(plaintext).hexdigest()

    def open_blob(self, relative: str, aad: bytes) -> bytes:
        """@brief 读盘解密(明文仅存在于调用方内存周期)"""
        with open(os.path.join(self._blob_dir, relative), "r",
                  encoding="utf-8") as handle:
            envelope = envelope_from_json(handle.read())
        return decrypt_envelope(envelope, self._ring, aad=aad)

    def destroy_blob(self, relative: str):
        """@brief 销毁密文 blob(剩余信息保护:删除记录连带删除派生物)"""
        path = os.path.join(self._blob_dir, relative)
        if os.path.exists(path):
            os.remove(path)

    # ---- 证件 CRUD ------------------------------------------------------
    def upload_cert(self, owner_id: int, cert_type: str, label: str,
                    file_bytes: bytes, is_demo: bool = False) -> dict:
        """
        @brief  上传证件:类型/大小/可解码三重校验(06-E11)→ 信封加密落盘
                → 缩略图入库
        """
        if cert_type not in CERT_TYPES:
            raise PolicyValidationError(f"未知证件类型: {cert_type}")
        if not file_bytes or len(file_bytes) > MAX_UPLOAD_BYTES:
            raise PolicyValidationError("文件为空或超过 20MB 上限")
        image = cv2.imdecode(np.frombuffer(file_bytes, np.uint8),
                             cv2.IMREAD_COLOR)
        if image is None:
            raise PolicyValidationError("文件无法解码为图像")
        thumb = self._make_thumb(image)
        aad = f"cv_cert:{owner_id}".encode()
        relative, digest = self.seal_blob(file_bytes, aad)
        self._db.execute(
            "INSERT INTO cv_certs(owner_id, cert_type, label, blob_path,"
            " blob_sha256, thumb_b64, is_demo, created_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (owner_id, cert_type, label, relative, digest, thumb,
             1 if is_demo else 0, _now()))
        row = self._db.query(
            "SELECT id FROM cv_certs WHERE blob_path = ?", (relative,))
        return {"id": row[0][0], "cert_type": cert_type, "label": label}

    def _make_thumb(self, image: np.ndarray) -> str:
        """@brief 生成缩略图 base64(列表页用,02-B1)"""
        scale = THUMB_WIDTH / image.shape[1]
        thumb = cv2.resize(image, (THUMB_WIDTH, int(image.shape[0] * scale)))
        ok, encoded = cv2.imencode(".jpg", thumb,
                                   [cv2.IMWRITE_JPEG_QUALITY, 70])
        return base64.b64encode(encoded.tobytes()).decode("ascii") if ok else ""

    def list_certs(self, owner_id: int, is_admin: bool) -> list:
        """@brief 列表(owner 隔离,管理员全量;越权控制,H03 §5)"""
        if is_admin:
            rows = self._db.query(
                "SELECT id, owner_id, cert_type, label, thumb_b64, created_at"
                " FROM cv_certs ORDER BY id DESC")
        else:
            rows = self._db.query(
                "SELECT id, owner_id, cert_type, label, thumb_b64, created_at"
                " FROM cv_certs WHERE owner_id = ? ORDER BY id DESC",
                (owner_id,))
        return [{"id": row[0], "owner_id": row[1], "cert_type": row[2],
                 "label": row[3], "thumb_b64": row[4], "created_at": row[5]}
                for row in rows]

    def get_cert(self, cert_id: int) -> dict:
        """@brief 单证件元数据"""
        rows = self._db.query(
            "SELECT id, owner_id, cert_type, label, blob_path FROM cv_certs"
            " WHERE id = ?", (cert_id,))
        if not rows:
            return None
        return {"id": rows[0][0], "owner_id": rows[0][1],
                "cert_type": rows[0][2], "label": rows[0][3],
                "blob_path": rows[0][4]}

    def read_cert_image(self, cert: dict) -> bytes:
        """@brief 内存解密原图字节(调用方 finally del,02-B3)"""
        return self.open_blob(cert["blob_path"],
                              f"cv_cert:{cert['owner_id']}".encode())

    def delete_cert(self, cert: dict):
        """@brief 删除记录连带销毁密文 blob(H04 §六)"""
        self.destroy_blob(cert["blob_path"])
        self._db.execute("DELETE FROM cv_certs WHERE id = ?", (cert["id"],))
