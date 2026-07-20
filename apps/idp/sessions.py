# -*- coding: utf-8 -*-
"""
@file    sessions.py
@brief   IdP 会话服务(H08 sessions / ARC-4):会话一律入共享易失态(生产 Redis),
         空闲+绝对双超时(H03 §3),支持按用户吊销与按 demo 标记吊销(H05 §3.2.4)。
         设计说明:按用户/按标记吊销依赖 gd:idp:sess_index 会话索引键(JSON 列表,
         读改写;IdP 会话规模下竞态窗口可接受,写入失败 fail-closed)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import json
import secrets
import time

from gd_storage import make_key

AMR_DEMO_MARK = "demo"          # 演示登录方式标记(测试码/演示账号会话)
SESSION_INDEX_KEY = make_key("idp", "sess", "__index__")
SESSION_INDEX_TTL = 86400 * 8   # 索引键 TTL(略大于最长绝对会话期)


class SessionService:
    """IdP 人机会话(sid → 会话记录,全易失态)。"""

    def __init__(self, store, profile):
        """@brief 注入易失态存储与生效策略快照"""
        self._store = store
        self._profile = profile

    def _absolute_ttl(self) -> int:
        """@brief 绝对有效期秒数(session_max_hours)"""
        return self._profile.session_max_hours * 3600

    def create(self, account: str, amr: list, now: float = None) -> str:
        """@brief 建立会话,返回 sid(HttpOnly Cookie 值)"""
        sid = secrets.token_urlsafe(32)
        created = now if now is not None else time.time()
        record = {"account": account, "amr": amr, "created": created,
                  "last_seen": created}
        self._store.set(make_key("idp", "sess", sid), json.dumps(record),
                        ttl_seconds=self._absolute_ttl())
        index = self._load_index()
        index.append(sid)
        self._store.set(SESSION_INDEX_KEY, json.dumps(index),
                        ttl_seconds=SESSION_INDEX_TTL)
        return sid

    def get(self, sid: str, now: float = None) -> dict:
        """@brief 取会话并执行空闲/绝对双超时(过期返回 None 并清除)"""
        if not sid:
            return None
        raw = self._store.get(make_key("idp", "sess", sid))
        if raw is None:
            return None
        record = json.loads(raw)
        moment = now if now is not None else time.time()
        idle_limit = self._profile.session_idle_minutes * 60
        if (moment - record["last_seen"] > idle_limit
                or moment - record["created"] > self._absolute_ttl()):
            self.revoke(sid)
            return None
        record["last_seen"] = moment      # 滚动空闲计时
        self._store.set(make_key("idp", "sess", sid), json.dumps(record),
                        ttl_seconds=self._absolute_ttl())
        return record

    def revoke(self, sid: str):
        """@brief 吊销单个会话"""
        self._store.delete(make_key("idp", "sess", sid))

    def _load_index(self) -> list:
        """@brief 装载会话索引(缺失即空)"""
        raw = self._store.get(SESSION_INDEX_KEY)
        return json.loads(raw) if raw else []

    def _revoke_matching(self, predicate) -> int:
        """@brief 按谓词批量吊销,并压缩索引 @return 吊销数"""
        index, kept, revoked = self._load_index(), [], 0
        for sid in index:
            raw = self._store.get(make_key("idp", "sess", sid))
            if raw is None:
                continue
            if predicate(json.loads(raw)):
                self.revoke(sid)
                revoked += 1
            else:
                kept.append(sid)
        self._store.set(SESSION_INDEX_KEY, json.dumps(kept),
                        ttl_seconds=SESSION_INDEX_TTL)
        return revoked

    def revoke_user(self, account: str) -> int:
        """@brief 吊销某用户全部会话(停用/重置即刻断线,H04 §二.b)"""
        return self._revoke_matching(lambda rec: rec["account"] == account)

    def revoke_demo_sessions(self) -> int:
        """@brief 吊销 amr 含 demo 标记的全部会话(H05 §3.2.4)"""
        return self._revoke_matching(lambda rec: AMR_DEMO_MARK in rec.get("amr", []))
