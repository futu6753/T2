# -*- coding: utf-8 -*-
"""
@file    nvr_check_cli.py
@brief   单设备检测 CLI(L04 §3):--host/--username/--password-env(或交互
         隐藏输入)/--no-icmp/--timeout/--json;在线退出码 0,否则 1。
         生产 ISAPI 探针随 GAP-14 挂接;未挂接时 CLI 走 TCP/ICMP 降级判定
         并明示。凭据不出现在任何输出。
@author  港电实验室平台组
@date    2026-07-19
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
用法:PYTHONPATH=packages:. python3 scripts/nvr_check_cli.py --host 10.0.0.5
"""
import argparse
import getpass
import json
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "packages"))
sys.path.insert(0, REPO_ROOT)

from apps.nvr.checker import DeviceChecker, IsapiTimeout   # noqa: E402


def _placeholder_isapi(host, port, username, password, timeout):
    """@brief ISAPI 探针占位(GAP-14):直接超时信号进入 TCP/ICMP 降级判定"""
    raise IsapiTimeout()


def main() -> int:
    """@brief CLI 入口 @return 进程退出码(在线=0)"""
    parser = argparse.ArgumentParser(description="NVR 单设备检测(L04 §3)")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=80)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password-env", default="",
                        help="从该环境变量读口令;缺省交互隐藏输入")
    parser.add_argument("--no-icmp", action="store_true")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.password_env:
        password = os.environ.get(args.password_env, "")
    else:
        password = getpass.getpass("设备口令(不回显): ")
    checker = DeviceChecker(_placeholder_isapi,
                            icmp_enabled=not args.no_icmp,
                            timeout_seconds=args.timeout)
    result = checker.check(args.host, args.port, args.username, password)
    result["note"] = ("ISAPI 真探针未挂接(GAP-14),本结果为 TCP/ICMP"
                      " 降级判定")
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"状态: {result['status']}  耗时: {result['latency_ms']}ms")
        print(f"明细: {result['detail']}")
        print(result["note"])
    return 0 if result["status"] == "online" else 1


if __name__ == "__main__":
    sys.exit(main())
