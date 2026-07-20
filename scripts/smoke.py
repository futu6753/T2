#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@file    smoke.py
@brief   部署后冒烟探测(交付物清单 4):对运行中的 IdP 与五个 RP 逐一探测
         /healthz,核对 status/mode/crypto_suite 三字段并检查全平台一致性
         (套件与模式不一致是多实例部署事故高发点,06-E13)。
         用法:python3 scripts/smoke.py --idp http://127.0.0.1:9000 \\
                 --rp quiz=http://127.0.0.1:9101 --rp nvr=http://127.0.0.1:9102
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import argparse
import json
import sys
import urllib.request

HEALTHZ_TIMEOUT_SECONDS = 5


def _probe(name: str, base_url: str) -> dict:
    """@brief 探测单实例 /healthz @return 结果 dict(异常转 status=down)"""
    url = base_url.rstrip("/") + "/healthz"
    try:
        with urllib.request.urlopen(url, timeout=HEALTHZ_TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return {"name": name, "url": url, "status": payload.get("status"),
                "mode": payload.get("mode"),
                "crypto_suite": payload.get("crypto_suite")}
    except Exception as exc:  # noqa: BLE001 冒烟工具:任何异常都要落入报告
        return {"name": name, "url": url, "status": "down", "error": str(exc)}


def main() -> int:
    """@brief 探测全部实例并核对一致性 @return 0 全绿 / 1 存在异常"""
    parser = argparse.ArgumentParser(description="平台部署冒烟(healthz 一致性)")
    parser.add_argument("--idp", required=True, help="IdP 基址")
    parser.add_argument("--rp", action="append", default=[],
                        help="RP 探测项,格式 name=base_url,可多次")
    args = parser.parse_args()

    targets = [("idp", args.idp)]
    for item in args.rp:
        name, _, url = item.partition("=")
        targets.append((name, url))
    results = [_probe(name, url) for name, url in targets]
    print(json.dumps(results, ensure_ascii=False, indent=2))

    failures = [r for r in results if r.get("status") != "ok"]
    suites = {r.get("crypto_suite") for r in results if r.get("status") == "ok"}
    modes = {r.get("mode") for r in results if r.get("status") == "ok"}
    if failures:
        print(f"冒烟失败:{len(failures)} 个实例异常", file=sys.stderr)
        return 1
    if len(suites) > 1 or len(modes) > 1:
        print(f"冒烟失败:套件/模式不一致 suites={sorted(suites)}"
              f" modes={sorted(modes)}(06-E13)", file=sys.stderr)
        return 1
    print(f"冒烟通过:{len(results)} 实例全绿,suite={suites.pop()},mode={modes.pop()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
