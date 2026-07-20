# -*- coding: utf-8 -*-
"""
@file    adapter_deadletter.py
@brief   13-R-AD-3 死信重放运维工具:对运行中的适配器实例导出死信队列
         (JSON Lines 落盘)、检视统计、按文件回灌重放。重放走服务端
         /api/v1/deadletters/replay——已投递事件由进程内 DedupeCache 跳过,
         at-least-once 语义下游仍只见一次(对应测试 test_r_ad3_replay)。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
用法:
  导出:python3 scripts/adapter_deadletter.py export --out dead.jsonl
  检视:python3 scripts/adapter_deadletter.py inspect --file dead.jsonl
  重放:python3 scripts/adapter_deadletter.py replay [--file dead.jsonl]
"""
import argparse
import collections
import json
import sys
import urllib.error
import urllib.request


def _call(base_url: str, method: str, path: str, payload: dict = None) -> dict:
    """@brief 调用适配器北向接口(错误信封原样透出)"""
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base_url.rstrip("/") + path, data=body,
                                     method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"服务端返回 HTTP {exc.code}:"
                         f"{exc.read().decode('utf-8')}")


def do_export(base_url: str, out_path: str) -> int:
    """@brief 导出死信队列到 JSONL 文件"""
    doc = _call(base_url, "GET", "/api/v1/deadletters/export")
    with open(out_path, "w", encoding="utf-8") as handle:
        if doc.get("jsonl"):
            handle.write(doc["jsonl"] + "\n")
    print(f"已导出 {doc.get('count', 0)} 条死信 → {out_path}")
    return 0


def do_inspect(file_path: str) -> int:
    """@brief 检视 JSONL 导出(条数/来源/事件类型分布)"""
    sources = collections.Counter()
    types = collections.Counter()
    total = 0
    with open(file_path, "r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            total += 1
            sources[row.get("source", "?")] += 1
            types[row.get("event_type", "?")] += 1
    print(f"共 {total} 条死信")
    print("按来源:", dict(sources))
    print("按事件类型:", dict(types))
    return 0


def do_replay(base_url: str, file_path: str) -> int:
    """@brief 重放:有文件按文件回灌,无文件重放服务端当前死信队列"""
    payload = {}
    if file_path:
        with open(file_path, "r", encoding="utf-8") as handle:
            payload = {"jsonl": handle.read()}
    doc = _call(base_url, "POST", "/api/v1/deadletters/replay", payload)
    print(f"重放完成:入队 {doc.get('enqueued', 0)} 条,"
          f"已投递跳过 {doc.get('skipped', 0)} 条(下游只见一次)")
    return 0


def main() -> int:
    """@brief 命令行入口"""
    parser = argparse.ArgumentParser(description="死信导出/检视/重放")
    parser.add_argument("action", choices=("export", "inspect", "replay"))
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--out", default="dead_letters.jsonl",
                        help="export 输出文件")
    parser.add_argument("--file", default="",
                        help="inspect/replay 的输入文件")
    args = parser.parse_args()
    if args.action == "export":
        return do_export(args.base_url, args.out)
    if args.action == "inspect":
        if not args.file:
            print("inspect 需要 --file", file=sys.stderr)
            return 2
        return do_inspect(args.file)
    return do_replay(args.base_url, args.file)


if __name__ == "__main__":
    raise SystemExit(main())
