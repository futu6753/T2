# -*- coding: utf-8 -*-
"""
@file    jwt_verify.py
@brief   RP 侧 JWT 验签(RS256,按 JWKS kid 选钥):iss/aud/exp 强校验、
         ±60s 时钟偏移容忍(06-E9);gm 套件 SM2 验签预留 TODO(GAP-01)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import base64
import json
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from gd_common.errors import CryptoError

JWT_CLOCK_SKEW_SECONDS = 60


def _b64url_decode(segment: str) -> bytes:
    """@brief 补齐填充的 base64url 解码"""
    padded = segment + "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(padded)


def _b64url_to_int(segment: str) -> int:
    """@brief base64url → 大整数(JWK n/e)"""
    return int.from_bytes(_b64url_decode(segment), "big")


def _public_key_for_kid(jwks: dict, kid: str):
    """@brief 从 JWKS 按 kid 构建 RSA 公钥"""
    for jwk in jwks.get("keys", []):
        if jwk.get("kid") == kid and jwk.get("kty") == "RSA":
            numbers = rsa.RSAPublicNumbers(_b64url_to_int(jwk["e"]),
                                           _b64url_to_int(jwk["n"]))
            return numbers.public_key()
    raise CryptoError(f"JWKS 中不存在 kid={kid} 的 RSA 公钥")


def verify_jwt(token: str, jwks: dict, issuer: str, audience: str,
               nonce: str = None, now: float = None) -> dict:
    """
    @brief  验签并校验 iss/aud/exp(/nonce),返回声明
    @param  token    JWT 字符串
    @param  jwks     IdP 发布的 JWKS 文档
    @param  issuer   期望 iss
    @param  audience 期望 aud
    @param  nonce    期望 nonce(id_token 传入;logout_token 传 None)
    @return claims 字典
    @raises CryptoError 任一校验失败
    """
    try:
        header_seg, payload_seg, signature_seg = token.split(".")
        header = json.loads(_b64url_decode(header_seg))
        claims = json.loads(_b64url_decode(payload_seg))
    except (ValueError, json.JSONDecodeError) as exc:
        raise CryptoError("JWT 格式非法") from exc
    if header.get("alg") != "RS256":
        # TODO(GAP-01): gm 套件 SM2-with-SM3 验签随国密 Provider 接入
        raise CryptoError(f"不支持的签名算法: {header.get('alg')}")
    public_key = _public_key_for_kid(jwks, header.get("kid"))
    try:
        public_key.verify(_b64url_decode(signature_seg),
                          f"{header_seg}.{payload_seg}".encode("ascii"),
                          padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as exc:
        raise CryptoError("JWT 签名校验失败") from exc
    moment = now if now is not None else time.time()
    if claims.get("iss") != issuer:
        raise CryptoError("JWT iss 不匹配")
    if claims.get("aud") != audience:
        raise CryptoError("JWT aud 不匹配")
    if "exp" in claims and moment > claims["exp"] + JWT_CLOCK_SKEW_SECONDS:
        raise CryptoError("JWT 已过期")
    if nonce is not None and claims.get("nonce") != nonce:
        raise CryptoError("JWT nonce 不匹配(疑似重放)")
    return claims
