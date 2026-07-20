# -*- coding: utf-8 -*-
"""
@file    generator.py
@brief   三包流水线生成器(H10):make copyright-pack / paper-pack / patent-pack
         一键产出 dist/ 下按系统组织的申报材料。占位文本允许,但源码打印稿、
         行数统计、基线数据表均为真实抽取;所有产物打"内部资料"标签。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""
import datetime
import glob
import os
import shutil

from scripts.packs.systems import COMPANY, PLATFORM_VERSION, SYSTEMS

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DIST_DIR = os.path.join(REPO_ROOT, "dist")
BENCH_DATA = os.path.join(REPO_ROOT, "benchmarks", "data")
LINES_PER_PAGE = 50
PAGES_HEAD = 30
PAGES_TAIL = 30
SOURCE_SUFFIXES = (".py", ".js", ".vue", ".ts")
INTERNAL_TAG = "【内部资料】本文件由三包流水线自动生成,仅限申报准备使用。"


def _today() -> str:
    """@brief 生成日期"""
    return datetime.date.today().isoformat()


def _collect_source_lines(dirs: tuple) -> list:
    """@brief 收集系统源码行(剔空行与第三方目录,文件间以标头分隔)"""
    lines = []
    for rel_dir in dirs:
        base = os.path.join(REPO_ROOT, rel_dir)
        for path in sorted(glob.glob(os.path.join(base, "**", "*"),
                                     recursive=True)):
            if (not path.endswith(SOURCE_SUFFIXES)
                    or "__pycache__" in path or "node_modules" in path):
                continue
            rel = os.path.relpath(path, REPO_ROOT)
            lines.append(f"===== {rel} =====")
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                lines.extend(line.rstrip() for line in handle
                             if line.strip())
    return lines


def _paginate(lines: list, header: str) -> str:
    """@brief 页化(50 行/页,页眉含系统名与页码),截取前 30+后 30 页"""
    pages = [lines[i:i + LINES_PER_PAGE]
             for i in range(0, len(lines), LINES_PER_PAGE)]
    total = len(pages)
    if total > PAGES_HEAD + PAGES_TAIL:
        kept = (list(enumerate(pages[:PAGES_HEAD], 1))
                + [(-1, None)]
                + list(enumerate(pages[-PAGES_TAIL:], total - PAGES_TAIL + 1)))
    else:
        kept = list(enumerate(pages, 1))
    out = []
    for number, page in kept:
        if number == -1:
            out.append("\n…………(中间页略,软著要求仅前后各 30 页)…………\n")
            continue
        out.append(f"—— {header} · 第 {number}/{total} 页 ——")
        out.extend(page)
        out.append("")
    return "\n".join(out)


def _write(path: str, content: str):
    """@brief 落盘(自动建目录,统一 UTF-8)"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _copy_baselines(names: tuple, dest: str) -> list:
    """@brief 拷贝挂钩基线数据表(模糊匹配 benchmarks/data/*.csv 与样例 json)"""
    copied = []
    for pattern in names:
        for src in glob.glob(os.path.join(BENCH_DATA, f"*{pattern}*")):
            os.makedirs(dest, exist_ok=True)
            shutil.copy2(src, dest)
            copied.append(os.path.basename(src))
    return sorted(set(copied))


def build_copyright(key: str) -> str:
    """@brief 生成单系统软著包 @return 输出目录"""
    meta = SYSTEMS[key]
    out_dir = os.path.join(DIST_DIR, "copyright", key)
    lines = _collect_source_lines(meta["dirs"])
    header = f"{meta['name']} {PLATFORM_VERSION}"
    _write(os.path.join(out_dir, "source_listing.txt"),
           f"{INTERNAL_TAG}\n{header} 源码打印稿(自动抽取,剔空行)\n"
           f"著作权人:{COMPANY}\n生成日期:{_today()}\n\n"
           + _paginate(lines, header))
    features = "\n".join(f"- {item}" for item in meta["features"])
    _write(os.path.join(out_dir, "profile.md"), f"""# 系统概况页(软著申请表素材)

{INTERNAL_TAG}

| 字段 | 内容 |
| ---- | ---- |
| 软件全称 | {meta['name']} |
| 简称 | {meta['short']} |
| 版本号 | {PLATFORM_VERSION} |
| 著作权人 | {COMPANY} |
| 开发完成日期 | {_today()}(以实际申报口径为准) |
| 源码总行数(剔空行) | {len(lines)} |
| 开发环境 | Python 3.12 / FastAPI / SQLite·PostgreSQL / Vue3(如适用) |
| 运行环境 | Linux x86_64,Docker Compose 参考拓扑(deploy/) |

## 主要功能
{features}
""")
    flows = "\n".join(f"{i}. {flow}(截图占位:docs/screenshots/{key}_{i}.png,"
                      f"用 DEMO 演示数据截取)"
                      for i, flow in enumerate(meta["manual_flows"], 1))
    _write(os.path.join(out_dir, "manual.md"), f"""# {meta['name']} 用户操作手册

{INTERNAL_TAG}

> 手册按主流程组织;所有截图 MUST 使用 DEMO 演示数据,禁止出现真实
> 人员/设备信息(H05 §四)。定稿后由排版工序导出 PDF。

## 主流程
{flows}

## 通用说明
- 登录入口统一经 UniPass 单点登录;DEMO 模式含演示账号一键体验。
- 每个页面右上角提供当前账号与退出;异常提示遵循平台统一文案契约。
""")
    inventions = "\n".join(f"- {title}(锚点 {anchor}):{summary}"
                           for title, anchor, summary in meta["inventions"])
    _write(os.path.join(out_dir, "design.md"), f"""# {meta['name']} 设计说明书(简版)

{INTERNAL_TAG}

## 架构位置
- 本系统运行于港电实验室统一平台,身份统一经 UniPass IdP(OIDC),
  审计统一走 `gd_storage.audit` 链式哈希,密码学统一走 `gd_crypto` 抽象
  (intl/gm 双套件,密文对象自描述)。

## 功能结构
{features}

## 关键设计点
{inventions}

## 部署形态
- Docker Compose 参考拓扑见 `deploy/`;多实例共享态入 Redis,
  会话与锁定均可跨实例存活。
""")
    return out_dir


