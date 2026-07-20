# -*- coding: utf-8 -*-
"""
@file    context.py
@brief   IdP 应用上下文:装配共享库(db/settings/profile/密钥环/套件/易失态/审计/
         签名钥/账户/会话/OIDC),模式热切换时整体重建生效快照(H05 §1.2)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import os

from gd_crypto import current_suite, MasterKeyRing
from gd_policy import resolve_profile, SettingsService
from gd_storage import apply_migrations, AuditWriter, Database, LocalVolatileStore
from apps.idp.accounts import AccountService
from apps.idp.keys import ServerKeyStore
from apps.idp.oidc import OidcService
from apps.idp.sessions import SessionService

DEFAULT_ISSUER = "http://127.0.0.1:9000"


class IdpContext:
    """IdP 运行上下文(单实例装配一次;profile 为不可变快照,业务只读)。"""

    def __init__(self, db_url: str, key_dir: str, store=None, environ=None,
                 config_file: str = None, issuer: str = None):
        """@brief 装配全部依赖;易失态可注入(生产 Redis / 测试本地)"""
        self.environ = dict(os.environ if environ is None else environ)
        self.db = Database(db_url)
        apply_migrations(self.db)
        self.store = store if store is not None else LocalVolatileStore()
        self.settings = SettingsService(self.db, config_file=config_file,
                                        environ=self.environ)
        self.ring = MasterKeyRing.from_env(self.environ)
        self.suite = current_suite(self.environ)
        self.audit = AuditWriter(self.db, self.suite)
        self.keys = ServerKeyStore(key_dir)
        self.issuer = issuer or self.environ.get("IDP_ISSUER", DEFAULT_ISSUER)
        self.profile = resolve_profile(self.settings, environ=self.environ)
        self._rebuild_services()

    def _rebuild_services(self):
        """@brief 按当前 profile 重建各服务(会话超时等参数随快照生效)"""
        self.accounts = AccountService(self.db, self.ring, self.suite,
                                       self.store, self.audit)
        self.sessions = SessionService(self.store, self.profile)
        self.oidc = OidcService(self.db, self.store, self.keys, self.suite,
                                self.issuer)

    def refresh_profile(self, environ_override: dict = None):
        """@brief 重新解析生效策略快照(模式热切换入口,H05 §3)"""
        if environ_override is not None:
            self.environ = dict(environ_override)
        self.profile = resolve_profile(self.settings, environ=self.environ)
        self._settings_version = self.settings.version()
        self._rebuild_services()
        return self.profile

    def maybe_refresh(self):
        """
        @brief  设置版本轮询:他实例改配置后本实例 ≤ 下一请求即生效
                (09 §二 G.4 策略热更新全实例传播;版本号在共享库)
        """
        latest = self.settings.version()
        if latest != getattr(self, "_settings_version", 0):
            self.refresh_profile()

    def user_lookup(self, account: str) -> tuple:
        """@brief OIDC 换令牌时的用户回查(account → (user, groups))"""
        user = self.accounts.get_user(account)
        if user is None:
            return None, []
        return user, self.accounts.user_groups(user["id"])

    def close(self):
        """@brief 释放数据库连接"""
        self.db.close()
