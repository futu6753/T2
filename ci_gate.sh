#!/usr/bin/env bash
# =============================================================================
# @file  ci_gate.sh
# @brief CI 门禁统一入口(H09 §二 E):任何一步失败即中断,不可跳过(H00 G8)
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"

echo "[1/4] 全量回归测试(SQLite)"
python3 -m unittest discover -s tests

if [ -n "${GD_TEST_PG_URL:-}" ]; then
  echo "[1b] 双库同测(PostgreSQL)  # GAP-03 一键入口"
  GD_DB_URL="${GD_TEST_PG_URL}" python3 -m unittest discover -s tests
fi

echo "[2/4] 敏感信息扫描(P0-1,含 .md)"
python3 scripts/scan_secrets.py --path .

echo "[3/4] DEMO_MODE 单一入口静态检查(H05)"
python3 scripts/check_demo_mode_usage.py

echo "[4/4] 语法编译检查"
python3 -m compileall -q packages selfcheck scripts tests

echo "CI 门禁全部通过"
