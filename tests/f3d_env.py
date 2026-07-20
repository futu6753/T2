# -*- coding: utf-8 -*-
"""
@file    f3d_env.py
@brief   factory-3d 测试基座:IdP + F3D 进程内装配(含主密钥环/统一策略层),
         复用 rp_env 浏览器模拟完成 SSO 登录;提供已登录客户端与上下文直达。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
from tests.idp_env import IdpEnv
from tests.rp_env import drive_sso_login, make_sso_client

from apps.factory3d.web import create_app as create_f3d
from selfcheck.asgi import AsgiClient

F3D_REDIRECT = "http://f3d.gd.test/sso/callback"
# 应急通道令牌(测试常量,<16 字符;secretscan:allow)
EMERGENCY_TOKEN = "f3d-tok-1"


class F3dEnv:
    """IdP + factory-3d 双系统装配(单进程,共享 IdP 库表)。"""

    def __init__(self, admin_token: str = EMERGENCY_TOKEN):
        """@brief 建 IdP、注册 f3d 客户端、装配应用"""
        self.idp = IdpEnv()
        self.idp.seed_admin_and_user()
        self.sso, self.rp_store, self.secret = make_sso_client(
            self.idp, "f3d", "factory3d", F3D_REDIRECT)
        self.app = create_f3d(self.idp.ctx.db, self.idp.ctx.suite, self.sso,
                              admin_token=admin_token, ring=self.idp.ctx.ring,
                              environ={})
        self.ctx = self.app.state.f3d

    def client(self) -> AsgiClient:
        """@brief 匿名客户端"""
        return AsgiClient(self.app)

    def logged_in(self, next_path: str = "/") -> AsgiClient:
        """@brief 完成 SSO 登录的客户端(默认普通账号 → operator 角色)"""
        client = self.client()
        drive_sso_login(self.idp, client, next_path=next_path)
        return client
