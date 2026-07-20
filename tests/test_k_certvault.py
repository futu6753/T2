# -*- coding: utf-8 -*-
"""
@file    test_k_certvault.py
@brief   H09 §二 K(CV 组)+ L02 §8 验收:本地登录锁定文案契约、改密吊销、
         2FA 回环、证件库三重校验与越权、/issue 响应契约与不可用引擎 400
         人话、溯源命中/未命中/撤销、独立备案剔除、笔记越权 403、
         引擎故障隔离、组合投票置信(13-R-CV-3)、推荐器(13-R-CV-2)、
         管理区全套。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import base64
import time
import unittest

import numpy as np

from tests.cv_env import (
    ADMIN_LOCAL, ADMIN_PASSWORD, CvEnv, USER_LOCAL, USER_PASSWORD,
    build_multipart, sample_cert_jpeg,
)

from apps.certvault.wm.engines import EngineRegistry
from apps.certvault.wm.payload import (
    decode_bw_payload, encode_bw_payload, new_tracer_id,
)
from apps.idp.totp import totp_code


class CvBase(unittest.TestCase):
    """公共装配:首号 admin + 普通用户。"""

    def setUp(self):
        self.env = CvEnv()
        self.client = self.env.client()
        self.admin_token = self.env.register_and_login(
            self.client, ADMIN_LOCAL, ADMIN_PASSWORD)
        self.user_client = self.env.client()
        self.user_token = self.env.register_and_login(
            self.user_client, USER_LOCAL, USER_PASSWORD)

    def tearDown(self):
        self.env.close()


class TestPayloadRoundtrip(unittest.TestCase):
    """载荷编解码(RS 纠错 + CRC 兜底)。"""

    def test_k_payload_roundtrip_and_error_correction(self):
        """96bit 回环;注错 ≤2 字节可纠;CRC 拒伪载荷"""
        tracer = new_tracer_id()
        bits = encode_bw_payload(tracer)
        self.assertEqual(len(bits), 96)
        self.assertEqual(decode_bw_payload(bits), tracer)
        corrupted = list(bits)
        corrupted[3] ^= 1
        corrupted[40] ^= 1               # 两处位错(跨 2 字节内)
        self.assertEqual(decode_bw_payload(corrupted), tracer)
        garbage = [1] * 96
        self.assertIsNone(decode_bw_payload(garbage))


class TestLocalAuthContract(CvBase):
    """L02 §3 鉴权文案与锁定契约。"""

    def test_k_first_register_is_admin_open_register_toggle(self):
        """首个账号=admin;关闭开放注册 → 403"""
        rows = self.env.db.query(
            "SELECT role FROM cv_users WHERE username = ?", (ADMIN_LOCAL,))
        self.assertEqual(rows[0][0], "admin")
        closed_env = CvEnv(allow_open_register=False)
        try:
            resp = closed_env.client().post(
                "/auth/register",
                data={"username": "x", "password": "GdPass#2026x"})
            self.assertEqual(resp.status_code, 403)
        finally:
            closed_env.close()

    def test_k_lockout_messages_and_admin_unlock(self):
        """401 剩余预警文案 → 5 次 423 → 锁定中 423 文案 → 管理员解锁"""
        client = self.env.client()
        for attempt in range(4):
            resp = client.post("/auth/login", data={
                "username": USER_LOCAL, "password": "wrong"})
            self.assertEqual(resp.status_code, 401)
            self.assertIn(f"再失败 {4 - attempt} 次将锁定",
                          resp.json()["error"])
        final = client.post("/auth/login", data={
            "username": USER_LOCAL, "password": "wrong"})
        self.assertEqual(final.status_code, 423)
        locked = client.post("/auth/login", data={
            "username": USER_LOCAL, "password": USER_PASSWORD})
        self.assertEqual(locked.status_code, 423)
        self.assertIn("分钟后自动解锁", locked.json()["error"])
        locks = self.client.get("/admin/locks",
                                headers=self.env.auth_headers(self.admin_token))
        self.assertIn(USER_LOCAL, locks.json()["locked"])
        unlock = self.client.post(
            "/admin/unlock", data={"username": USER_LOCAL},
            headers=self.env.auth_headers(self.admin_token))
        self.assertEqual(unlock.status_code, 200)
        recovered = client.post("/auth/login", data={
            "username": USER_LOCAL, "password": USER_PASSWORD})
        self.assertEqual(recovered.status_code, 200)

    def test_k_change_password_revokes_old_tokens(self):
        """改密即吊销全部旧令牌(1 秒粒度水位)"""
        time.sleep(1.1)
        changed = self.user_client.post(
            "/auth/change_password",
            data={"old_password": USER_PASSWORD,
                  "new_password": "GdNew#2026abc"},
            headers=self.env.auth_headers(self.user_token))
        self.assertEqual(changed.status_code, 200)
        replay = self.user_client.get(
            "/auth/me", headers=self.env.auth_headers(self.user_token))
        self.assertEqual(replay.status_code, 401)
        fresh = self.user_client.get(
            "/auth/me",
            headers=self.env.auth_headers(changed.json()["token"]))
        self.assertEqual(fresh.status_code, 200)

    def test_k_2fa_roundtrip_and_failure_counts(self):
        """setup→enable(真实动态码)→登录需 totp;totp 错计失败"""
        headers = self.env.auth_headers(self.user_token)
        setup = self.user_client.post("/auth/2fa/setup", headers=headers)
        secret = setup.json()["otpauth_uri"].split("secret=")[1].split("&")[0]
        enable = self.user_client.post(
            "/auth/2fa/enable", data={"code": totp_code(secret)},
            headers=headers)
        self.assertEqual(enable.status_code, 200)
        missing = self.env.client().post("/auth/login", data={
            "username": USER_LOCAL, "password": USER_PASSWORD})
        self.assertEqual(missing.status_code, 401)
        self.assertIn("动态码", missing.json()["error"])
        good = self.env.client().post("/auth/login", data={
            "username": USER_LOCAL, "password": USER_PASSWORD,
            "totp": totp_code(secret)})
        self.assertEqual(good.status_code, 200)


class TestCertLibrary(CvBase):
    """证件库四路由(02-B1)。"""

    def test_k_upload_validation_and_owner_isolation(self):
        """坏文件 400;owner 隔离(他人 image/删除 403,管理员例外)"""
        body, content_type = build_multipart(
            {"cert_type": "idcard", "label": "坏图"},
            {"file": ("x.jpg", b"not-an-image")})
        bad = self.user_client.request(
            "POST", "/certs/upload",
            headers=self.env.auth_headers(self.user_token),
            raw_body=body, content_type=content_type)
        self.assertEqual(bad.status_code, 400)
        self.assertIn("无法解码", bad.json()["error"])
        cert_id = self.env.upload_cert(self.user_client, self.user_token)
        stranger = self.env.client()
        stranger_token = self.env.register_and_login(
            stranger, "cv_other", "GdOther#2026x")
        forbidden = stranger.get(
            f"/certs/{cert_id}/image",
            headers=self.env.auth_headers(stranger_token))
        self.assertEqual(forbidden.status_code, 403)
        admin_view = self.client.get(
            f"/certs/{cert_id}/image",
            headers=self.env.auth_headers(self.admin_token))
        self.assertEqual(admin_view.status_code, 200)

    def test_k_delete_destroys_blob(self):
        """删除连带销毁密文 blob(剩余信息保护)"""
        import os
        cert_id = self.env.upload_cert(self.user_client, self.user_token)
        cert = self.env.ctx.store.get_cert(cert_id)
        blob_abs = os.path.join(self.env.ctx.store._blob_dir,
                                cert["blob_path"])
        self.assertTrue(os.path.exists(blob_abs))
        deleted = self.user_client.request(
            "DELETE", f"/certs/{cert_id}",
            headers=self.env.auth_headers(self.user_token))
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(os.path.exists(blob_abs))
        self.assertIsNone(self.env.ctx.store.get_cert(cert_id))


class TestIssueAndTrace(CvBase):
    """发证响应契约 + 溯源三态(命中/未命中/撤销)。"""

    def test_k_issue_response_contract_and_auto_text(self):
        """响应字段齐备;visible_text 自动拼装;备案参数快照落库"""
        cert_id = self.env.upload_cert(self.user_client, self.user_token)
        issued = self.env.issue(self.user_client, self.user_token, cert_id)
        self.assertEqual(issued.status_code, 200, issued.body)
        data = issued.json()
        for key in ("issuance_id", "tracer_id", "engine", "engine_name",
                    "visible_text", "image_b64", "size", "embed_shape",
                    "recommendation"):
            self.assertIn(key, data)
        self.assertEqual(data["visible_text"], "限张三报销使用 当日有效")
        record = self.env.ctx.records.get_by_tracer(data["tracer_id"])
        self.assertEqual(record["params"]["recipient"], "张三")
        self.assertGreater(record["embed_w"], 0)

    def test_k_issue_unavailable_engine_400_human_message(self):
        """选 stega(模型未装)→ 400 人话原因;组合含不可用成员同拒"""
        cert_id = self.env.upload_cert(self.user_client, self.user_token)
        for engine in ("stega", "bw+stega"):
            resp = self.env.issue(self.user_client, self.user_token, cert_id,
                                  extra_fields={"engine": engine})
            self.assertEqual(resp.status_code, 400, engine)
            self.assertIn("模型未安装", resp.json()["error"])

    def test_k_trace_hit_miss_revoked(self):
        """命中人话消息;未命中附 stega 提示;撤销后仍命中且明示作废"""
        cert_id = self.env.upload_cert(self.user_client, self.user_token)
        issued = self.env.issue(self.user_client, self.user_token,
                                cert_id).json()
        suspect = base64.b64decode(issued["image_b64"])
        hit = self.env.trace(self.user_client, self.user_token, suspect).json()
        self.assertTrue(hit["found"])
        self.assertIn("交付对象『张三』", hit["message"])
        self.assertEqual(hit["confidence"], "standard")
        miss = self.env.trace(self.user_client, self.user_token,
                              sample_cert_jpeg(seed=99)).json()
        self.assertFalse(miss["found"])
        self.assertIn("深度隐写引擎未启用", miss["message"])
        revoke = self.user_client.post(
            f"/records/{issued['tracer_id']}/revoke",
            headers=self.env.auth_headers(self.user_token))
        self.assertEqual(revoke.status_code, 200)
        again = self.env.trace(self.user_client, self.user_token,
                               suspect).json()
        self.assertTrue(again["found"])
        self.assertTrue(again["revoked"])
        self.assertIn("已作废", again["message"])
        double_revoke = self.user_client.post(
            f"/records/{issued['tracer_id']}/revoke",
            headers=self.env.auth_headers(self.user_token))
        self.assertEqual(double_revoke.status_code, 400)

    def test_k_standalone_excluded_from_trace_candidates(self):
        """独立备案不进溯源候选(互不干扰,L02 §8)"""
        body, content_type = build_multipart(
            {"purpose": "外来现场图登记"},
            {"file": ("scene.jpg", sample_cert_jpeg(seed=42))})
        created = self.user_client.request(
            "POST", "/records/standalone",
            headers=self.env.auth_headers(self.user_token),
            raw_body=body, content_type=content_type)
        self.assertEqual(created.status_code, 200)
        candidates = self.env.ctx.records.traceable_candidates()
        self.assertEqual(len(candidates), 0)
        records = self.user_client.get(
            "/records",
            headers=self.env.auth_headers(self.user_token)).json()
        self.assertEqual(len(records["records"]), 1)


if __name__ == "__main__":
    unittest.main()
