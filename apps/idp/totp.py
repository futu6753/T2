# -*- coding: utf-8 -*-
"""
@file    totp.py
@brief   TOTP 动态口令(RFC 6238 / HMAC-SHA1,H04 §8.1 双套件同此实现):
         30 秒步长、6 位码、±1 步时间窗容忍(部署 NTP 见 H04 §九,06-E9)
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time

TOTP_STEP_SECONDS = 30
TOTP_DIGITS = 6
TOTP_WINDOW_STEPS = 1        # 允许前后各 1 个时间步(时钟偏移容忍)
TOTP_SECRET_BYTES = 20


def generate_totp_secret() -> str:
    """@brief 生成 base32 TOTP 密钥(绑定二维码用,密钥不出内网)"""
    return base64.b32encode(secrets.token_bytes(TOTP_SECRET_BYTES)).decode("ascii")


def totp_code(secret_base32: str, timestamp: float = None,
              step_offset: int = 0) -> str:
    """
    @brief  计算指定时间步的 TOTP 码
    @param  secret_base32 base32 密钥
    @param  timestamp     计算时刻(缺省当前)
    @param  step_offset   时间步偏移(验证窗口用)
    @return 6 位数字码
    """
    counter = int((timestamp if timestamp is not None else time.time())
                  // TOTP_STEP_SECONDS) + step_offset
    key = base64.b32decode(secret_base32, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code_int % (10 ** TOTP_DIGITS)).zfill(TOTP_DIGITS)


def verify_totp(secret_base32: str, code: str, timestamp: float = None) -> bool:
    """@brief 在 ±TOTP_WINDOW_STEPS 窗口内校验动态码(恒定时间比较)"""
    for step_offset in range(-TOTP_WINDOW_STEPS, TOTP_WINDOW_STEPS + 1):
        expected = totp_code(secret_base32, timestamp, step_offset)
        if hmac.compare_digest(expected, str(code)):
            return True
    return False
