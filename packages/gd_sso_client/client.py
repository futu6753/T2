# -*- coding: utf-8 -*-
"""
@file    client.py
@brief   SsoClient 实现(H08 §3 契约,解除 GAP-05):state 一次性+10 分钟过期、
         nonce 绑定防重放、PKCE S256、回跳 next 仅站内相对路径、
         back-channel 即刻吊销全部本地会话;传输层可注入(离线测试直连 ASGI)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import base64
import hashlib
import json
import secrets
import time
import urllib.parse

from gd_common.errors import CryptoError, PolicyValidationError
from gd_sso_client import SsoConfig
from gd_sso_client.jwt_verify import verify_jwt
from gd_storage import make_key

STATE_TTL_SECONDS = 600            # state 一次性 + 10 分钟过期(H08 §3)
RP_SESSION_PREFIX = "sess"       # gd:{rp}:sess:{sid}(H12 §五)
RP_SUB_INDEX_PREFIX = "subsess"  # sub → 会话集索引(backchannel 吊销用)
DEFAULT_PORTAL_PATH = "/"


def load_config(environ: dict) -> SsoConfig:
    """@brief 从环境变量装载 RP 配置(缺任一必填项则 SSO 不启用)"""
    required = [environ.get(key, "") for key in
                ("SSO_ISSUER", "SSO_CLIENT_ID", "SSO_CLIENT_SECRET", "SSO_REDIRECT")]
    is_enabled = all(required)
    return SsoConfig(
        issuer=required[0], client_id=required[1], client_secret=required[2],
        redirect_uri=required[3],
        scopes=environ.get("SSO_SCOPES", "openid profile"),
        session_ttl_seconds=int(environ.get("SSO_SESSION_TTL", "28800")),
        cookie_name=environ.get("SSO_COOKIE_NAME", ""),
        cookie_secure=environ.get("SSO_COOKIE_SECURE", "1") == "1",
        post_logout=environ.get("SSO_POST_LOGOUT", "/"),
        default_role=environ.get("SSO_DEFAULT_ROLE", ""),
        is_enabled=is_enabled)


def _pkce_pair() -> tuple:
    """@brief 生成 PKCE (verifier, S256 challenge)"""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _is_safe_next(next_path: str) -> bool:
    """@brief 回跳只接受站内相对路径(防开放重定向,06-E13)"""
    return bool(next_path) and next_path.startswith("/") \
        and not next_path.startswith("//")


class SsoClient:
    """RP 侧统一 OIDC 客户端(实现 ISsoClient 协议)。"""

    def __init__(self, config: SsoConfig, store, transport, system: str = "rp"):
        """
        @brief  注入配置/易失态/传输层
        @param  transport callable(method, url, headers, body) → (status, dict, bytes)
        """
        self._config = config
        self._store = store
        self._transport = transport
        self._system = system            # Redis 键空间系统段(H12 §五)
        self._jwks_cache = None

    # ---- 端点发现与 JWKS ---------------------------------------------------
    def _endpoints(self) -> dict:
        """@brief OIDC 发现;失败回退约定路径(H08 §3)"""
        url = self._config.issuer + "/.well-known/openid-configuration"
        status, _, body = self._transport("GET", url, None, None)
        if status == 200:
            return json.loads(body)
        return {"authorization_endpoint": self._config.issuer + "/authorize",
                "token_endpoint": self._config.issuer + "/token",
                "jwks_uri": self._config.issuer + "/jwks.json"}

    def _jwks(self) -> dict:
        """@brief 获取并缓存 JWKS(只读缓存,ARC-7 允许)"""
        if self._jwks_cache is None:
            status, _, body = self._transport("GET", self._endpoints()["jwks_uri"],
                                              None, None)
            if status != 200:
                raise CryptoError("获取 JWKS 失败")
            self._jwks_cache = json.loads(body)
        return self._jwks_cache

    # ---- 契约五路由语义 ----------------------------------------------------
    @property
    def config(self) -> SsoConfig:
        """@brief 只读配置(RP 装配层取 cookie/secure 选项)"""
        return self._config

    def status(self) -> dict:
        """@brief /sso/status:登录页据此显隐按钮"""
        return {"enabled": self._config.is_enabled}

    def build_login_redirect(self, next_path: str) -> str:
        """@brief 生成授权跳转 URL(state/nonce/PKCE 入易失态)"""
        if not self._config.is_enabled:
            raise PolicyValidationError("SSO 未启用(必填 env 不全)")
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        verifier, challenge = _pkce_pair()
        safe_next = next_path if _is_safe_next(next_path) else DEFAULT_PORTAL_PATH
        self._store.set(make_key(self._system, "state", state),
                        json.dumps({"nonce": nonce, "verifier": verifier,
                                    "next": safe_next}),
                        ttl_seconds=STATE_TTL_SECONDS)
        params = urllib.parse.urlencode({
            "response_type": "code", "client_id": self._config.client_id,
            "redirect_uri": self._config.redirect_uri, "scope": self._config.scopes,
            "state": state, "nonce": nonce, "code_challenge": challenge,
            "code_challenge_method": "S256"})
        return f"{self._endpoints()['authorization_endpoint']}?{params}"

    def handle_callback(self, query: dict) -> dict:
        """
        @brief  回调:state 一次性取出→换令牌→验签→建本地会话
        @return {claims, session_id, next}
        """
        state_key = make_key(self._system, "state", query.get("state", ""))
        raw = self._store.get(state_key)
        self._store.delete(state_key)              # 先删后用:state 严格一次性
        if raw is None:
            raise PolicyValidationError("state 无效或已使用(疑似 CSRF/重放)")
        state_record = json.loads(raw)
        body = urllib.parse.urlencode({
            "grant_type": "authorization_code", "code": query.get("code", ""),
            "redirect_uri": self._config.redirect_uri,
            "client_id": self._config.client_id,
            "client_secret": self._config.client_secret,
            "code_verifier": state_record["verifier"]}).encode("ascii")
        status, _, resp = self._transport(
            "POST", self._endpoints()["token_endpoint"],
            {"content-type": "application/x-www-form-urlencoded"}, body)
        if status != 200:
            raise CryptoError(f"令牌交换失败(HTTP {status})")
        token_set = json.loads(resp)
        claims = verify_jwt(token_set["id_token"], self._jwks(),
                            issuer=self._config.issuer,
                            audience=self._config.client_id,
                            nonce=state_record["nonce"])
        session_id = self._create_session(claims)
        return {"claims": claims, "session_id": session_id,
                "next": state_record["next"]}

    def _create_session(self, claims: dict) -> str:
        """@brief 建 RP 本地会话并维护 sub→sid 索引(back-channel 吊销依据)"""
        session_id = secrets.token_urlsafe(32)
        ttl = self._config.session_ttl_seconds
        self._store.set(make_key(self._system, RP_SESSION_PREFIX, session_id),
                        json.dumps({"sub": claims["sub"],
                                    "groups": claims.get("groups", []),
                                    "created": time.time()}), ttl_seconds=ttl)
        index_key = make_key(self._system, RP_SUB_INDEX_PREFIX, claims["sub"])
        raw = self._store.get(index_key)
        sids = json.loads(raw) if raw else []
        sids.append(session_id)
        self._store.set(index_key, json.dumps(sids), ttl_seconds=ttl)
        return session_id

    def get_session(self, session_id: str) -> dict:
        """@brief 取 RP 本地会话(TTL 由存储层保证)"""
        raw = self._store.get(make_key(self._system, RP_SESSION_PREFIX, session_id or ""))
        return json.loads(raw) if raw else None

    def revoke_session(self, session_id: str):
        """@brief 注销单个 RP 本地会话(/sso/logout 与映射失败回滚用)"""
        session = self.get_session(session_id)
        self._store.delete(make_key(self._system, RP_SESSION_PREFIX, session_id))
        if session:
            index_key = make_key(self._system, RP_SUB_INDEX_PREFIX, session["sub"])
            raw = self._store.get(index_key)
            remaining = [sid for sid in (json.loads(raw) if raw else [])
                         if sid != session_id]
            if remaining:
                self._store.set(index_key, json.dumps(remaining),
                                self._config.session_ttl_seconds)
            else:
                self._store.delete(index_key)

    def post_logout_url(self) -> str:
        """@brief 注销后跳转地址(SSO_POST_LOGOUT,默认站内首页)"""
        target = self._config.post_logout or "/"
        return target if _is_safe_next(target) or "://" in target else "/"

    def handle_backchannel_logout(self, logout_token: str) -> str:
        """@brief 验 logout_token 并即刻吊销该用户全部本地会话(H08 §3)"""
        claims = verify_jwt(logout_token, self._jwks(),
                            issuer=self._config.issuer,
                            audience=self._config.client_id, nonce=None)
        if "events" not in claims:
            raise CryptoError("logout_token 缺少 events 声明")
        sub = claims["sub"]
        index_key = make_key(self._system, RP_SUB_INDEX_PREFIX, sub)
        raw = self._store.get(index_key)
        for session_id in (json.loads(raw) if raw else []):
            self._store.delete(make_key(self._system, RP_SESSION_PREFIX, session_id))
        self._store.delete(index_key)
        return sub
