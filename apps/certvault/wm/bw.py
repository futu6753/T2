# -*- coding: utf-8 -*-
"""
@file    bw.py
@brief   bw 频域盲水印引擎(02-B2):Haar DWT LL 子带 → 8×8 分块 DCT →
         SVD 首奇异值 QIM 调制,96bit 循环重复嵌入、提取多数投票。
         电子链路可靠(JPEG q60 实测零误码)、毫秒级、零模型依赖;
         诚实边界:不承诺打印翻拍存活(L02 §5)。
@author  港电实验室平台组
@date    2026-07-18
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import numpy as np
from scipy.fft import dctn, idctn

from apps.certvault.wm.payload import (
    BW_TOTAL_BITS, decode_bw_payload, encode_bw_payload,
)

ENGINE_ID = "bw"
ENGINE_NAME = "频域盲水印(DWT-DCT-SVD)"
RECOMMENDED_STRENGTH = 56.0      # QIM 步长(L02:bw 推荐 56)
BLOCK = 8
MAX_EMBED_SIDE = 1600            # 嵌入工作图上限(过大图先缩,记录 embed_w/h)


def _haar_dwt(channel: np.ndarray) -> tuple:
    """@brief 一级 Haar DWT @return (LL, LH, HL, HH)"""
    even_rows, odd_rows = channel[0::2], channel[1::2]
    height = min(even_rows.shape[0], odd_rows.shape[0])
    width = (channel.shape[1] // 2) * 2
    a = even_rows[:height, 0:width:2]
    b = even_rows[:height, 1:width:2]
    c = odd_rows[:height, 0:width:2]
    d = odd_rows[:height, 1:width:2]
    return ((a + b + c + d) / 2.0, (a - b + c - d) / 2.0,
            (a + b - c - d) / 2.0, (a - b - c + d) / 2.0)


def _haar_idwt(ll, lh, hl, hh) -> np.ndarray:
    """@brief 一级 Haar 逆变换"""
    a = (ll + lh + hl + hh) / 2.0
    b = (ll - lh + hl - hh) / 2.0
    c = (ll + lh - hl - hh) / 2.0
    d = (ll - lh - hl + hh) / 2.0
    height, width = ll.shape
    out = np.zeros((height * 2, width * 2), dtype=np.float64)
    out[0::2, 0::2], out[0::2, 1::2] = a, b
    out[1::2, 0::2], out[1::2, 1::2] = c, d
    return out


def embed_bits(y_channel: np.ndarray, bits: list, strength: float) -> np.ndarray:
    """
    @brief  在亮度通道嵌入 bit 序列(循环重复覆盖全部 8×8 块)
    @param  y_channel float64 亮度图(嵌入尺寸由调用方裁定并记录)
    @return 含水印亮度图(同尺寸,clip 0–255)
    """
    ll, lh, hl, hh = _haar_dwt(y_channel.astype(np.float64))
    blocks_y, blocks_x = ll.shape[0] // BLOCK, ll.shape[1] // BLOCK
    total_bits = len(bits)
    index = 0
    for by in range(blocks_y):
        for bx in range(blocks_x):
            window = ll[by * BLOCK:(by + 1) * BLOCK, bx * BLOCK:(bx + 1) * BLOCK]
            coeffs = dctn(window, norm="ortho")
            u_mat, sigma, vt_mat = np.linalg.svd(coeffs)
            quantum = np.floor(sigma[0] / strength)
            fraction = 0.25 if bits[index % total_bits] == 0 else 0.75
            sigma[0] = (quantum + fraction) * strength
            ll[by * BLOCK:(by + 1) * BLOCK, bx * BLOCK:(bx + 1) * BLOCK] = \
                idctn(u_mat @ np.diag(sigma) @ vt_mat, norm="ortho")
            index += 1
    restored = _haar_idwt(ll, lh, hl, hh)
    out = y_channel.astype(np.float64).copy()
    out[:restored.shape[0], :restored.shape[1]] = restored
    return np.clip(out, 0, 255)


def extract_bits(y_channel: np.ndarray, total_bits: int,
                 strength: float) -> list:
    """@brief 盲提取:全部块投票还原 bit 序列"""
    ll, _, _, _ = _haar_dwt(y_channel.astype(np.float64))
    blocks_y, blocks_x = ll.shape[0] // BLOCK, ll.shape[1] // BLOCK
    ones = np.zeros(total_bits)
    counts = np.zeros(total_bits)
    index = 0
    for by in range(blocks_y):
        for bx in range(blocks_x):
            window = ll[by * BLOCK:(by + 1) * BLOCK, bx * BLOCK:(bx + 1) * BLOCK]
            sigma = np.linalg.svd(dctn(window, norm="ortho"), compute_uv=False)
            if (sigma[0] / strength) % 1.0 >= 0.5:
                ones[index % total_bits] += 1
            counts[index % total_bits] += 1
            index += 1
    return [1 if ones[i] / max(counts[i], 1) >= 0.5 else 0
            for i in range(total_bits)]


def embed_tracer(y_channel: np.ndarray, tracer_id: int,
                 strength: float = RECOMMENDED_STRENGTH) -> np.ndarray:
    """@brief 嵌入 96bit 载荷(ID+CRC16+RS)"""
    return embed_bits(y_channel, encode_bw_payload(tracer_id), strength)


def extract_tracer(y_channel: np.ndarray,
                   strength: float = RECOMMENDED_STRENGTH) -> int:
    """@brief 提取并译码 @return tracer_id 或 None(CRC/RS 判非命中)"""
    bits = extract_bits(y_channel, BW_TOTAL_BITS, strength)
    return decode_bw_payload(bits)
