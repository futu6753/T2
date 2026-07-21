#!/usr/bin/env bash
# =============================================================================
# @file  fetch_libs.sh
# @brief 前端三方库本地化预取(H01 §二 ARC-5:内网离线,禁运行时 CDN)。
#        当前清单:Three.js(F3 大屏)。产物入 apps/factory3d/web/vendor/,
#        随部署包交付;目标环境部署时不联网。
# @usage bash deploy/fetch_libs.sh   # 需可出网的打包机;内网用制品库镜像同 URL 结构
# Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

THREE_VERSION="0.160.1"
VENDOR_DIR="apps/factory3d/web/vendor"
REGISTRY="${NPM_REGISTRY:-https://registry.npmmirror.com}"  # 默认国内源(npmmirror);出海环境可覆盖

mkdir -p "${VENDOR_DIR}"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

echo "[fetch_libs] 下载 three@${THREE_VERSION} (${REGISTRY})"
curl -fsSL "${REGISTRY}/three/-/three-${THREE_VERSION}.tgz" -o "${TMP}/three.tgz"
tar -xzf "${TMP}/three.tgz" -C "${TMP}"
cp "${TMP}/package/build/three.module.min.js" "${VENDOR_DIR}/three.module.min.js"
cp "${TMP}/package/LICENSE" "${VENDOR_DIR}/THREE_LICENSE"
sha256sum "${VENDOR_DIR}/three.module.min.js" | tee "${VENDOR_DIR}/three.module.min.js.sha256"
echo "[fetch_libs] 完成:${VENDOR_DIR}/three.module.min.js"
