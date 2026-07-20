# -*- coding: utf-8 -*-
"""
@file    web.py
@brief   CertVault 装配中心(L02 §3,31 条路由分模块挂载):CvContext 服务
         容器、HS256 JWT(主密钥派生)、Bearer 逐请求回库 + iat 吊销水位、
         90 天口令到期业务拦截(SSO 用户豁免,H03 §6)、本地登录 auth 区、
         SSO exchange 特例。证件库/发证/溯源/管理各区见 web_*.py。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import base64
import hashlib
import hmac
import json
import tempfile
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from gd_common.errors import PolicyValidationError
from gd_common.jsonlog import get_logger
from gd_storage.audit import AuditWriter
from apps.certvault.auth_local import LocalAuthService
from apps.certvault.records import RecordService
from apps.certvault.store import CertStore
from apps.certvault.trace import TraceService
from apps.certvault.wm.engines import EngineRegistry
from apps.rp_common.accounts import (
    ROLE_ADMIN, ROLE_USER, RpAccountService, STATUS_ACTIVE,
)
from apps.rp_common.forms import read_form
from apps.rp_common.spa import healthz_extras, mount_spa
from apps.rp_common.sso_routes import build_sso_router, require_session

_log = get_logger("certvault.web")

SYSTEM = "certvault"
COOKIE_NAME = "gd_cv_sid"
JWT_EXPIRE_SECONDS = 3600            # JWT_EXPIRE_MINUTES=60(L02)
JWT_KEY_CONTEXT = b"certvault-jwt-hs256"


def _b64url(data: bytes) -> str:
    """@brief base64url 无填充编码"""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    """@brief base64url 解码(补齐填充)"""
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class CvJwt:
    """certvault 本系统 HS256 JWT(密钥由平台主密钥派生,ARC-4)。"""

    def __init__(self, ring):
        """@brief 由主密钥环派生 HS256 密钥"""
        self._secret = hmac.new(ring.current_key, JWT_KEY_CONTEXT,
                                hashlib.sha256).digest()

    def issue(self, username: str, role: str, now: int = None) -> str:
        """@brief 签发 JWT(sub/role/iat/exp=60 分钟)"""
        issued_at = int(time.time()) if now is None else now
        header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        payload = _b64url(json.dumps({
            "sub": username, "role": role, "iat": issued_at,
            "exp": issued_at + JWT_EXPIRE_SECONDS}).encode())
        signature = _b64url(hmac.new(self._secret,
                                     f"{header}.{payload}".encode(),
                                     hashlib.sha256).digest())
        return f"{header}.{payload}.{signature}"

    def verify(self, token: str, now: int = None) -> dict:
        """@brief 验签+过期检查 @return 声明字典;失败 None"""
        try:
            header, payload, signature = token.split(".")
            expected = _b64url(hmac.new(self._secret,
                                        f"{header}.{payload}".encode(),
                                        hashlib.sha256).digest())
            if not hmac.compare_digest(signature, expected):
                return None
            claims = json.loads(_b64url_decode(payload))
            current = int(time.time()) if now is None else now
            return None if claims.get("exp", 0) <= current else claims
        except (ValueError, KeyError):
            return None


class CvContext:
    """certvault 服务容器(路由模块共享)。"""

    def __init__(self, db, ring, suite, volatile, sso, blob_dir: str,
                 registry: EngineRegistry, allow_open_register: bool):
        """@brief 装配全部服务"""
        self.db = db
        self.ring = ring
        self.suite = suite
        self.sso = sso
        self.cookie_name = sso.config.cookie_name or COOKIE_NAME
        self.jwt = CvJwt(ring)
        self.audit = AuditWriter(db, suite)
        self.accounts = RpAccountService(db, suite, table="cv_users",
                                         allowed_roles=(ROLE_ADMIN, ROLE_USER),
                                         default_role=ROLE_USER,
                                         has_token_valid_after=True)
        self.auth = LocalAuthService(db, ring, suite, volatile, self.audit)
        self.store = CertStore(db, ring, suite, blob_dir)
        self.records = RecordService(db, self.store)
        self.registry = registry
        self.tracer = TraceService(registry, self.records)
        self.allow_open_register = allow_open_register

    # ---- Bearer 鉴权(逐请求回库 + iat 吊销 + 口令到期) ---------------
    def bearer_user(self, request: Request, allow_expired_password=False):
        """
        @brief  Bearer JWT 鉴权链(H03 §3 token_recheck_per_request)
        @return (user, claims, None) 或 (None, None, 错误响应)
        """
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            return None, None, JSONResponse({"error": "未携带令牌"},
                                            status_code=401)
        claims = self.jwt.verify(authorization[len("Bearer "):])
        if claims is None:
            return None, None, JSONResponse({"error": "令牌无效或过期"},
                                            status_code=401)
        user = self.auth._get_local_user(claims["sub"])
        if user is None or user["status"] != STATUS_ACTIVE:
            return None, None, JSONResponse({"error": "账户不可用"},
                                            status_code=403)
        valid_after = self.accounts.token_valid_after(claims["sub"])
        if valid_after:
            from datetime import datetime
            watermark = int(datetime.fromisoformat(valid_after).timestamp())
            if claims["iat"] < watermark:
                return None, None, JSONResponse(
                    {"error": "令牌已被吊销,请重新登录"}, status_code=401)
        if not allow_expired_password and not user["sso_sub"]:
            # 90 天到期/首登强改:业务接口拒绝并引导改密(SSO 用户豁免)
            if user["must_change_password"] or self.auth.password_expired(user):
                return None, None, JSONResponse(
                    {"error": "口令已过期或需首次修改,请先修改口令",
                     "must_change_password": True}, status_code=403)
        return user, claims, None

    def require_admin(self, request: Request):
        """@brief 管理接口闸门 @return (admin_user, None) 或 (None, 错误)"""
        user, _, error = self.bearer_user(request)
        if error:
            return None, error
        if user["role"] != ROLE_ADMIN:
            return None, JSONResponse({"error": "权限不足"}, status_code=403)
        return user, None


def error_response(exc: PolicyValidationError) -> JSONResponse:
    """@brief 策略异常 → 人话 JSON(http_status 默认 400)"""
    return JSONResponse({"error": str(exc)},
                        status_code=getattr(exc, "http_status", 400))


def create_app(db, ring, suite, store, sso, blob_dir: str = None,
               registry: EngineRegistry = None,
               allow_open_register: bool = False,
               profile=None, spa_dist: str = None) -> FastAPI:
    """
    @brief  装配 certvault 应用(31 条路由分区挂载)
    @param  store    易失态(锁定计数/SSO 会话)
    @param  blob_dir 密文 blob 目录(None=临时目录,生产必须指定 data/blobs)
    @param  profile  SecurityProfile(healthz 横切徽标,H11 §二;可缺省)
    @param  spa_dist SPA 构建产物目录(缺省 apps/certvault/web/dist)
    """
    from apps.certvault.web_certs import build_certs_router
    from apps.certvault.web_issue import build_issue_router
    from apps.certvault.web_trace import build_trace_router
    from apps.certvault.web_admin import build_admin_router

    app = FastAPI(title="港电 CertVault", docs_url=None, redoc_url=None)
    ctx = CvContext(db, ring, suite, store, sso,
                    blob_dir or tempfile.mkdtemp(prefix="cv-blobs-"),
                    registry or EngineRegistry(), allow_open_register)
    app.state.ctx = ctx
    app.state.accounts = ctx.accounts      # 兼容里程碑 3 测试装配点
    app.include_router(build_sso_router(sso, ctx.accounts,
                                        cookie_name=ctx.cookie_name,
                                        cookie_secure=sso.config.cookie_secure))
    app.include_router(build_certs_router(ctx))
    app.include_router(build_issue_router(ctx))
    app.include_router(build_trace_router(ctx))
    app.include_router(build_admin_router(ctx))

    # ---- 鉴权区(L02 §3) ----------------------------------------------
    @app.post("/auth/register")
    async def register(request: Request):
        """@brief 开放注册(仅 ALLOW_OPEN_REGISTER=1;首个账号=admin)"""
        if not ctx.allow_open_register:
            return JSONResponse({"error": "开放注册已关闭,请联系管理员建号"},
                                status_code=403)
        form = await read_form(request)
        try:
            user = ctx.auth.register(form.get("username", ""),
                                     form.get("password", ""),
                                     form.get("display_name", ""), "0.0.0.0")
        except PolicyValidationError as exc:
            return error_response(exc)
        return {"username": user["username"], "role": user["role"]}

    @app.post("/auth/login")
    async def login(request: Request):
        """@brief 本地口令登录(锁定 423/剩余预警 401 文案契约)"""
        form = await read_form(request)
        try:
            result = ctx.auth.login(form.get("username", ""),
                                    form.get("password", ""),
                                    form.get("totp", ""), "0.0.0.0")
        except PolicyValidationError as exc:
            return error_response(exc)
        user = result["user"]
        token = ctx.jwt.issue(user["username"], user["role"])
        return {"token": token, "token_type": "Bearer",
                "expires_in": JWT_EXPIRE_SECONDS,
                "totp_enabled": result["totp_enabled"],
                "need_2fa_setup": result["need_2fa_setup"],
                "must_change_password": bool(user["must_change_password"]
                                             or ctx.auth.password_expired(user))}

    @app.post("/auth/change_password")
    async def change_password(request: Request):
        """@brief 改密(旧令牌全吊销;到期强改入口,允许过期口令进入)"""
        user, _, error = ctx.bearer_user(request, allow_expired_password=True)
        if error:
            return error
        form = await read_form(request)
        try:
            ctx.auth.change_password(user["username"],
                                     form.get("old_password", ""),
                                     form.get("new_password", ""), "0.0.0.0")
        except PolicyValidationError as exc:
            return error_response(exc)
        token = ctx.jwt.issue(user["username"], user["role"])
        return {"changed": True, "token": token}

    @app.post("/auth/2fa/setup")
    def twofa_setup(request: Request):
        """@brief 生成 TOTP secret(信封加密存 pending),返回 otpauth URI"""
        user, _, error = ctx.bearer_user(request)
        if error:
            return error
        return {"otpauth_uri": ctx.auth.setup_2fa(user["username"])}

    @app.post("/auth/2fa/enable")
    async def twofa_enable(request: Request):
        """@brief 验证 6 位码后正式启用"""
        user, _, error = ctx.bearer_user(request)
        if error:
            return error
        form = await read_form(request)
        try:
            ctx.auth.enable_2fa(user["username"], form.get("code", ""),
                                "0.0.0.0")
        except PolicyValidationError as exc:
            return error_response(exc)
        return {"totp_enabled": True}

    @app.post("/auth/2fa/disable")
    def twofa_disable(request: Request):
        """@brief 关闭两步验证"""
        user, _, error = ctx.bearer_user(request)
        if error:
            return error
        ctx.auth.disable_2fa(user["username"], "0.0.0.0")
        return {"totp_enabled": False}

    @app.get("/auth/me")
    def auth_me(request: Request):
        """@brief 当前用户信息(角色/2FA 状态)"""
        user, claims, error = ctx.bearer_user(request,
                                              allow_expired_password=True)
        if error:
            return error
        return {"username": user["username"],
                "display_name": user["display_name"], "role": user["role"],
                "totp_enabled": bool(user["totp_enabled"]),
                "is_sso": bool(user["sso_sub"]), "token_iat": claims["iat"]}

    @app.post("/auth/sso/exchange")
    def sso_exchange(request: Request):
        """@brief SSO Cookie 会话 → 本系统 JWT(H08 §3 特例)"""
        user, error = require_session(request, ctx.sso, ctx.accounts,
                                      cookie_name=ctx.cookie_name)
        if error:
            return error
        token = ctx.jwt.issue(user["username"], user["role"])
        return {"token": token, "token_type": "Bearer",
                "expires_in": JWT_EXPIRE_SECONDS, "role": user["role"]}

    @app.get("/health")
    def health():
        """@brief 健康检查含引擎可用性(06-E7)"""
        return {"status": "ok", "system": SYSTEM,
                "sso_enabled": sso.status()["enabled"],
                "engines": ctx.registry.describe_all()}

    @app.get("/healthz")
    def healthz():
        """@brief 统一横切健康检查(兼容里程碑 3 断言)"""
        return {"status": "ok", "system": SYSTEM,
                "sso_enabled": sso.status()["enabled"],
                **healthz_extras(profile)}

    # ---- F2 SPA 静态托管(H11 §一:/app + history 兜底;里程碑 9) ----
    import os as _os
    dist = spa_dist or _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                     "web", "dist")
    mount_spa(app, dist)

    return app
