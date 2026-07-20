# -*- coding: utf-8 -*-
"""
@file    tokens.py
@brief   OIDC 令牌铸造(IdP 侧):JWT 编码(id_token / logout_token),签名算法
         随套件——intl=RS256、gm=SM2SM3(H04 §8.1),header 携带 kid。
         验签在 RP 侧库 gd_sso_client.jwt_verify(按 JWKS kid 选钥选算法)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import base64
import json
import secrets
import time

from apps.idp.keys import ServerKeyStore
from gd_crypto.suites import ICryptoSuite, SUITE_GM

JWT_ALG_RS256 = "RS256"
JWT_ALG_SM2SM3 = "SM2SM3"
ID_TOKEN_TTL_SECONDS = 600          # 授权码流中 id_token 短时有效(RP 立即换会话)
BACKCHANNEL_LOGOUT_EVENT = "http://schemas.openid.net/event/backchannel-logout"


def b64url_encode(raw: bytes) -> str:
    """@brief 无填充 base64url 编码(RFC 7515)"""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def encode_jwt(claims: dict, key_store: ServerKeyStore,
               suite: ICryptoSuite = None) -> str:
    """
    @brief  JWT 编码,签名算法随套件(缺省 intl→RS256),header 携带 kid
            (存量兼容与双套件切换依据,H04 §8.2.3)
    @param  claims    载荷声明
    @param  key_store 服务器签名密钥仓
    @param  suite     当前套件(gm→SM2SM3)
    @return JWT 字符串
    """
    use_gm = suite is not None and suite.name == SUITE_GM
    header = {"alg": JWT_ALG_SM2SM3 if use_gm else JWT_ALG_RS256, "typ": "JWT",
              "kid": key_store.sm2_kid if use_gm else key_store.kid}
    signing_input = (b64url_encode(json.dumps(header, separators=(",", ":")).encode())
                     + "." +
                     b64url_encode(json.dumps(claims, separators=(",", ":")).encode()))
    if use_gm:
        signature = key_store.sign_sm2(signing_input.encode("ascii"))
    else:
        signature = key_store.sign_rs256(signing_input.encode("ascii"))
    return signing_input + "." + b64url_encode(signature)


def mint_id_token(issuer: str, client_id: str, user: dict, groups: list,
                  amr: list, nonce: str, key_store: ServerKeyStore,
                  now: float = None, suite: ICryptoSuite = None) -> str:
    """@brief 铸造 id_token(携带 groups/preferred_username/amr,02-A1)"""
    issued_at = int(now if now is not None else time.time())
    claims = {
        "iss": issuer, "aud": client_id, "sub": user["account"],
        "iat": issued_at, "exp": issued_at + ID_TOKEN_TTL_SECONDS,
        "preferred_username": user["display_name"], "groups": groups,
        "amr": amr, "nonce": nonce,
    }
    return encode_jwt(claims, key_store, suite)


def mint_logout_token(issuer: str, client_id: str, account: str,
                      key_store: ServerKeyStore, now: float = None,
                      suite: ICryptoSuite = None) -> str:
    """@brief 铸造 back-channel logout_token(OIDC Back-Channel Logout)"""
    issued_at = int(now if now is not None else time.time())
    claims = {
        "iss": issuer, "aud": client_id, "sub": account,
        "iat": issued_at, "jti": secrets.token_hex(8),
        "events": {BACKCHANNEL_LOGOUT_EVENT: {}},
    }
    return encode_jwt(claims, key_store, suite)
