#!/usr/bin/env bash
# =============================================================================
# @file  ci_gate.sh
# @brief CI 门禁统一入口(H09 §二 E):任何一步失败即中断,不可跳过(H00 G8)
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"

echo "[1/6] 全量回归测试(SQLite)"
python3 -m unittest discover -s tests

if [ -n "${GD_TEST_PG_URL:-}" ]; then
  echo "[1b] 双库同测(PostgreSQL)  # GAP-03 一键入口"
  GD_DB_URL="${GD_TEST_PG_URL}" python3 -m unittest discover -s tests
fi

echo "[2/6] 敏感信息扫描(P0-1,含 .md)"
python3 scripts/scan_secrets.py --path .

echo "[3/6] DEMO_MODE 单一入口静态检查(H05)"
python3 scripts/check_demo_mode_usage.py

echo "[4/6] 语法编译检查"
python3 -m compileall -q packages selfcheck scripts tests

echo "[5/6] 前端静态门禁(E3 双条款 + 构建产物外链零命中,H09 I)"
python3 scripts/check_frontend_e3.py
python3 scripts/scan_frontend_external.py

if command -v node >/dev/null 2>&1 && [ -d frontend/node_modules ]; then
  echo "[6/6] 前端 lint(eslint + prettier;目标离线环境无 node 则跳过)"
  (cd frontend && npx eslint . && npx prettier --check . --log-level warn)
else
  echo "[6/6] 前端 lint:node/依赖缺失,跳过(打包机必跑)"
fi

echo "CI 门禁全部通过"
