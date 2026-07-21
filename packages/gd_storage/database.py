# -*- coding: utf-8 -*-
"""
@file    database.py
@brief   统一 Database 抽象(H12 §一):生产默认 PostgreSQL,开发/单机 DEMO 用
         SQLite(WAL);业务代码零方言感知,方言差异(占位符、串行锁)在本层适配。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import contextlib
import sqlite3
import threading

from gd_common.errors import ConfigError

try:                                     # PG 驱动为可选依赖(单机形态无需)
    import psycopg as _psycopg
    DB_ERRORS = (sqlite3.DatabaseError, _psycopg.Error)
except ImportError:                      # noqa: SIM105
    DB_ERRORS = (sqlite3.DatabaseError,)

DIALECT_SQLITE = "sqlite"
DIALECT_POSTGRES = "postgres"
SQLITE_URL_PREFIX = "sqlite:///"
POSTGRES_URL_PREFIXES = ("postgresql://", "postgres://")
SQLITE_BUSY_TIMEOUT_MS = 5000        # H12 §一.3 强制 PRAGMA 下限
AUDIT_ADVISORY_LOCK_ID = 745001      # PG 审计链全局串行锁号(固定常量,H12 §四)


class Database:
    """双方言数据库封装:统一 qmark 占位符,提供事务与审计串行锁原语。"""

    def __init__(self, url: str):
        self.url = url
        self._lock = threading.RLock()   # 连接级互斥(Web 线程池防御)
        if url.startswith(SQLITE_URL_PREFIX):
            self.dialect = DIALECT_SQLITE
            path = url[len(SQLITE_URL_PREFIX):]
            self._conn = sqlite3.connect(path, check_same_thread=False)
            self._apply_sqlite_pragmas()
        elif url.startswith(POSTGRES_URL_PREFIXES):
            self.dialect = DIALECT_POSTGRES
            import psycopg  # 延迟导入:开发单机形态无需 PG 驱动
            self._conn = psycopg.connect(url, autocommit=True)
        else:
            raise ConfigError(f"不支持的数据库 URL: {url}")

    def _apply_sqlite_pragmas(self):
        """@brief SQLite 强制 PRAGMA:WAL、外键、busy_timeout(H12 §一.3)"""
        cur = self._conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        cur.close()

    def _adapt_sql(self, sql: str) -> str:
        """
        @brief  占位符适配:统一书写 qmark(?),PG 侧转换为 %s;
                psycopg3 对裸 % 严格报错,先转义 % → %%(生产实测,2026-07-19)
        """
        if self.dialect == DIALECT_POSTGRES:
            sql = sql.replace("%", "%%")
            sql = sql.replace("?", "%s")
            # SQLite 幂等插入方言 → PG 等价语义(J.1 双库同测发现,2026-07-21)
            if sql.lstrip().upper().startswith("INSERT OR IGNORE "):
                head = sql.lstrip()
                sql = "INSERT " + head[len("INSERT OR IGNORE "):]
                sql += " ON CONFLICT DO NOTHING"
        return sql

    def execute(self, sql: str, params: tuple = ()) -> None:
        """@brief 执行写语句(连接级互斥,线程池安全)"""
        with self._lock:
            return self._execute_locked(sql, params)

    def _execute_locked(self, sql: str, params: tuple = ()) -> None:
        """@brief 执行写语句(自动提交语境)"""
        cur = self._conn.cursor()
        try:
            cur.execute(self._adapt_sql(sql), params)
            if self.dialect == DIALECT_SQLITE:
                self._conn.commit()
        finally:
            cur.close()

    def query(self, sql: str, params: tuple = ()) -> list:
        """@brief 查询并返回行列表(连接级互斥,线程池安全)"""
        with self._lock:
            return self._query_locked(sql, params)

    def _query_locked(self, sql: str, params: tuple = ()) -> list:
        """@brief 查询并返回行列表(tuple)"""
        cur = self._conn.cursor()
        try:
            cur.execute(self._adapt_sql(sql), params)
            return cur.fetchall()
        finally:
            cur.close()

    @contextlib.contextmanager
    def serial_txn(self):
        """
        @brief  审计链全局串行写入事务(H01 ARC-7 / H12 §四):
                SQLite 用 BEGIN IMMEDIATE 单写者天然串行;
                PG 在事务内取 pg_advisory_xact_lock(固定锁号)防并发分叉。
        @return 事务内游标(with 语境自动提交/回滚,H07 L1-11)
        """
        with self._lock:
            cur = self._conn.cursor()
            try:
                if self.dialect == DIALECT_SQLITE:
                    cur.execute("BEGIN IMMEDIATE")
                else:
                    self._conn.autocommit = False
                    cur.execute("SELECT pg_advisory_xact_lock(?)".replace("?", "%s"),
                                (AUDIT_ADVISORY_LOCK_ID,))
                yield _TxnCursor(cur, self)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                if self.dialect == DIALECT_POSTGRES:
                    self._conn.autocommit = True
                cur.close()

    def close(self):
        """@brief 关闭连接;关闭后的句柄不得复用(H07 L3 条款)"""
        self._conn.close()


class _TxnCursor:
    """事务内游标包装:提供与 Database 一致的占位符适配。"""

    def __init__(self, cursor, db: Database):
        self._cursor = cursor
        self._db = db

    def execute(self, sql: str, params: tuple = ()):
        """@brief 事务内执行"""
        self._cursor.execute(self._db._adapt_sql(sql), params)

    def fetchone(self):
        """@brief 取单行"""
        return self._cursor.fetchone()

    def fetchall(self):
        """@brief 取全部行"""
        return self._cursor.fetchall()
