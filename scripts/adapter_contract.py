# -*- coding: utf-8 -*-
"""
@file    adapter_contract.py
@brief   适配器契约防漂移流水线(H06-E17 / H08 §6 / H09 §二 E):
         export —— 导出 openapi.json 与映射 DSL 规范化锁文件到
                    harness/dist/(首次导出即冻结基线);
         diff  —— 现场重新生成并与冻结基线逐字节比对,漂移即非零退出。
         映射 DSL 文件(harness/mappings/*.yaml)升格为契约工件,
         同入 diff(13-R-AD-1)。
@usage   python3 scripts/adapter_contract.py export|diff
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import json
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "packages"))

DIST_DIR = os.path.join(REPO_ROOT, "harness", "dist")
OPENAPI_PATH = os.path.join(DIST_DIR, "adapter.openapi.json")
MAPPINGS_LOCK_PATH = os.path.join(DIST_DIR, "mappings.lock.json")
MAPPINGS_DIR = os.path.join(REPO_ROOT, "harness", "mappings")


def render_openapi() -> str:
    """@brief 现场装配应用并导出规范化 openapi JSON"""
    from apps.adapter.api.main import create_app
    from apps.adapter.core.config import Settings
    app = create_app(Settings())
    return json.dumps(app.openapi(), ensure_ascii=False, sort_keys=True,
                      indent=2) + "\n"


def render_mappings_lock() -> str:
    """@brief 映射 DSL 目录 → 规范化 JSON 锁(文件名排序,内容排序键)"""
    from apps.adapter.core import yamlite
    lock = {}
    for name in sorted(os.listdir(MAPPINGS_DIR)):
        if name.endswith(".yaml"):
            lock[name] = yamlite.load_file(os.path.join(MAPPINGS_DIR, name))
    return json.dumps(lock, ensure_ascii=False, sort_keys=True,
                      indent=2) + "\n"


def export() -> int:
    """@brief 导出/刷新冻结基线"""
    os.makedirs(DIST_DIR, exist_ok=True)
    with open(OPENAPI_PATH, "w", encoding="utf-8") as handle:
        handle.write(render_openapi())
    with open(MAPPINGS_LOCK_PATH, "w", encoding="utf-8") as handle:
        handle.write(render_mappings_lock())
    print(f"契约基线已导出:{os.path.relpath(OPENAPI_PATH, REPO_ROOT)}、"
          f"{os.path.relpath(MAPPINGS_LOCK_PATH, REPO_ROOT)}")
    return 0


def _diff_one(label: str, frozen_path: str, live_text: str) -> int:
    """@brief 单工件比对 @return 漂移数(0/1)"""
    if not os.path.exists(frozen_path):
        print(f"[漂移] {label}:冻结基线缺失({frozen_path}),先执行 export")
        return 1
    with open(frozen_path, "r", encoding="utf-8") as handle:
        frozen = handle.read()
    if frozen != live_text:
        frozen_doc, live_doc = json.loads(frozen), json.loads(live_text)
        detail = _first_divergence(frozen_doc, live_doc, label)
        print(f"[漂移] {label}:{detail}")
        return 1
    print(f"[一致] {label}:0 error")
    return 0


def _first_divergence(frozen, live, path: str) -> str:
    """@brief 递归定位首个分歧路径(人话报错)"""
    if type(frozen) is not type(live):
        return f"{path} 类型 {type(frozen).__name__}→{type(live).__name__}"
    if isinstance(frozen, dict):
        for key in sorted(set(frozen) | set(live)):
            if key not in frozen:
                return f"{path}.{key} 为新增项"
            if key not in live:
                return f"{path}.{key} 被移除"
            if frozen[key] != live[key]:
                return _first_divergence(frozen[key], live[key],
                                         f"{path}.{key}")
    if isinstance(frozen, list) and len(frozen) != len(live):
        return f"{path} 列表长度 {len(frozen)}→{len(live)}"
    return f"{path} 取值 {frozen!r}→{live!r}"


def diff() -> int:
    """@brief 零漂移检查(任一工件漂移即非零退出)"""
    drift = _diff_one("openapi.json", OPENAPI_PATH, render_openapi())
    drift += _diff_one("mappings.lock.json", MAPPINGS_LOCK_PATH,
                       render_mappings_lock())
    if drift:
        print(f"契约漂移 {drift} 处:若为有意变更,评审后执行 export 刷新基线")
    return 1 if drift else 0


def main() -> int:
    """@brief 命令入口"""
    action = sys.argv[1] if len(sys.argv) > 1 else "diff"
    if action == "export":
        return export()
    if action == "diff":
        return diff()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
