# -*- coding: utf-8 -*-
"""
@file    idp_env.py
@brief   IdP 测试环境构造器:临时库+临时密钥目录+本地易失态,按模式构建应用;
         同一目录重建即模拟"重启/多实例"(C08/C09 等价复现前提)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import tempfile

from tests.base import make_db_url, TEST_KEY_HEX

from gd_storage import LocalVolatileStore
from apps.idp.context import IdpContext
from apps.idp.web import create_app
from selfcheck.asgi import AsgiClient

ADMIN_ACCOUNT = "op_admin"
ADMIN_PASSWORD = "Adm1n!Passw0rd"
USER_ACCOUNT = "alice"
USER_PASSWORD = "Alice!Passw0rd9"
TEST_IP = "10.0.0.1"


class IdpEnv:
    """一套可重启的 IdP 测试环境(db/keys/store 跨"重启"保持)。"""

    def __init__(self, is_demo: bool = False, extra_environ: dict = None,
                 store=None):
        """@brief 首次构建环境 @param extra_environ 追加环境(如 CRYPTO_SUITE=gm)
        @param store 易失态注入(J.4 Redis 集成用;缺省本地单机)"""
        self.db_url = make_db_url()          # GD_DB_URL 有则 PG 独立库(J.1)
        self.key_dir = tempfile.mkdtemp(prefix="idp-keys-")
        self.store = LocalVolatileStore() if store is None else store
        self.is_demo = is_demo
        self.extra_environ = dict(extra_environ or {})
        self.ctx = None
        self.restart()

    def _environ(self) -> dict:
        """@brief 组装环境变量(测试主密钥;DEMO 按需)"""
        environ = {"MASTER_KEY_HEX": TEST_KEY_HEX, "MASTER_KEY_ID": "mk1"}
        if self.is_demo:
            environ["DEMO_MODE"] = "1"
        environ.update(self.extra_environ)
        return environ

    def restart(self):
        """@brief 重建应用(模拟进程重启:store/db/keys 持久,进程对象全新)"""
        if self.ctx is not None:
            self.ctx.close()
        self.ctx = IdpContext(self.db_url, self.key_dir,
                              store=self.store, environ=self._environ())
        self.app = create_app(self.ctx)
        return self

    def client(self) -> AsgiClient:
        """@brief 新建独立 Cookie 罐的客户端"""
        return AsgiClient(self.app)

    def seed_admin_and_user(self):
        """@brief 播种一名管理员与一名普通用户(不触发首登强改)"""
        self.ctx.accounts.create_user(ADMIN_ACCOUNT, "运维管理员", ADMIN_PASSWORD,
                                      self.ctx.profile, "system", TEST_IP,
                                      is_admin=True, force_change=False)
        self.ctx.accounts.create_user(USER_ACCOUNT, "张三", USER_PASSWORD,
                                      self.ctx.profile, "system", TEST_IP,
                                      force_change=False)

    def login(self, client: AsgiClient, account: str, password: str,
              extra: dict = None):
        """@brief 表单登录并返回响应(Cookie 落客户端)"""
        data = {"account": account, "password": password}
        data.update(extra or {})
        return client.post("/login", data=data)

    def close(self):
        """@brief 释放资源"""
        self.ctx.close()
