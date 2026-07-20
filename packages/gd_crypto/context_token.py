# -*- coding: utf-8 -*-
"""
@file    context_token.py
@brief   无状态自包含登录上下文令牌(H02-A3 / H06-E2 红线):
         载荷 base64url + 服务器持久密钥 HMAC 签名,重启/多实例可验签还原;
         过期走"自动续签"语义而非报错死路。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import base64
import hmac as _hmac_mod
import json
import time

from gd_common.errors import ExpiredContextError, InvalidContextError
from gd_crypto.suites import ICryptoSuite

TOKEN_SEP = "."
CLOCK_SKEW_SECONDS = 60      # 允许的时钟偏移(H06-E9:时钟漂移导致"登录即过期")
DEFAULT_CONTEXT_TTL = 1800   # 登录上下文默认有效期 30 分钟(H02-A3)
CLAIM_EXPIRES_AT = "exp"
CLAIM_ISSUED_AT = "iat"


def _b64url_encode(data: bytes) -> str:
    """@brief base64url 无填充编码"""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    """@brief base64url 解码(补齐填充),损坏抛 InvalidContextError"""
    padding = "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(data + padding)
    except Exception as exc:
        raise InvalidContextError("上下文令牌编码损坏") from exc


def issue_context(payload: dict, key: bytes, suite: ICryptoSuite,
                  ttl_seconds: int = DEFAULT_CONTEXT_TTL, now: float = None) -> str:
    """
    @brief  签发无状态上下文令牌:header.payload.sig 三段式,签名算法自描述
    @param  payload     业务载荷(rid/pid 等,不得含口令或密钥)
    @param  key         HMAC 状态密钥(持久化,多实例读同一份,H02-A3)
    @param  suite       当前套件(签名算法随套件)
    @param  ttl_seconds 有效期秒数
    @param  now         当前时间戳(测试注入用)
    @return 令牌字符串
    """
    issued_at = int(time.time() if now is None else now)
    body = dict(payload)
    body[CLAIM_ISSUED_AT] = issued_at
    body[CLAIM_EXPIRES_AT] = issued_at + int(ttl_seconds)
    header_b64 = _b64url_encode(json.dumps({"alg": suite.hmac_alg}).encode("utf-8"))
    payload_b64 = _b64url_encode(
        json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    signing_input = f"{header_b64}{TOKEN_SEP}{payload_b64}".encode("ascii")
    sig_b64 = _b64url_encode(suite.hmac(key, signing_input))
    return f"{header_b64}{TOKEN_SEP}{payload_b64}{TOKEN_SEP}{sig_b64}"


def verify_context(token: str, key: bytes, suite: ICryptoSuite, now: float = None) -> dict:
    """
    @brief  验签并还原上下文载荷。签名不符 → InvalidContextError;
            已过期 → ExpiredContextError(携带载荷,调用方 MUST 自动续签回登录页
            而非报"登录超时"死路,H06-E2)
    @param  token 令牌字符串
    @param  key   HMAC 状态密钥
    @param  suite 当前套件
    @param  now   当前时间戳(测试注入用)
    @return 载荷 dict
    """
    parts = token.split(TOKEN_SEP)
    if len(parts) != 3:
        raise InvalidContextError("上下文令牌结构非法")
    header_b64, payload_b64, sig_b64 = parts
    signing_input = f"{header_b64}{TOKEN_SEP}{payload_b64}".encode("ascii")
    expected_sig = _b64url_encode(suite.hmac(key, signing_input))
    if not _hmac_mod.compare_digest(expected_sig, sig_b64):
        raise InvalidContextError("上下文令牌签名校验失败")
    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except (TypeError, ValueError) as exc:
        raise InvalidContextError("上下文载荷反序列化失败") from exc
    current = time.time() if now is None else now
    if current > payload.get(CLAIM_EXPIRES_AT, 0) + CLOCK_SKEW_SECONDS:
        raise ExpiredContextError(payload)
    return payload


def renew_context(expired_payload: dict, key: bytes, suite: ICryptoSuite,
                  ttl_seconds: int = DEFAULT_CONTEXT_TTL, now: float = None) -> str:
    """
    @brief  过期上下文自动续签:保留业务字段、刷新时间声明并重新签发
    @param  expired_payload ExpiredContextError.payload
    @return 新令牌字符串
    """
    body = {k: v for k, v in expired_payload.items()
            if k not in (CLAIM_ISSUED_AT, CLAIM_EXPIRES_AT)}
    return issue_context(body, key, suite, ttl_seconds, now)
