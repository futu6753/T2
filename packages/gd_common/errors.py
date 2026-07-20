# -*- coding: utf-8 -*-
"""
@file    errors.py
@brief   全平台统一异常类型定义(共享库,禁止各子系统自定义同义异常)
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""


class PlatformError(Exception):
    """平台异常基类:所有自定义异常必须继承本类,便于全局处理器统一转换。"""


class ConfigError(PlatformError):
    """配置错误:未知配置键、格式非法、范围越界等。启动期抛出即 fail-fast。"""


class CryptoError(PlatformError):
    """密码学错误:加解密失败、签名验证失败、算法不可用等。"""


class MasterKeyMismatchError(CryptoError):
    """
    主密钥与存量密文不匹配(H06-E10 红线)。

    携带明确的处置指引而非静默解密失败;启动检测到本异常时必须非零退出。
    """

    GUIDANCE = (
        "主密钥与存量密文(wrapped_dek)不匹配。"
        "更换根密钥不是改配置——必须执行官方迁移脚本 scripts/rotate_master_key.py "
        "(用旧钥解密 wrapped_dek 再用新钥重包)。"
        "请核对 MASTER_KEY_HEX / MASTER_KEY_ID 是否被误改,或参照密钥轮换 runbook 操作。"
    )

    def __init__(self, kid: str):
        super().__init__(f"未找到 kid={kid} 对应的主密钥。{self.GUIDANCE}")
        self.kid = kid


class InvalidContextError(CryptoError):
    """登录上下文令牌非法(签名不符/格式损坏),按无效处理。"""


class ExpiredContextError(CryptoError):
    """
    登录上下文令牌已过期(H06-E2 红线)。

    与 InvalidContextError 严格区分:过期上下文必须走"自动续签回登录页"
    而非报错死路,故本异常携带原载荷供调用方续签。
    """

    def __init__(self, payload: dict):
        super().__init__("登录上下文已过期,应自动续签而非拒绝")
        self.payload = payload


class PolicyValidationError(PlatformError):
    """策略参数校验失败:范围越界、防自锁校验不通过等,携带逐项原因。"""

    def __init__(self, reasons: dict):
        super().__init__(f"策略校验失败: {reasons}")
        self.reasons = reasons


class StoreUnavailableError(PlatformError):
    """
    共享易失态存储(Redis)不可用。

    语义为 fail-closed(H12 §五):登录等依赖路径必须明示原因暂停服务,
    禁止静默回退进程内存(H06-E2 根因不许复活)。
    """


class AuditTamperError(PlatformError):
    """审计链校验失败:检测到篡改或断链。"""


class SelfCheckError(PlatformError):
    """等保态自检失败(H05 §4):进程必须以非零码停止服务(fail-closed)。"""

    def __init__(self, failures: list):
        super().__init__(f"等保态自检失败 {len(failures)} 项: {failures}")
        self.failures = failures
