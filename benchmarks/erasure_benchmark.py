# -*- coding: utf-8 -*-
"""
@file    erasure_benchmark.py
@brief   13-R-CV-1 明水印擦除对抗评测(离线工件,不进生产链路):
         对发证成品执行「检测水印区→inpainting 擦除→暗码提取」全链评测,
         报告①暗码在擦除后的存活率;②擦除残留的视觉痕迹(与原图差异)。
         默认擦除器为 cv2.inpaint(TELEA,经典基线);深度擦除器(LaMa 类)
         通过 --lama-dir 指向本地权重目录挂接(TODO(GAP-13) 随模型导入激活)。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
用法:PYTHONPATH=packages:. python3 benchmarks/erasure_benchmark.py --rounds 5
"""
import argparse
import os
import sys

import numpy as np
import cv2

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "packages"))
sys.path.insert(0, REPO_ROOT)

from apps.certvault.wm import bw, visible                    # noqa: E402
from apps.certvault.wm.engines import EngineRegistry         # noqa: E402
from apps.certvault.wm.payload import new_tracer_id          # noqa: E402
from apps.certvault.wm.pipeline import process_certificate   # noqa: E402


def _sample_document(seed: int) -> np.ndarray:
    """@brief 合成证件样图(低频底纹+多行文字,贴近真实版面)"""
    rng = np.random.default_rng(seed)
    gray = cv2.GaussianBlur(
        (rng.random((640, 960)) * 150 + 70).astype(np.uint8), (41, 41), 11)
    image = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    for line in range(6):
        cv2.putText(image, f"GANGDIAN SAMPLE LINE {line}",
                    (60, 120 + line * 80), cv2.FONT_HERSHEY_SIMPLEX,
                    1.0, (35, 35, 35), 2)
    return image


def _watermark_mask(marked: np.ndarray, original: np.ndarray) -> np.ndarray:
    """
    @brief  攻击者视角的水印区检测:成品与"估计底图"差异阈值化。
            评测给攻击者最有利条件——直接用真实原图差分(上界攻击)。
    """
    diff = cv2.absdiff(marked, original).max(axis=2)
    _, mask = cv2.threshold(diff, 8, 255, cv2.THRESH_BINARY)
    return cv2.dilate(mask, np.ones((5, 5), np.uint8), iterations=1)


def _erase_telea(marked: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """@brief 经典擦除基线:cv2.inpaint TELEA"""
    return cv2.inpaint(marked, mask, 5, cv2.INPAINT_TELEA)


def _erase_lama(marked: np.ndarray, mask: np.ndarray, lama_dir: str):
    """@brief LaMa 类深度擦除 Provider 口(权重未导入则明确不可用)"""
    if not (lama_dir and os.path.isdir(lama_dir)
            and any(name.endswith((".onnx", ".pt"))
                    for name in os.listdir(lama_dir))):
        return None                      # 未导入权重:跳过并在报告注明
    raise NotImplementedError(
        "LaMa 推理挂接随 GAP-13 交付(权重已检测到,请更新本函数)")


def run_benchmark(rounds: int, lama_dir: str) -> dict:
    """@brief 主评测循环 @return 汇总统计"""
    registry = EngineRegistry()
    survived_telea, survived_lama = 0, 0
    residue_scores = []
    lama_active = False
    for round_no in range(rounds):
        original = _sample_document(seed=round_no)
        tracer = new_tracer_id()
        artifacts = process_certificate(
            original, tracer, registry, "bw",
            "限评测对象内部评测 当日有效",
            {"distort_seed": round_no, "wm_strength": -1})
        marked = cv2.imdecode(
            np.frombuffer(artifacts["jpeg_bytes"], np.uint8), cv2.IMREAD_COLOR)
        # 原图按同参微扭曲对齐(擦除者上界:知道底图)
        aligned = visible.micro_distort(original, 1.2, round_no)
        aligned = cv2.resize(aligned, (marked.shape[1], marked.shape[0]))
        mask = _watermark_mask(marked, aligned)
        for erase_fn, is_lama in ((_erase_telea, False),):
            erased = erase_fn(marked, mask)
            luma = cv2.cvtColor(erased,
                                cv2.COLOR_BGR2YCrCb)[:, :, 0].astype(np.float64)
            got = bw.extract_tracer(luma, artifacts["wm_strength"])
            if got == tracer:
                survived_telea += 1
            residue_scores.append(
                float(cv2.absdiff(erased, aligned).mean()))
        lama_result = _erase_lama(marked, mask, lama_dir)
        if lama_result is not None:
            lama_active = True
    return {"rounds": rounds,
            "telea_survival": survived_telea / rounds,
            "mean_residue": sum(residue_scores) / len(residue_scores),
            "lama_active": lama_active}


def main():
    """@brief CLI 入口:打印评测报告(供 R-CV-1 强度/密度参数整定)"""
    parser = argparse.ArgumentParser(description="13-R-CV-1 擦除对抗评测")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--lama-dir", default=os.environ.get("LAMA_DIR", ""))
    args = parser.parse_args()
    report = run_benchmark(args.rounds, args.lama_dir)
    print("==== 13-R-CV-1 擦除对抗评测报告 ====")
    print(f"评测轮次           : {report['rounds']}")
    print(f"TELEA 擦除后暗码存活: {report['telea_survival']:.0%}")
    print(f"擦除残留(均差)    : {report['mean_residue']:.2f}"
          "(越高=擦除痕迹越明显,抗擦除设计有效)")
    print("LaMa 深度擦除       :",
          "已挂接" if report["lama_active"]
          else "未导入权重(--lama-dir 指向权重目录后激活,GAP-13)")
    print("说明:评测按攻击者上界(已知底图)执行;生产建议按报告调整"
          " opacity/density 使 TELEA 存活率 ≥80% 且残留可察觉。")


if __name__ == "__main__":
    main()