def build_paper(key: str) -> str:
    """@brief 生成单系统论文包 @return 输出目录"""
    meta = SYSTEMS[key]
    out_dir = os.path.join(DIST_DIR, "paper", key)
    copied = _copy_baselines(meta["baselines"], os.path.join(out_dir, "data"))
    data_list = "\n".join(f"- data/{name}" for name in copied) or "-(待运行基准生成)"
    points = "\n".join(f"### {title}\n锚点:{anchor}\n\n{summary}。\n"
                       for title, anchor, summary in meta["inventions"])
    _write(os.path.join(out_dir, "method.md"), f"""# {meta['short']} 方法稿骨架

{INTERNAL_TAG}

## 问题定义(占位:结合现场痛点撰写)

## 方法
{points}
## 实验与基线
本包 data/ 内数据表首行注释含环境指纹(时间/CPU/内存/Python/种子),
论文引用的每个数字 MUST 指回其中一次运行记录(H10 §四)。

{data_list}
""")
    commands = "\n".join(
        f"- `python3 benchmarks/{script}`" for script in sorted(
            os.path.basename(p) for p in
            glob.glob(os.path.join(REPO_ROOT, "benchmarks", "*_benchmark.py"))
            + glob.glob(os.path.join(REPO_ROOT, "benchmarks", "*_replay.py"))
            + glob.glob(os.path.join(REPO_ROOT, "benchmarks", "*_matrix.py"))))
    _write(os.path.join(out_dir, "reproducibility.md"), f"""# 可复现说明

{INTERNAL_TAG}

- 全部基准离线一键运行,固定随机种子;数据表落 `benchmarks/data/`。
- 一键命令(在仓库根目录):
{commands}
- 环境:Python 3.12,依赖见 requirements.txt;无网络依赖。
""")
    return out_dir


def build_patent(key: str) -> str:
    """@brief 生成单系统专利包 @return 输出目录"""
    meta = SYSTEMS[key]
    out_dir = os.path.join(DIST_DIR, "patent", key)
    sections = []
    for title, anchor, summary in meta["inventions"]:
        sections.append(f"""## 发明点候选:{title}

- 规约锚点:{anchor}(实现与验收测试见版本库对应 R 组用例)
- 技术领域:电力行业信息系统安全 / 工业物联网软件
- 背景技术问题(占位:检索对比后补充现有技术缺陷)
- 技术方案概述:{summary}。
- 实施方式素材:见 `{meta['dirs'][0]}` 对应模块与 `tests/` 中 {anchor}
  验收用例(用例即实施例的可验证描述)。
- 有益效果:以 `benchmarks/data/` 挂钩数据表为证
  ({', '.join(meta['baselines'])})。
""")
    _write(os.path.join(out_dir, "disclosure.md"),
           f"# {meta['name']} 技术交底书(骨架)\n\n{INTERNAL_TAG}\n\n"
           + "\n".join(sections))
    claims = "\n\n".join(
        f"{i}. 一种{title},其特征在于:{summary}。(候选独立权利要求,"
        f"以代理人改写为准)"
        for i, (title, _, summary) in enumerate(meta["inventions"], 1))
    _write(os.path.join(out_dir, "claims_candidates.md"),
           f"# 权利要求候选\n\n{INTERNAL_TAG}\n\n{claims}\n")
    return out_dir


BUILDERS = {"copyright": build_copyright, "paper": build_paper,
            "patent": build_patent}


def build(kind: str, system: str = "all") -> list:
    """@brief 构建入口 @param kind copyright|paper|patent @return 输出目录列表"""
    keys = list(SYSTEMS) if system == "all" else [system]
    return [BUILDERS[kind](key) for key in keys]
