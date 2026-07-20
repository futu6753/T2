# -*- coding: utf-8 -*-
"""
@file    keyring.py
@brief   主密钥环:kid → 主密钥映射,支撑信封解包按 kid 选钥与"轮换=迁移"
         (H04 §五 / H06-E10 / H06-P0-1)。生产环境主密钥经 env 注入,未来迁 KMS。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import os

from gd_common.errors import ConfigError, MasterKeyMismatchError

MASTER_KEY_HEX_LEN = 64          # 32 字节主密钥的 hex 长度(H06-D5:非 64 位 hex 是常见 502 根因)
ENV_MASTER_KEY_HEX = "MASTER_KEY_HEX"
ENV_MASTER_KEY_ID = "MASTER_KEY_ID"
ENV_OLD_MASTER_KEY_HEX = "OLD_MASTER_KEY_HEX"    # 轮换迁移期旧钥(仅迁移脚本使用)
ENV_OLD_MASTER_KEY_ID = "OLD_MASTER_KEY_ID"
DEFAULT_MASTER_KEY_ID = "mk1"
DEFAULT_OLD_MASTER_KEY_ID = "mk0"
# 演示派生默认主密钥(仅 DEMO 单机联调;进入生产前置校验会与本常量比对并阻止,H05 §3.2.5)
DEMO_MASTER_KEY_HEX = "00" * 32


def _parse_key_hex(raw: str, source: str) -> bytes:
    """
    @brief  解析并校验 hex 主密钥格式
    @param  raw    hex 字符串
    @param  source 来源描述(报错定位用)
    @return 32 字节密钥
    """
    value = raw.strip().strip('"').strip("'")   # 值带引号/空格是常见部署事故(H06-D5)
    if len(value) != MASTER_KEY_HEX_LEN:
        raise ConfigError(f"{source} 必须为 {MASTER_KEY_HEX_LEN} 位 hex(当前 {len(value)} 位)")
    try:
        return bytes.fromhex(value)
    except ValueError as exc:
        raise ConfigError(f"{source} 含非法 hex 字符") from exc


class MasterKeyRing:
    """主密钥环:持有一个当前钥与可选旧钥,按 kid 取钥,未知 kid 抛 E10 指引。"""

    def __init__(self, keys: dict, current_kid: str):
        if current_kid not in keys:
            raise ConfigError(f"当前主密钥 kid={current_kid} 不在密钥环中")
        self._keys = dict(keys)
        self.current_kid = current_kid

    @classmethod
    def from_env(cls, environ: dict = None) -> "MasterKeyRing":
        """
        @brief  从环境变量装载密钥环(生产标准途径;测试可注入 environ)
        @return MasterKeyRing 实例
        """
        env = os.environ if environ is None else environ
        raw = env.get(ENV_MASTER_KEY_HEX)
        if not raw:
            raise ConfigError(
                f"缺少 {ENV_MASTER_KEY_HEX}。请运行 scripts/gen_master_key.py 生成并注入环境变量;"
                "任何真实密钥不得写入代码库或交付包(H00 G7)。"
            )
        current_kid = env.get(ENV_MASTER_KEY_ID, DEFAULT_MASTER_KEY_ID)
        keys = {current_kid: _parse_key_hex(raw, ENV_MASTER_KEY_HEX)}
        old_raw = env.get(ENV_OLD_MASTER_KEY_HEX)
        if old_raw:
            old_kid = env.get(ENV_OLD_MASTER_KEY_ID, DEFAULT_OLD_MASTER_KEY_ID)
            keys[old_kid] = _parse_key_hex(old_raw, ENV_OLD_MASTER_KEY_HEX)
        return cls(keys, current_kid)

    def get(self, kid: str) -> bytes:
        """
        @brief  按 kid 取主密钥;未知 kid 抛 MasterKeyMismatchError 并携带处置指引
                (H06-E10:换根密钥≠改配置,必须走信封重包迁移)
        @param  kid 密钥标识
        @return 主密钥字节串
        """
        if kid not in self._keys:
            raise MasterKeyMismatchError(kid)
        return self._keys[kid]

    def current(self) -> tuple:
        """@brief 取当前钥 @return (kid, key)"""
        return self.current_kid, self._keys[self.current_kid]

    @property
    def current_key(self) -> bytes:
        """@brief 当前主密钥字节串(便捷只读属性)"""
        return self._keys[self.current_kid]

    def is_demo_key(self) -> bool:
        """@brief 判断当前主密钥是否演示派生默认值(生产前置校验用,H05 §3.2.5)"""
        return self._keys[self.current_kid] == bytes.fromhex(DEMO_MASTER_KEY_HEX)
