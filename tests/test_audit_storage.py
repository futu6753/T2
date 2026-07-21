# -*- coding: utf-8 -*-
"""
@file    test_audit_storage.py
@brief   审计链与存储层回归:只增不改触发器、链校验与篡改检出(H09 A.4)、
         迁移幂等(J.3)、易失态存储 TTL 与 Redis fail-closed(J.4)
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import sqlite3
import unittest

from tests.base import make_temp_db

from gd_common.errors import AuditTamperError, StoreUnavailableError
from gd_crypto import get_suite, SUITE_INTL
from gd_storage import (
    apply_migrations,
    AuditWriter,
    LocalVolatileStore,
    make_key,
    RedisVolatileStore,
    verify_chain,
)
from gd_storage import events

SAMPLE_IP = "10.0.0.8"


class TestAuditChain(unittest.TestCase):
    """审计表防篡改(H12 §四)。"""

    def setUp(self):
        self.db = make_temp_db()
        self.writer = AuditWriter(self.db, get_suite(SUITE_INTL))

    def tearDown(self):
        self.db.close()

    def _append_samples(self):
        """@brief 写入三条样例审计"""
        self.writer.append("admin", events.LOGIN_SUCCESS, {"method": "password"}, SAMPLE_IP)
        self.writer.append("admin", events.SETTINGS_CHANGED,
                           {"key": "lockout_minutes", "old": 15, "new": 30}, SAMPLE_IP)
        self.writer.append("system", events.MODE_CHANGED,
                           {"from": "demo", "to": "prod"}, SAMPLE_IP)

    def test_a4_audit_chain_append_and_verify(self):
        """链式哈希连续且逐条携带 alg,全链校验通过(H09 A.4)"""
        self._append_samples()
        self.assertEqual(verify_chain(self.db), 3)
        rows = self.db.query("SELECT alg FROM audit_logs")
        self.assertTrue(all(row[0] == "SHA-256" for row in rows))

    def test_a4_audit_update_delete_rejected(self):
        """触发器拒绝 UPDATE/DELETE(只增不改,H09 A.4 / J.2)"""
        self._append_samples()
        from gd_storage import DB_ERRORS         # 双方言异常(J.1/J.2)
        with self.assertRaises(DB_ERRORS):
            self.db.execute("UPDATE audit_logs SET actor = 'evil' WHERE id = 1")
        with self.assertRaises(DB_ERRORS):
            self.db.execute("DELETE FROM audit_logs WHERE id = 1")

    def test_a4_audit_tamper_detected(self):
        """伪造插入(绕过写入器)的不一致记录被链校验检出"""
        self._append_samples()
        self.db.execute(
            "INSERT INTO audit_logs(id, ts, actor, action, detail, ip, alg, prev_hash, hash) "
            "VALUES(4, '2026-07-18T00:00:00+00:00', 'evil', 'login_success', '{}', "
            "'1.2.3.4', 'SHA-256', 'forged-prev', 'forged-hash')")
        with self.assertRaises(AuditTamperError):
            verify_chain(self.db)

    def test_event_dictionary_governance(self):
        """事件字典 ≥20 类(H04 §三.a);未登记事件写入被拒绝"""
        self.assertGreaterEqual(len(events.ALL_EVENTS), 20)
        with self.assertRaises(ValueError):
            self.writer.append("admin", "made_up_event", {}, SAMPLE_IP)


class TestMigrationsAndVolatile(unittest.TestCase):
    """迁移幂等与易失态存储语义。"""

    def test_j3_migrations_idempotent(self):
        """从 0 到最新可重跑,第二次执行零新增(H09 J.3 / H12 §六.2)"""
        db = make_temp_db()
        try:
            self.assertEqual(apply_migrations(db), [])   # base 已迁移过一轮
        finally:
            db.close()

    def test_j4_volatile_local_ttl_and_incr(self):
        """本地实现:TTL 过期返回 None;计数自增(开发/单机 profile 专用)"""
        store = LocalVolatileStore()
        key = make_key("idp", "fail", "alice")
        self.assertEqual(store.incr(key, ttl_seconds=60), 1)
        self.assertEqual(store.incr(key, ttl_seconds=60), 2)
        store.set(make_key("idp", "sms", "alice"), "hashed-code", ttl_seconds=0)
        self.assertIsNone(store.get(make_key("idp", "sms", "alice")))

    def test_j4_redis_fail_closed(self):
        """Redis 故障统一收敛为 StoreUnavailableError,禁止静默回退(H12 §五)"""

        class _BrokenRedis:
            """模拟不可用的 Redis 客户端"""

            def get(self, key):
                raise ConnectionError("redis down")

        store = RedisVolatileStore(_BrokenRedis())
        with self.assertRaises(StoreUnavailableError):
            store.get(make_key("idp", "sess", "sid-1"))


if __name__ == "__main__":
    unittest.main()
