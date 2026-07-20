# -*- coding: utf-8 -*-
"""
@file    cv_env.py
@brief   certvault 测试基座:IdP+certvault 装配、multipart 请求体构造、
         小尺寸测试证件图(控制 SVD 流水线耗时)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import secrets

import numpy as np
import cv2

from tests.idp_env import IdpEnv
from tests.rp_env import make_sso_client

from apps.certvault.web import create_app as create_certvault
from selfcheck.asgi import AsgiClient

CV_REDIRECT = "http://cv.internal/sso/callback"
ADMIN_LOCAL = "cv_boss"
ADMIN_PASSWORD = "GdAdmin#2026x"
USER_LOCAL = "cv_user"
USER_PASSWORD = "GdUser#2026yz"


def build_multipart(fields: dict, files: dict = None) -> tuple:
    """
    @brief  构造 multipart 请求体
    @param  files {字段: (文件名, bytes)}
    @return (body, content_type)
    """
    boundary = "gdtest" + secrets.token_hex(8)
    chunks = []
    for name, value in (fields or {}).items():
        chunks.append(
            (f"--{boundary}\r\nContent-Disposition: form-data;"
             f' name="{name}"\r\n\r\n{value}\r\n').encode("utf-8"))
    for name, (filename, payload) in (files or {}).items():
        chunks.append(
            (f"--{boundary}\r\nContent-Disposition: form-data;"
             f' name="{name}"; filename="{filename}"\r\n'
             "Content-Type: application/octet-stream\r\n\r\n").encode("utf-8")
            + payload + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def sample_cert_jpeg(seed: int = 5, size: tuple = (320, 480)) -> bytes:
    """@brief 生成小尺寸测试证件图(低频纹理+文字,保证 bw 可嵌)"""
    rng = np.random.default_rng(seed)
    gray = cv2.GaussianBlur(
        (rng.random(size) * 170 + 50).astype(np.uint8), (31, 31), 9)
    image = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    cv2.putText(image, "GD TEST CERT", (20, size[0] // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (30, 30, 30), 2)
    ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return buffer.tobytes()


class CvEnv:
    """certvault 端到端环境(本地登录体系为主,SSO 可选)。"""

    def __init__(self, allow_open_register: bool = True, registry=None):
        """@brief 装配 IdP(供 SSO 场景)与 certvault 应用"""
        self.idp = IdpEnv(is_demo=False)
        self.idp.seed_admin_and_user()
        self.sso, self.rp_store, _ = make_sso_client(
            self.idp, "certvault", "certvault", CV_REDIRECT)
        ctx = self.idp.ctx
        self.app = create_certvault(ctx.db, ctx.ring, ctx.suite,
                                    self.rp_store, self.sso,
                                    registry=registry,
                                    allow_open_register=allow_open_register)
        self.ctx = self.app.state.ctx
        self.db = ctx.db

    def client(self) -> AsgiClient:
        """@brief 新客户端"""
        return AsgiClient(self.app)

    def register_and_login(self, client: AsgiClient, username: str,
                           password: str) -> str:
        """@brief 注册并登录 @return Bearer token"""
        resp = client.post("/auth/register",
                           data={"username": username, "password": password,
                                 "display_name": username})
        assert resp.status_code == 200, resp.body
        login = client.post("/auth/login",
                            data={"username": username, "password": password})
        assert login.status_code == 200, login.body
        return login.json()["token"]

    def auth_headers(self, token: str) -> dict:
        """@brief Bearer 头"""
        return {"Authorization": f"Bearer {token}"}

    def upload_cert(self, client: AsgiClient, token: str,
                    label: str = "身份证-正面", seed: int = 5,
                    size: tuple = (320, 480)) -> int:
        """@brief 上传测试证件 @return cert_id(size 大图=更高冗余)"""
        body, content_type = build_multipart(
            {"cert_type": "idcard", "label": label},
            {"file": ("cert.jpg", sample_cert_jpeg(seed, size))})
        resp = client.request("POST", "/certs/upload",
                              headers=self.auth_headers(token),
                              raw_body=body, content_type=content_type)
        assert resp.status_code == 200, resp.body
        return resp.json()["id"]

    def issue(self, client: AsgiClient, token: str, cert_id: int,
              extra_fields: dict = None, files: dict = None):
        """@brief 发证 @return 响应对象"""
        fields = {"cert_id": str(cert_id), "recipient": "张三",
                  "purpose": "报销使用", "engine": "bw"}
        fields.update(extra_fields or {})
        body, content_type = build_multipart(fields, files)
        return client.request("POST", "/issue",
                              headers=self.auth_headers(token),
                              raw_body=body, content_type=content_type)

    def trace(self, client: AsgiClient, token: str, image_bytes: bytes,
              medium: str = ""):
        """@brief 溯源 @return 响应对象"""
        fields = {"medium": medium} if medium else {}
        body, content_type = build_multipart(
            fields, {"file": ("suspect.jpg", image_bytes)})
        return client.request("POST", "/trace",
                              headers=self.auth_headers(token),
                              raw_body=body, content_type=content_type)

    def close(self):
        """@brief 释放资源"""
        self.idp.close()
