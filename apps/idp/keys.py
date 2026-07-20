# -*- coding: utf-8 -*-
"""
@file    keys.py
@brief   IdP 服务器签名密钥仓(H08 server-keys / ARC-7):RSA-2048 私钥落共享存储、
         kid 稳定(重启不变)、文件 0600;JWKS 发布;RS256 签名。
         国密 SM2 双套预留:TODO(GAP-01) 随 gm Provider 接入同批补齐。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import base64
import hashlib
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

RSA_KEY_BITS = 2048
RSA_PUBLIC_EXPONENT = 65537
KEY_FILE_MODE = 0o600           # H04 §九:密钥文件权限
KID_HEX_LEN = 16                # kid = 公钥摘要前 16 位 hex(稳定且不泄露密钥)
PRIVATE_KEY_FILENAME = "idp_rsa_private.pem"


def _b64url_uint(value: int) -> str:
    """@brief 大整数转 JWK base64url(RFC 7518)"""
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class ServerKeyStore:
    """签名密钥仓:多实例读同一目录,kid 由公钥内容派生(重启/多实例恒定)。"""

    def __init__(self, key_dir: str):
        """@brief 装载或首次生成签名私钥 @param key_dir 共享密钥目录"""
        os.makedirs(key_dir, exist_ok=True)
        self._key_path = os.path.join(key_dir, PRIVATE_KEY_FILENAME)
        if os.path.exists(self._key_path):
            with open(self._key_path, "rb") as handle:
                self._private_key = serialization.load_pem_private_key(
                    handle.read(), password=None)
        else:
            self._private_key = rsa.generate_private_key(
                public_exponent=RSA_PUBLIC_EXPONENT, key_size=RSA_KEY_BITS)
            pem = self._private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption())
            with open(self._key_path, "wb") as handle:
                handle.write(pem)
            os.chmod(self._key_path, KEY_FILE_MODE)
        public_der = self._private_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        self.kid = hashlib.sha256(public_der).hexdigest()[:KID_HEX_LEN]

    def sign_rs256(self, message: bytes) -> bytes:
        """@brief RS256 签名(OIDC id_token/logout_token,H04 §8.1)"""
        return self._private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())

    def jwks(self) -> dict:
        """@brief JWKS 文档(RP 按 kid 验签,套件切换后旧令牌有效期内仍可验)"""
        numbers = self._private_key.public_key().public_numbers()
        return {"keys": [{
            "kty": "RSA", "use": "sig", "alg": "RS256", "kid": self.kid,
            "n": _b64url_uint(numbers.n), "e": _b64url_uint(numbers.e),
        }]}
