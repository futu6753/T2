# -*- coding: utf-8 -*-
"""
@file    test_crypto.py
@brief   gd_crypto 回归:信封自描述与 GCM 完整性(H09 A.5)、E10 主密钥指引、
         E2 无状态上下文重启存活与过期续签、E9 时钟偏移容忍、透明重哈希
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import base64
import unittest

from tests.base import make_ring, TEST_KEY_HEX

from gd_common.errors import (
    CryptoError,
    ExpiredContextError,
    InvalidContextError,
    MasterKeyMismatchError,
)
from gd_crypto import (
    decrypt_envelope,
    encrypt_envelope,
    envelope_from_json,
    envelope_to_json,
    get_suite,
    hash_password,
    hmac_index,
    hmac_index_matches,
    issue_context,
    MasterKeyRing,
    renew_context,
    SUITE_INTL,
    verify_context,
)
from gd_crypto.context_token import CLOCK_SKEW_SECONDS
from gd_crypto.keyring import DEMO_MASTER_KEY_HEX
from gd_crypto.password import verify_password

PLAINTEXT = "身份证图像字节流示例".encode("utf-8")
CONTEXT_TTL = 1800


class TestEnvelope(unittest.TestCase):
    """密文信封:自描述、完整性、E10 指引。"""

    def setUp(self):
        self.ring = make_ring()
        self.suite = get_suite(SUITE_INTL)

    def test_envelope_roundtrip_self_describing(self):
        """信封往返可解,且携带 alg/kid 自描述元数据(H12 §三.1)"""
        envelope = encrypt_envelope(PLAINTEXT, self.ring, self.suite)
        self.assertEqual(envelope["alg"], "AES-256-GCM")
        self.assertTrue(envelope["kid"])
        self.assertEqual(envelope["wrapped_dek"]["kid"], "mk1")
        restored = envelope_from_json(envelope_to_json(envelope))
        self.assertEqual(decrypt_envelope(restored, self.ring), PLAINTEXT)

    def test_a5_envelope_bitflip_fails(self):
        """密文/标签按位翻转必须解密失败(GCM 完整性,H09 §二 A.5)"""
        for field in ("ct", "tag"):
            envelope = encrypt_envelope(PLAINTEXT, self.ring, self.suite)
            raw = bytearray(base64.b64decode(envelope[field]))
            raw[0] ^= 0x01
            envelope[field] = base64.b64encode(bytes(raw)).decode("ascii")
            with self.assertRaises(CryptoError, msg=f"翻转 {field} 未被检出"):
                decrypt_envelope(envelope, self.ring)

    def test_e10_master_key_mismatch_guidance(self):
        """未知主密钥 kid 给出明确迁移指引而非静默失败(H06-E10)"""
        envelope = encrypt_envelope(PLAINTEXT, self.ring, self.suite)
        other_ring = MasterKeyRing({"mk9": bytes.fromhex("c3" * 32)}, "mk9")
        with self.assertRaises(MasterKeyMismatchError) as ctx:
            decrypt_envelope(envelope, other_ring)
        self.assertIn("rotate_master_key", str(ctx.exception))

    def test_demo_master_key_detected(self):
        """演示派生默认主密钥可被识别(生产前置校验依据,H05 §3.2.5)"""
        demo_ring = MasterKeyRing({"mk1": bytes.fromhex(DEMO_MASTER_KEY_HEX)}, "mk1")
        self.assertTrue(demo_ring.is_demo_key())
        self.assertFalse(make_ring().is_demo_key())


class TestPasswordAndIndex(unittest.TestCase):
    """口令哈希透明重哈希与 HMAC 索引自描述。"""

    def setUp(self):
        self.suite = get_suite(SUITE_INTL)

    def test_password_hash_verify_and_rehash_semantics(self):
        """自描述哈希可验;当前套件下无需重哈希;错误口令拒绝(H04 §8.2.5)"""
        stored = hash_password("Str0ng!Passw0rd", self.suite)
        self.assertTrue(stored.startswith("$argon2id$"))
        is_ok, new_hash = verify_password("Str0ng!Passw0rd", stored, self.suite)
        self.assertTrue(is_ok)
        self.assertIsNone(new_hash)
        is_ok, _ = verify_password("wrong-password", stored, self.suite)
        self.assertFalse(is_ok)

    def test_hmac_index_self_describing_and_match(self):
        """HMAC 索引携带算法前缀,按存量 alg 重算比对(H12 §三.3)"""
        key = bytes.fromhex(TEST_KEY_HEX)
        index = hmac_index("13800000000", key, self.suite)
        self.assertTrue(index.startswith("HMAC-SHA256$"))
        self.assertTrue(hmac_index_matches("13800000000", index, key))
        self.assertFalse(hmac_index_matches("13900000000", index, key))


class TestContextToken(unittest.TestCase):
    """无状态登录上下文(H02-A3 / H06-E2 / H06-E9)。"""

    def setUp(self):
        self.key = bytes.fromhex(TEST_KEY_HEX)
        self.suite = get_suite(SUITE_INTL)
        self.payload = {"rid": "req-001", "client_id": "certvault"}

    def test_e02_context_restart_survival(self):
        """重启/多实例等价:仅凭持久密钥即可验签还原(C08 语义)"""
        token = issue_context(self.payload, self.key, self.suite, CONTEXT_TTL, now=1000.0)
        fresh_suite = get_suite(SUITE_INTL)   # 模拟另一实例/重启后的新进程对象
        restored = verify_context(token, self.key, fresh_suite, now=1100.0)
        self.assertEqual(restored["rid"], "req-001")

    def test_e02_context_expired_auto_renewal(self):
        """过期抛 ExpiredContextError 且携带载荷,自动续签而非死路(C09 语义)"""
        token = issue_context(self.payload, self.key, self.suite, CONTEXT_TTL, now=1000.0)
        expired_at = 1000.0 + CONTEXT_TTL + CLOCK_SKEW_SECONDS + 1
        with self.assertRaises(ExpiredContextError) as ctx:
            verify_context(token, self.key, self.suite, now=expired_at)
        renewed = renew_context(ctx.exception.payload, self.key, self.suite,
                                CONTEXT_TTL, now=expired_at)
        restored = verify_context(renewed, self.key, self.suite, now=expired_at + 1)
        self.assertEqual(restored["client_id"], "certvault")

    def test_e09_clock_skew_tolerated(self):
        """±60s 时钟偏移内不误判过期(H06-E9)"""
        token = issue_context(self.payload, self.key, self.suite, CONTEXT_TTL, now=1000.0)
        within_skew = 1000.0 + CONTEXT_TTL + CLOCK_SKEW_SECONDS - 1
        self.assertEqual(verify_context(token, self.key, self.suite,
                                        now=within_skew)["rid"], "req-001")

    def test_context_signature_tamper_rejected(self):
        """签名被改动即拒绝(与过期语义严格区分)"""
        token = issue_context(self.payload, self.key, self.suite, CONTEXT_TTL)
        tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
        with self.assertRaises(InvalidContextError):
            verify_context(tampered, self.key, self.suite)


if __name__ == "__main__":
    unittest.main()
