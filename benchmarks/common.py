# -*- coding: utf-8 -*-
"""
@file    common.py
@brief   基准脚本共享助手(H10 §四):环境指纹(CPU/内存/版本/随机种子)与
         CSV 数据表归档(benchmarks/data/,随版本库交付)。论文引用的每个数字
         MUST 可指回一次带指纹的运行记录。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import csv
import datetime
import os
import platform
import sys

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def environment_fingerprint(seed: int = None) -> dict:
    """@brief 采集运行环境指纹 @param seed 固定随机种子(可复现要求)"""
    memory_kb = 0
    try:
        with open("/proc/meminfo", "r", encoding="ascii") as handle:
            memory_kb = int(handle.readline().split()[1])
    except OSError:
        pass
    return {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "cpu": platform.processor() or platform.machine(),
        "cpu_count": os.cpu_count(),
        "memory_mb": memory_kb // 1024,
        "seed": seed,
    }


def write_table(name: str, header: list, rows: list, fingerprint: dict) -> str:
    """
    @brief  写 CSV 数据表(首行注释携带环境指纹,# 前缀)
    @param  name 表名(落盘 benchmarks/data/<name>.csv)
    @return 输出文件路径
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{name}.csv")
    with open(path, "w", encoding="utf-8", newline="") as handle:
        meta = ";".join(f"{key}={value}" for key, value in fingerprint.items())
        handle.write(f"# fingerprint: {meta}\n")
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path
