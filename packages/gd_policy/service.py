# -*- coding: utf-8 -*-
"""
@file    service.py
@brief   统一设置服务(H03 §8 / H02-C3 参照实现):优先级 环境变量 > 管理后台 >
         配置文件 > 默认值;范围校验;null=删除覆盖;来源层可查;修改留审计;
         配置文件未知键启动报错(防拼写错误)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import datetime
import os

import yaml

from gd_common.errors import ConfigError, PolicyValidationError
from gd_policy.schema import (
    Param,
    SCHEMA_BY_KEY,
    SETTINGS_SCHEMA,
    TYPE_BOOL,
    TYPE_CHOICE,
    TYPE_FLOAT,
    TYPE_INT,
)
from gd_storage import events
from gd_storage.database import Database

SOURCE_ENV = "env"
SOURCE_OVERRIDE = "override"
SOURCE_FILE = "file"
SOURCE_DEFAULT = "default"
SETTINGS_VERSION_KEY = "__settings_version__"   # 设置版本号:变更传播依据(H12 §五)
_BOOL_TRUE_LITERALS = ("1", "true", "yes", "on")
_BOOL_FALSE_LITERALS = ("0", "false", "no", "off")


def _coerce(param: Param, raw, source: str):
    """
    @brief  按参数类型解析原始值并做范围校验(L2-19:显式默认值 + 范围校验模式)
    @param  raw    原始值(env/文件/覆盖层的字符串或已解析值)
    @param  source 来源描述(报错定位)
    @return 解析后的类型化值
    """
    if param.type == TYPE_BOOL:
        if isinstance(raw, bool):
            return raw
        literal = str(raw).strip().lower()
        if literal in _BOOL_TRUE_LITERALS:
            return True
        if literal in _BOOL_FALSE_LITERALS:
            return False
        raise ConfigError(f"{source} 中 {param.key} 非法布尔值: {raw!r}")
    if param.type in (TYPE_INT, TYPE_FLOAT):
        try:
            value = (float(str(raw).strip()) if param.type == TYPE_FLOAT
                     else int(str(raw).strip()))
        except ValueError as exc:
            raise ConfigError(f"{source} 中 {param.key} 非法数值: {raw!r}") from exc
        if param.ge is not None and value < param.ge:
            raise ConfigError(f"{source} 中 {param.key}={value} 低于下限 {param.ge}")
        if param.le is not None and value > param.le:
            raise ConfigError(f"{source} 中 {param.key}={value} 超过上限 {param.le}")
        return value
    if param.type == TYPE_CHOICE:
        value = str(raw).strip()
        if value not in param.choices:
            raise ConfigError(f"{source} 中 {param.key} 非法取值 {value!r},可选 {param.choices}")
        return value
    return str(raw)


class SettingsService:
    """统一设置服务:全平台各子系统同一实现(H01 ARC-2),schema 由 gd_policy.schema 提供。"""

    def __init__(self, db: Database, config_file: str = None, environ: dict = None):
        self._db = db
        self._environ = os.environ if environ is None else environ
        self._file_values = self._load_config_file(config_file)

    def _load_config_file(self, path: str) -> dict:
        """@brief 装载 YAML 配置文件;未知键 fail-fast 启动报错(02-C3)"""
        if not path:
            return {}
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        if not isinstance(data, dict):
            raise ConfigError(f"配置文件 {path} 顶层必须是键值映射")
        unknown = sorted(set(data) - set(SCHEMA_BY_KEY))
        if unknown:
            raise ConfigError(f"配置文件 {path} 含未知配置键(疑似拼写错误): {unknown}")
        return {key: _coerce(SCHEMA_BY_KEY[key], raw, f"配置文件 {path}")
                for key, raw in data.items()}

    def _override_raw(self, key: str):
        """@brief 读取管理后台覆盖层原始值;无覆盖返回 None"""
        rows = self._db.query("SELECT value FROM settings_overrides WHERE key = ?", (key,))
        return rows[0][0] if rows else None

    def get_with_source(self, key: str) -> tuple:
        """
        @brief  取参数生效值与来源层(env 锁定展示依据,H03 §8)
        @return (类型化值, 来源层字符串)
        """
        param = SCHEMA_BY_KEY.get(key)
        if param is None:
            raise ConfigError(f"未知配置键: {key}")
        env_raw = self._environ.get(param.env_name())
        if env_raw is not None:
            return _coerce(param, env_raw, "环境变量"), SOURCE_ENV
        override_raw = self._override_raw(key)
        if override_raw is not None:
            return _coerce(param, override_raw, "管理后台覆盖"), SOURCE_OVERRIDE
        if key in self._file_values:
            return self._file_values[key], SOURCE_FILE
        return param.default, SOURCE_DEFAULT

    def get(self, key: str):
        """@brief 取参数生效值"""
        return self.get_with_source(key)[0]

    def set_override(self, key: str, raw_value, actor: str, ip: str, audit_writer=None):
        """
        @brief  写入/删除管理后台覆盖层(null=删除覆盖恢复默认,02-C3);
                env 已锁定的参数拒绝修改;修改写审计(secret 类只记"已修改")
        @param  raw_value 新值;None 表示删除覆盖
        """
        param = SCHEMA_BY_KEY.get(key)
        if param is None:
            raise ConfigError(f"未知配置键: {key}")
        if self._environ.get(param.env_name()) is not None:
            raise PolicyValidationError({key: "该参数已由环境变量锁定,请修改部署配置"})
        old_value = self.get(key)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if raw_value is None:
            self._db.execute("DELETE FROM settings_overrides WHERE key = ?", (key,))
            new_value = SCHEMA_BY_KEY[key].default
        else:
            new_value = _coerce(param, raw_value, "管理后台提交")
            if self._override_raw(key) is None:
                self._db.execute(
                    "INSERT INTO settings_overrides(key, value, updated_at) VALUES(?, ?, ?)",
                    (key, str(raw_value), now))
            else:
                self._db.execute(
                    "UPDATE settings_overrides SET value = ?, updated_at = ? WHERE key = ?",
                    (str(raw_value), now, key))
        self._bump_version(now)
        if audit_writer is not None:
            from gd_storage.audit import SECRET_DETAIL_MASK
            detail = ({"key": key, "change": SECRET_DETAIL_MASK} if param.secret
                      else {"key": key, "old": old_value, "new": new_value})
            audit_writer.append(actor, events.SETTINGS_CHANGED, detail, ip)
        return new_value

    def _bump_version(self, now: str):
        """@brief 设置版本号自增:各实例经订阅/轮询 ≤5s 生效的传播依据(H12 §五)"""
        current = self._override_raw(SETTINGS_VERSION_KEY)
        next_version = str(int(current) + 1) if current else "1"
        if current is None:
            self._db.execute(
                "INSERT INTO settings_overrides(key, value, updated_at) VALUES(?, ?, ?)",
                (SETTINGS_VERSION_KEY, next_version, now))
        else:
            self._db.execute(
                "UPDATE settings_overrides SET value = ?, updated_at = ? WHERE key = ?",
                (next_version, now, SETTINGS_VERSION_KEY))

    def version(self) -> int:
        """@brief 当前设置版本号(无变更历史为 0)"""
        raw = self._override_raw(SETTINGS_VERSION_KEY)
        return int(raw) if raw else 0

    def describe_all(self) -> list:
        """@brief schema 驱动渲染数据:全参数的 值/来源/元数据(设置页统一交互,H11 §三)"""
        result = []
        for param in SETTINGS_SCHEMA:
            value, source = self.get_with_source(param.key)
            result.append({
                "key": param.key, "label": param.label, "section": param.section,
                "type": param.type, "value": value, "source": source,
                "unit": param.unit, "help": param.help, "choices": list(param.choices),
                "restart": param.restart, "is_env_locked": source == SOURCE_ENV,
            })
        return result
