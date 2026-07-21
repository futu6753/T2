# -*- coding: utf-8 -*-
"""
@file    test_j_pg_redis.py
@brief   H09-J 真实中间件集成(GAP-03/GAP-04 解除组):
         J.1 PostgreSQL 双库——迁移幂等、审计链、R-IDP-2 套件迁移端到端;
         J.4 Redis——跨实例锁定计数累加、宕机 fail-closed 明示不可用。
         需环境:GD_TEST_PG_URL(管理连接串)与 GD_TEST_REDIS_URL;
         缺任一则对应用例跳过(单机离线不阻塞,与 ci_gate 语义一致)。
@author  港电实验室平台组
@date    2026-07-21
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import os
import tempfile
import unittest

from tests.idp_env import IdpEnv, TEST_IP, USER_PASSWORD

from gd_common.errors import StoreUnavailableError
from gd_crypto.migrate import run_migration
from gd_crypto.suites import ALG_SM4_GCM, get_suite, SUITE_GM
from gd_storage import apply_migrations, Database, RedisVolatileStore
from gd_storage.audit import verify_chain

PG_URL = os.environ.get("GD_TEST_PG_URL", "")
REDIS_URL = os.environ.get("GD_TEST_REDIS_URL", "")


def _pg_env() -> IdpEnv:
    """@brief 以 GD_DB_URL=PG 构建 IdP 环境(base.make_db_url 建独立库)"""
    os.environ["GD_DB_URL"] = PG_URL
    try:
        return IdpEnv()
    finally:
        os.environ.pop("GD_DB_URL", None)


@unittest.skipUnless(PG_URL, "未设 GD_TEST_PG_URL,J.1 跳过(GAP-03)")
class TestJ1Postgres(unittest.TestCase):
    """J.1:PostgreSQL 方言下的迁移/审计/套件迁移。"""

    def test_j1_migrations_idempotent_and_audit_chain(self):
        """全量迁移幂等两遍;审计禁改触发器生效;链校验全绿"""
        import psycopg
        import secrets as _secrets
        admin = psycopg.connect(PG_URL, autocommit=True)
        name = "gd_t_" + _secrets.token_hex(6)
        admin.execute(f'CREATE DATABASE "{name}"')
        admin.close()
        db = Database(PG_URL.rsplit("/", 1)[0] + "/" + name)
        apply_migrations(db)
        apply_migrations(db)                      # 幂等重放(H12 §六)
        from gd_storage.audit import AuditWriter
        from gd_crypto.suites import current_suite
        audit = AuditWriter(db, current_suite())
        audit.append("j1", "login_success", {"pg": True}, TEST_IP)
        audit.append("j1", "login_failed", {}, TEST_IP)
        self.assertEqual(verify_chain(db), 2)
        # 只增不改触发器(PG plpgsql 方言)
        with self.assertRaises(Exception):
            db.execute("UPDATE audit_logs SET actor = 'x' WHERE id = 1")

    def test_j1_idp_login_and_suite_migration_on_pg(self):
        """PG 上完整业务链:建号/登录/透明重哈希前提 + R-IDP-2 迁移端到端"""
        env = _pg_env()
        try:
            env.seed_admin_and_user()
            ctx = env.ctx
            for i in range(3):
                ctx.accounts.create_user(f"pgu{i}", f"PG用户{i}", USER_PASSWORD,
                                         ctx.profile, "system", TEST_IP,
                                         phone=f"1770000{i:04d}",
                                         force_change=False)
            outcome, _ = ctx.accounts.password_login_step(
                "pgu0", USER_PASSWORD, ctx.profile, TEST_IP)
            self.assertNotEqual(outcome, "failed")
            report = run_migration(ctx.db, ctx.ring, get_suite(SUITE_GM),
                                   ctx.audit,
                                   state_file=tempfile.mktemp(suffix=".json"))
            self.assertGreaterEqual(
                sum(c["migrated"] for c in report["phases"].values()), 3)
            rows = ctx.db.query("SELECT phone_ct, phone_index FROM idp_users"
                                " WHERE phone_ct IS NOT NULL")
            import json as _json
            for phone_ct, phone_index in rows:
                self.assertEqual(_json.loads(phone_ct)["alg"], ALG_SM4_GCM)
                self.assertTrue(phone_index.startswith("HMAC-SM3$"))
            self.assertGreater(verify_chain(ctx.db), 0)
        finally:
            env.close()


@unittest.skipUnless(REDIS_URL, "未设 GD_TEST_REDIS_URL,J.4 跳过(GAP-04)")
class TestJ4Redis(unittest.TestCase):
    """J.4:真 Redis 跨实例语义与 fail-closed。"""

    def _redis_store(self, url: str) -> RedisVolatileStore:
        """@brief 构造 Redis 易失态(短超时保证宕机快速失败)"""
        import redis
        return RedisVolatileStore(redis.Redis.from_url(
            url, socket_connect_timeout=1, socket_timeout=1))

    def test_j4_lockout_accumulates_across_instances(self):
        """两实例共享 Redis:失败计数跨实例累加至锁定;TTL 自动解锁语义在"""
        shared = self._redis_store(REDIS_URL)
        env1 = IdpEnv(store=shared)
        env1.seed_admin_and_user()
        env2 = IdpEnv(store=shared)          # 第二实例(独立库无妨,锁按账号键)
        ctx1, ctx2 = env1.ctx, env2.ctx
        account = "alice"
        limit = ctx1.profile.max_login_failures
        for i in range(limit):
            target = ctx1 if i % 2 == 0 else ctx2      # 轮流打两实例
            target.accounts.record_failure(account, target.profile, TEST_IP)
        self.assertTrue(ctx1.accounts.is_locked(account))
        self.assertTrue(ctx2.accounts.is_locked(account))   # 跨实例可见
        ctx1.accounts.admin_unlock(account, "j4", TEST_IP)
        self.assertFalse(ctx2.accounts.is_locked(account))
        env1.close()
        env2.close()

    def test_j4_redis_down_fail_closed(self):
        """Redis 宕(死端口):易失态操作明示不可用,MUST NOT 静默降级"""
        dead = self._redis_store("redis://127.0.0.1:6")
        env = IdpEnv(store=dead)
        env.seed_admin_and_user()
        with self.assertRaises(StoreUnavailableError):
            env.ctx.accounts.record_failure("alice", env.ctx.profile, TEST_IP)
        with self.assertRaises(StoreUnavailableError):
            env.ctx.accounts.is_locked("alice")
        env.close()


if __name__ == "__main__":
    unittest.main()
