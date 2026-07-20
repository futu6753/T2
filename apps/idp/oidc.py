# -*- coding: utf-8 -*-
"""
@file    oidc.py
@brief   OIDC 授权码 + PKCE S256 协议引擎(02-A1/A6):授权码一次性防重放、
         PKCE 强制、应用访问控制双点拒绝、id_token 携带 groups/amr、
         access_token 入易失态供 /userinfo、back-channel 登出扇出。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import base64
import hashlib
import json
import secrets

from gd_crypto import hash_password
from gd_crypto.password import verify_password
from gd_storage import make_key
from apps.idp.tokens import mint_id_token, mint_logout_token

AUTH_CODE_TTL_SECONDS = 120        # 授权码短时一次性
ACCESS_TOKEN_TTL_SECONDS = 3600
ERROR_ACCESS_DENIED = "access_denied"      # 标准错误码(02-A6)
ERROR_INVALID_GRANT = "invalid_grant"
ERROR_INVALID_CLIENT = "invalid_client"
ACCESS_POLICY_ALL = "all"
ACCESS_POLICY_GROUPS = "groups"


def _pkce_challenge(verifier: str) -> str:
    """@brief PKCE S256:BASE64URL(SHA256(verifier))"""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class OidcService:
    """OIDC 协议引擎(与 Web 路由解耦,便于回归测试)。"""

    def __init__(self, db, store, key_store, suite, issuer: str):
        """@brief 注入目录库/易失态/签名钥/套件/issuer"""
        self._db, self._store = db, store
        self._keys, self._suite = key_store, suite
        self.issuer = issuer

    # ---- 应用(RP)目录 ---------------------------------------------------
    def create_client(self, client_id: str, name: str, redirect_uris: list,
                      backchannel_url: str = None, access_policy: str = ACCESS_POLICY_ALL,
                      access_groups: list = None) -> str:
        """@brief 注册应用,返回一次性明文密钥(库中仅存哈希)"""
        secret = secrets.token_urlsafe(32)
        self._db.execute(
            "INSERT INTO idp_clients(client_id, secret_hash, name, redirect_uris,"
            " backchannel_url, access_policy, access_groups) VALUES(?,?,?,?,?,?,?)",
            (client_id, hash_password(secret, self._suite), name,
             json.dumps(redirect_uris), backchannel_url,
             access_policy, json.dumps(access_groups or [])))
        return secret

    def get_client(self, client_id: str) -> dict:
        """@brief 读取应用配置"""
        rows = self._db.query(
            "SELECT client_id, secret_hash, name, redirect_uris, backchannel_url,"
            " access_policy, access_groups, enabled FROM idp_clients"
            " WHERE client_id = ?", (client_id,))
        if not rows:
            return None
        keys = ("client_id", "secret_hash", "name", "redirect_uris",
                "backchannel_url", "access_policy", "access_groups", "enabled")
        client = dict(zip(keys, rows[0]))
        client["redirect_uris"] = json.loads(client["redirect_uris"])
        client["access_groups"] = json.loads(client["access_groups"])
        return client

    def check_access(self, client: dict, is_admin: bool, groups: list) -> bool:
        """@brief 应用访问控制(全员/指定组;管理员始终放行,02-A6)"""
        if is_admin or client["access_policy"] == ACCESS_POLICY_ALL:
            return True
        return bool(set(groups) & set(client["access_groups"]))

    # ---- authorize / token / userinfo ------------------------------------
    def issue_auth_code(self, client_id: str, redirect_uri: str, account: str,
                        amr: list, nonce: str, code_challenge: str) -> str:
        """@brief 授权通过后签发一次性授权码(载荷入易失态)"""
        code = secrets.token_urlsafe(32)
        payload = {"client_id": client_id, "redirect_uri": redirect_uri,
                   "account": account, "amr": amr, "nonce": nonce,
                   "code_challenge": code_challenge}
        self._store.set(make_key("idp", "authcode", code), json.dumps(payload),
                        ttl_seconds=AUTH_CODE_TTL_SECONDS)
        return code

    def validate_authorize(self, client_id: str, redirect_uri: str,
                           code_challenge: str, method: str) -> tuple:
        """@brief /authorize 参数校验 @return (client|None, 错误码|None)"""
        client = self.get_client(client_id)
        if client is None or not client["enabled"]:
            return None, ERROR_INVALID_CLIENT
        if redirect_uri not in client["redirect_uris"]:
            return None, ERROR_INVALID_CLIENT       # 回调地址白名单精确匹配
        if not code_challenge or method != "S256":
            return None, ERROR_INVALID_GRANT        # PKCE S256 强制(H00 G1)
        return client, None

    def _authenticate_client(self, client_id: str, client_secret: str) -> dict:
        """@brief token 端点客户端认证(client_secret_post/basic 同入口)"""
        client = self.get_client(client_id)
        if client is None or not client["enabled"]:
            return None
        is_ok, _ = verify_password(client_secret or "", client["secret_hash"],
                                   self._suite)
        return client if is_ok else None

    def exchange_code(self, client_id: str, client_secret: str, code: str,
                      redirect_uri: str, code_verifier: str, user_lookup) -> tuple:
        """
        @brief  授权码换令牌:一次性取出授权码、PKCE 校验、铸 id_token
        @param  user_lookup account → (user dict, groups list)
        @return (响应字典, 错误码|None)
        """
        client = self._authenticate_client(client_id, client_secret)
        if client is None:
            return None, ERROR_INVALID_CLIENT
        code_key = make_key("idp", "authcode", code)
        raw = self._store.get(code_key)
        self._store.delete(code_key)               # 先删后验:授权码严格一次性
        if raw is None:
            return None, ERROR_INVALID_GRANT
        payload = json.loads(raw)
        if (payload["client_id"] != client_id
                or payload["redirect_uri"] != redirect_uri
                or _pkce_challenge(code_verifier or "") != payload["code_challenge"]):
            return None, ERROR_INVALID_GRANT
        user, groups = user_lookup(payload["account"])
        if user is None:
            return None, ERROR_INVALID_GRANT
        id_token = mint_id_token(self.issuer, client_id, user, groups,
                                 payload["amr"], payload["nonce"], self._keys,
                                 suite=self._suite)
        access_token = secrets.token_urlsafe(32)
        self._store.set(make_key("idp", "access", access_token),
                        json.dumps({"account": user["account"], "groups": groups,
                                    "preferred_username": user["display_name"]}),
                        ttl_seconds=ACCESS_TOKEN_TTL_SECONDS)
        return {"access_token": access_token, "token_type": "Bearer",
                "expires_in": ACCESS_TOKEN_TTL_SECONDS, "id_token": id_token}, None

    def userinfo(self, bearer_token: str) -> dict:
        """@brief /userinfo:按 access_token 返回声明(无效返回 None)"""
        raw = self._store.get(make_key("idp", "access", bearer_token))
        if raw is None:
            return None
        info = json.loads(raw)
        return {"sub": info["account"], "preferred_username":
                info["preferred_username"], "groups": info["groups"]}

    # ---- Back-Channel Logout ----------------------------------------------
    def backchannel_fanout(self, account: str, deliver) -> list:
        """
        @brief  向所有配置了 backchannel_url 的应用扇出 logout_token
        @param  deliver callable(url, logout_token)(注入以便测试与重试策略演进)
        @return 已扇出的 client_id 列表
        """
        rows = self._db.query(
            "SELECT client_id, backchannel_url FROM idp_clients"
            " WHERE enabled = 1 AND backchannel_url IS NOT NULL")
        notified = []
        for client_id, url in rows:
            token = mint_logout_token(self.issuer, client_id, account, self._keys,
                                      suite=self._suite)
            deliver(url, token)
            notified.append(client_id)
        return notified

    def discovery_document(self) -> dict:
        """@brief OIDC 发现文档(02-A1 端点契约)"""
        return {
            "issuer": self.issuer,
            "authorization_endpoint": self.issuer + "/authorize",
            "token_endpoint": self.issuer + "/token",
            "userinfo_endpoint": self.issuer + "/userinfo",
            "jwks_uri": self.issuer + "/jwks.json",
            "end_session_endpoint": self.issuer + "/logout",
            "response_types_supported": ["code"],
            "code_challenge_methods_supported": ["S256"],
            "id_token_signing_alg_values_supported": ["RS256"],
        }
