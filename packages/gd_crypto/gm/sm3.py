# -*- coding: utf-8 -*-
"""
@file    sm3.py
@brief   SM3 摘要算法纯 Python 参考实现(GB/T 32905-2016),附 HMAC-SM3 与
         PBKDF2-HMAC-SM3(H04 §8.1 gm 套件原语)。零第三方依赖(H01 ARC-5
         供应链最小面),正确性由 tests/test_f_crypto_suite.py 标准向量锚定。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import struct

_MASK32 = 0xFFFFFFFF
# GB/T 32905 §4.1 初始值 IV
_IV = (0x7380166F, 0x4914B2B9, 0x172442D7, 0xDA8A0600,
       0xA96F30BC, 0x163138AA, 0xE38DEE4D, 0xB0FB0E4E)
_T_LOW = 0x79CC4519      # 常量 Tj(0 ≤ j ≤ 15)
_T_HIGH = 0x7A879D8A     # 常量 Tj(16 ≤ j ≤ 63)
SM3_BLOCK_SIZE = 64      # 分组长度(字节),HMAC 分组同此
SM3_DIGEST_SIZE = 32     # 摘要长度(字节)


def _rotl(value: int, bits: int) -> int:
    """@brief 32 位循环左移"""
    bits %= 32
    return ((value << bits) | (value >> (32 - bits))) & _MASK32


def _p0(value: int) -> int:
    """@brief 置换函数 P0(压缩用)"""
    return value ^ _rotl(value, 9) ^ _rotl(value, 17)


def _p1(value: int) -> int:
    """@brief 置换函数 P1(消息扩展用)"""
    return value ^ _rotl(value, 15) ^ _rotl(value, 23)


def _expand(block: bytes) -> tuple:
    """@brief 消息扩展:512bit 分组 → W[0..67] 与 W'[0..63]"""
    w = list(struct.unpack(">16I", block))
    for j in range(16, 68):
        w.append(_p1(w[j - 16] ^ w[j - 9] ^ _rotl(w[j - 3], 15))
                 ^ _rotl(w[j - 13], 7) ^ w[j - 6])
    w_prime = [w[j] ^ w[j + 4] for j in range(64)]
    return w, w_prime


def _compress(state: tuple, block: bytes) -> tuple:
    """@brief 压缩函数 CF:一个 512bit 分组更新链接变量(GB/T 32905 §4.3)"""
    w, w_prime = _expand(block)
    a, b, c, d, e, f, g, h = state
    for j in range(64):
        t_j = _T_LOW if j < 16 else _T_HIGH
        ss1 = _rotl((_rotl(a, 12) + e + _rotl(t_j, j)) & _MASK32, 7)
        ss2 = ss1 ^ _rotl(a, 12)
        if j < 16:
            ff = a ^ b ^ c
            gg = e ^ f ^ g
        else:
            ff = (a & b) | (a & c) | (b & c)
            gg = (e & f) | ((~e & _MASK32) & g)
        tt1 = (ff + d + ss2 + w_prime[j]) & _MASK32
        tt2 = (gg + h + ss1 + w[j]) & _MASK32
        d, c, b, a = c, _rotl(b, 9), a, tt1
        h, g, f, e = g, _rotl(f, 19), e, _p0(tt2)
    return tuple(x ^ y for x, y in zip(state, (a, b, c, d, e, f, g, h)))


def sm3_digest(message: bytes) -> bytes:
    """
    @brief  计算 SM3 摘要(一次性接口;流式需求出现前不引入,H07 最小实现)
    @param  message 输入字节串
    @return 32 字节摘要
    """
    length_bits = len(message) * 8
    # 填充:0x80 + 0x00* + 64bit 大端消息长度(与 SHA-256 同构)
    padded = message + b"\x80"
    padded += b"\x00" * ((SM3_BLOCK_SIZE - 8 - len(padded) % SM3_BLOCK_SIZE)
                         % SM3_BLOCK_SIZE)
    padded += struct.pack(">Q", length_bits)
    state = _IV
    for offset in range(0, len(padded), SM3_BLOCK_SIZE):
        state = _compress(state, padded[offset:offset + SM3_BLOCK_SIZE])
    return struct.pack(">8I", *state)


def hmac_sm3(key: bytes, message: bytes) -> bytes:
    """@brief HMAC-SM3(RFC 2104 结构,B=64) @return 32 字节 MAC"""
    if len(key) > SM3_BLOCK_SIZE:
        key = sm3_digest(key)
    key = key.ljust(SM3_BLOCK_SIZE, b"\x00")
    inner = bytes(k ^ 0x36 for k in key)
    outer = bytes(k ^ 0x5C for k in key)
    return sm3_digest(outer + sm3_digest(inner + message))


def pbkdf2_hmac_sm3(password: bytes, salt: bytes, iterations: int,
                    dklen: int = SM3_DIGEST_SIZE) -> bytes:
    """
    @brief  PBKDF2-HMAC-SM3 口令派生(RFC 8018 结构,PRF=HMAC-SM3,H04 §8.1)
    @param  password 口令字节串
    @param  salt     盐(MUST 随机,由调用方生成)
    @param  iterations 迭代次数(下限由套件层守卫)
    @param  dklen    派生密钥长度(字节)
    @return 派生密钥
    """
    if len(password) > SM3_BLOCK_SIZE:
        password = sm3_digest(password)
    key = password.ljust(SM3_BLOCK_SIZE, b"\x00")
    # 预压缩 ipad/opad 首块链接状态:内循环每次 HMAC 仅剩 2 次压缩(RFC 8018 惯用优化)
    inner_state = _compress(_IV, bytes(k ^ 0x36 for k in key))
    outer_state = _compress(_IV, bytes(k ^ 0x5C for k in key))
    # 固定尾块布局:64B(已预压缩)+ 32B 数据 + 0x80 填充 + 96 字节总长(768 bit)
    tail_suffix = b"\x80" + b"\x00" * 23 + struct.pack(">Q", (SM3_BLOCK_SIZE + SM3_DIGEST_SIZE) * 8)

    def _hmac_fast(data32: bytes) -> bytes:
        inner = _compress(inner_state, data32 + tail_suffix)
        return struct.pack(">8I", *_compress(outer_state, struct.pack(">8I", *inner) + tail_suffix))

    derived = b""
    block_index = 1
    while len(derived) < dklen:
        u = hmac_sm3(password, salt + struct.pack(">I", block_index))
        t = int.from_bytes(u, "big")
        for _ in range(iterations - 1):
            u = _hmac_fast(u)
            t ^= int.from_bytes(u, "big")
        derived += t.to_bytes(SM3_DIGEST_SIZE, "big")
        block_index += 1
    return derived[:dklen]
