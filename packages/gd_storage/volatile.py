# -*- coding: utf-8 -*-
"""
@file    volatile.py
@brief   共享易失态存储抽象(H01 ARC-4 / H06-E13):会话、锁定/限速计数、验证码等
         生产一律 Redis;开发/单机 profile 可显式配置本地实现(接口签名唯一)。
         Redis 不可用时 fail-closed,禁止静默回退进程内存。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import time
from typing import Optional, Protocol

from gd_common.errors import StoreUnavailableError

KEY_PREFIX = "gd"     # 键空间统一前缀:gd:{sys}:{domain}:{id}(H12 §五)


def make_key(system: str, domain: str, ident: str) -> str:
    """@brief 组装规约键名 gd:{sys}:{domain}:{id}"""
    return f"{KEY_PREFIX}:{system}:{domain}:{ident}"


class IVolatileStore(Protocol):
    """共享易失态存储接口:全部操作显式 TTL,实现方 MUST 保证跨实例可见语义。"""

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        """@brief 写入并设置 TTL"""
        ...

    def get(self, key: str) -> Optional[str]:
        """@brief 读取;不存在或已过期返回 None"""
        ...

    def delete(self, key: str) -> None:
        """@brief 删除键(验证码用后作废等场景)"""
        ...

    def incr(self, key: str, ttl_seconds: int) -> int:
        """@brief 原子自增(失败计数/限速),首写设置 TTL @return 自增后的值"""
        ...


class LocalVolatileStore:
    """
    本地实现:仅限开发/单机 DEMO profile 显式配置使用(H12 §五)。

    注意:进程内存实现不满足多实例共享语义,生产 profile 装配层 MUST 拒绝本实现
    (装配校验见 profile 装配代码;这是 H06-E2/E13 根因,禁止在生产复活)。
    """

    def __init__(self):
        self._data: dict = {}

    def _purge_if_expired(self, key: str):
        """@brief 惰性清理过期键"""
        entry = self._data.get(key)
        if entry and entry[1] < time.monotonic():
            del self._data[key]

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        """@brief 写入并设置 TTL"""
        self._data[key] = (value, time.monotonic() + ttl_seconds)

    def get(self, key: str) -> Optional[str]:
        """@brief 读取;过期返回 None"""
        self._purge_if_expired(key)
        entry = self._data.get(key)
        return entry[0] if entry else None

    def delete(self, key: str) -> None:
        """@brief 删除键"""
        self._data.pop(key, None)

    def incr(self, key: str, ttl_seconds: int) -> int:
        """@brief 自增计数;过期后从 1 重新开始"""
        self._purge_if_expired(key)
        entry = self._data.get(key)
        count = int(entry[0]) + 1 if entry else 1
        expire_at = entry[1] if entry else time.monotonic() + ttl_seconds
        self._data[key] = (str(count), expire_at)
        return count


class RedisVolatileStore:
    """
    Redis 实现(生产默认):任何连接/命令异常统一收敛为 StoreUnavailableError,
    由调用方明示"登录暂不可用"——fail-closed 语义 MUST NOT 弱化(H12 ai_directives)。
    """

    def __init__(self, redis_client):
        self._redis = redis_client

    def _guard(self, func, *args):
        """@brief 统一异常收敛:Redis 故障 → StoreUnavailableError(fail-closed)"""
        try:
            return func(*args)
        except Exception as exc:
            raise StoreUnavailableError(
                "共享状态存储(Redis)不可用,依赖它的功能已暂停(fail-closed)。"
                "请检查 Redis 服务与网络;禁止改用进程内存绕过(H06-E13)。"
            ) from exc

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        """@brief SET + EX"""
        self._guard(lambda: self._redis.set(key, value, ex=ttl_seconds))

    def get(self, key: str) -> Optional[str]:
        """@brief GET,bytes 解码为 str"""
        raw = self._guard(lambda: self._redis.get(key))
        return raw.decode("utf-8") if isinstance(raw, bytes) else raw

    def delete(self, key: str) -> None:
        """@brief DEL"""
        self._guard(lambda: self._redis.delete(key))

    def incr(self, key: str, ttl_seconds: int) -> int:
        """@brief INCR,首写补 EXPIRE(NX 语义防覆盖既有 TTL)"""
        def _do():
            value = self._redis.incr(key)
            if value == 1:
                self._redis.expire(key, ttl_seconds)
            return value
        return self._guard(_do)
