# -*- coding: utf-8 -*-
"""
@file    payload.py
@brief   暗码载荷编解码(02-B2):bw 96bit = 48bit ID + CRC16 + RS(nsym=4);
         stega/tm 100bit = 48bit ID + CRC16 + BCH(t=4)(引擎随模型激活,
         编码器接口先行)。CRC 兜底判真,RS 纠错提升信道存活。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import secrets

import reedsolo

from gd_common.errors import PolicyValidationError

WM_ID_BITS = 48                  # tracer_id 位宽(L02 config WM_ID_BITS)
WM_CRC_BITS = 16
WM_DATA_BYTES = (WM_ID_BITS + WM_CRC_BITS) // 8      # 8 字节
RS_NSYM_BYTES = 4                # RS 校验 4 字节 → 总 96bit
BW_TOTAL_BITS = (WM_DATA_BYTES + RS_NSYM_BYTES) * 8  # 96
TRACER_MAX = (1 << WM_ID_BITS) - 1

_rs = reedsolo.RSCodec(RS_NSYM_BYTES)


def new_tracer_id() -> int:
    """@brief 生成 48bit 随机溯源 ID(对外业务码,H12 §二)"""
    return secrets.randbits(WM_ID_BITS)


def crc16_ccitt(data: bytes) -> int:
    """@brief CRC16-CCITT(0x1021,初值 0xFFFF)"""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def _id_with_crc(tracer_id: int) -> bytes:
    """@brief 48bit ID + CRC16 → 8 字节"""
    if not 0 <= tracer_id <= TRACER_MAX:
        raise PolicyValidationError("tracer_id 超出 48bit 空间")
    id_bytes = tracer_id.to_bytes(WM_ID_BITS // 8, "big")
    return id_bytes + crc16_ccitt(id_bytes).to_bytes(2, "big")


def bytes_to_bits(data: bytes) -> list:
    """@brief 字节串 → bit 列表(MSB 先)"""
    return [(byte >> (7 - offset)) & 1
            for byte in data for offset in range(8)]


def bits_to_bytes(bits: list) -> bytes:
    """@brief bit 列表 → 字节串(长度须为 8 的倍数)"""
    out = bytearray()
    for start in range(0, len(bits), 8):
        value = 0
        for bit in bits[start:start + 8]:
            value = (value << 1) | (1 if bit else 0)
        out.append(value)
    return bytes(out)


def encode_bw_payload(tracer_id: int) -> list:
    """@brief bw 载荷:ID+CRC → RS 编码 → 96bit"""
    encoded = bytes(_rs.encode(_id_with_crc(tracer_id)))
    return bytes_to_bits(encoded)


def decode_bw_payload(bits: list) -> int:
    """
    @brief  bw 提取译码:RS 纠错 → CRC 校验 → tracer_id
    @return tracer_id;纠错失败或 CRC 不符返回 None(未命中)
    """
    if len(bits) != BW_TOTAL_BITS:
        return None
    try:
        decoded = bytes(_rs.decode(bits_to_bytes(bits))[0])
    except reedsolo.ReedSolomonError:
        return None
    id_bytes, crc_bytes = decoded[:WM_ID_BITS // 8], decoded[WM_ID_BITS // 8:]
    if crc16_ccitt(id_bytes) != int.from_bytes(crc_bytes, "big"):
        return None
    return int.from_bytes(id_bytes, "big")
