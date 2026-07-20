# -*- coding: utf-8 -*-
"""
@file    recapture_matrix.py
@brief   13-R-CV-4 翻拍信道评测矩阵(离线工件,半自动):以仿真信道
         (透视角度 0/15/30° × 光照增益/伽马三档 × JPEG 二压)先行摸底
         bw 引擎翻拍存活边界,输出矩阵报告;真实翻拍实测按同一矩阵用
         手机拍摄屏幕/打印件后以 --input-dir 喂入本脚本复核
         (每格 ≥3 样本,记录设备型号)。诚实边界:bw 不承诺翻拍存活
         (L02 §5),本矩阵用于验证边界并为 stega 激活后的对比留基线。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
用法:PYTHONPATH=packages:. python3 benchmarks/recapture_matrix.py
      PYTHONPATH=packages:. python3 benchmarks/recapture_matrix.py \
          --input-dir /path/真实翻拍样本  # 文件名含 tracer 十六进制
"""
import argparse
import os
import sys

import numpy as np
import cv2

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "packages"))
sys.path.insert(0, REPO_ROOT)

from apps.certvault.wm import bw                             # noqa: E402
from apps.certvault.wm.engines import EngineRegistry         # noqa: E402
from apps.certvault.wm.payload import new_tracer_id          # noqa: E402
from apps.certvault.wm.pipeline import process_certificate   # noqa: E402

ANGLES_DEG = (0, 15, 30)
LIGHTING = (("标准", 1.00, 1.00), ("暗场", 0.72, 1.25), ("过曝", 1.30, 0.82))
SAMPLES_PER_CELL = 3


def _sample_document(seed: int) -> np.ndarray:
    """@brief 合成证件样图"""
    rng = np.random.default_rng(seed)
    gray = cv2.GaussianBlur(
        (rng.random((600, 900)) * 150 + 70).astype(np.uint8), (41, 41), 11)
    image = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    cv2.putText(image, "GANGDIAN RECAPTURE SAMPLE", (60, 300),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (35, 35, 35), 3)
    return image


def _perspective(image: np.ndarray, angle_deg: float) -> np.ndarray:
    """@brief 水平翻拍角仿真:单侧收缩的透视映射"""
    height, width = image.shape[:2]
    shrink = np.tan(np.radians(angle_deg)) * 0.5
    offset = int(height * shrink * 0.5)
    src = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
    dst = np.float32([[0, offset], [width, 0],
                      [width, height], [0, height - offset]])
    matrix = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(image, matrix, (width, height),
                               borderMode=cv2.BORDER_REPLICATE)


def _rectify(image: np.ndarray, angle_deg: float) -> np.ndarray:
    """@brief 溯源侧四边形矫正等价步骤(评测中用已知角度逆变换)"""
    height, width = image.shape[:2]
    shrink = np.tan(np.radians(angle_deg)) * 0.5
    offset = int(height * shrink * 0.5)
    src = np.float32([[0, offset], [width, 0],
                      [width, height], [0, height - offset]])
    dst = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
    matrix = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(image, matrix, (width, height),
                               borderMode=cv2.BORDER_REPLICATE)


def _apply_lighting(image: np.ndarray, gain: float, gamma: float):
    """@brief 光照信道:线性增益 + 伽马"""
    scaled = np.clip(image.astype(np.float32) * gain, 0, 255) / 255.0
    return np.clip((scaled ** gamma) * 255.0, 0, 255).astype(np.uint8)


def _simulate_cell(angle: float, gain: float, gamma: float,
                   registry) -> int:
    """@brief 单格仿真 @return 存活样本数"""
    survived = 0
    for sample in range(SAMPLES_PER_CELL):
        original = _sample_document(seed=int(angle * 100 + gain * 10 + sample))
        tracer = new_tracer_id()
        artifacts = process_certificate(
            original, tracer, registry, "bw", "限评测翻拍矩阵 当日有效",
            {"distort_seed": sample, "wm_strength": -1})
        marked = cv2.imdecode(
            np.frombuffer(artifacts["jpeg_bytes"], np.uint8), cv2.IMREAD_COLOR)
        channel = _apply_lighting(_perspective(marked, angle), gain, gamma)
        ok, jpeg = cv2.imencode(".jpg", channel,
                                [cv2.IMWRITE_JPEG_QUALITY, 80])   # 翻拍二压
        recovered = _rectify(
            cv2.imdecode(jpeg, cv2.IMREAD_COLOR), angle)
        recovered = cv2.resize(
            recovered, (artifacts["embed_w"], artifacts["embed_h"]))
        luma = cv2.cvtColor(recovered,
                            cv2.COLOR_BGR2YCrCb)[:, :, 0].astype(np.float64)
        if bw.extract_tracer(luma, artifacts["wm_strength"]) == tracer:
            survived += 1
    return survived


def run_matrix() -> list:
    """@brief 全矩阵仿真 @return [(角度, 光照名, 存活数)]"""
    registry = EngineRegistry()
    rows = []
    for angle in ANGLES_DEG:
        for name, gain, gamma in LIGHTING:
            rows.append((angle, name,
                         _simulate_cell(angle, gain, gamma, registry)))
    return rows


def check_real_samples(input_dir: str):
    """@brief 真实翻拍复核:目录内文件名含 tracer 十六进制,逐个提取比对"""
    print(f"---- 真实翻拍样本复核({input_dir}) ----")
    for name in sorted(os.listdir(input_dir)):
        path = os.path.join(input_dir, name)
        image = cv2.imread(path)
        if image is None:
            continue
        luma = cv2.cvtColor(image,
                            cv2.COLOR_BGR2YCrCb)[:, :, 0].astype(np.float64)
        got = bw.extract_tracer(luma, bw.RECOMMENDED_STRENGTH)
        expected = "".join(ch for ch in name.lower()
                           if ch in "0123456789abcdef")[:12]
        verdict = "命中" if (got is not None
                             and f"{got:012x}" == expected) else "未命中"
        print(f"{name}: {verdict}(提取 {got and f'{got:012x}'})")


def main():
    """@brief CLI 入口:打印矩阵报告"""
    parser = argparse.ArgumentParser(description="13-R-CV-4 翻拍信道矩阵")
    parser.add_argument("--input-dir", default="",
                        help="真实翻拍样本目录(可选,文件名含 tracer hex)")
    args = parser.parse_args()
    print("==== 13-R-CV-4 翻拍信道矩阵(bw 仿真摸底) ====")
    print(f"{'角度':<6}{'光照':<6}{'存活':>8}")
    for angle, lighting, survived in run_matrix():
        print(f"{angle:<8}{lighting:<6}{survived}/{SAMPLES_PER_CELL:>4}")
    print("说明:bw 诚实边界为不承诺翻拍存活;矩阵为 stega 激活(GAP-13)后的"
          "对照基线。真实实测:同矩阵手机拍摄 ≥3 样本/格,--input-dir 复核。")
    if args.input_dir:
        check_real_samples(args.input_dir)


if __name__ == "__main__":
    main()
