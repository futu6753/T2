#!/usr/bin/env bash
# =============================================================================
# @file  bootstrap.sh
# @brief 单机六系统一键部署引导(里程碑 10 部署包)。解决 RP 依赖 IdP 先登记
#        SSO 客户端的"鸡生蛋"问题,采用两阶段:
#          阶段一:起 postgres/redis/idp → 在 idp 容器内登记四 RP 客户端 →
#                  回填 .env 的四个 *_SSO_SECRET;
#          阶段二:起全部六系统 + nginx → 冒烟。
#        幂等:已生成的密钥/证书不覆盖;可重复执行补齐缺失步骤。
# @usage sudo bash deploy/bootstrap.sh            # 需已装 docker + compose 插件
#        环境可改:BASE_DOMAIN(默认 gangdian.internal)、
#                  COMPOSE(默认 deploy/docker-compose.single.yml)
# Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."          # 仓库根
REPO_ROOT="$(pwd)"
BASE_DOMAIN="${BASE_DOMAIN:-gangdian.internal}"
COMPOSE_FILE="${COMPOSE:-deploy/docker-compose.single.yml}"
ENV_FILE=".env"
DC="docker compose -f ${COMPOSE_FILE} --env-file ${ENV_FILE}"

log() { echo -e "\n==== $1"; }

# --- 0. 前置检查 ---------------------------------------------------------
log "0/6 前置检查(docker / compose 插件)"
command -v docker >/dev/null || { echo "未安装 docker"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "缺 docker compose 插件"; exit 1; }
echo "提示:国内网络请先给 dockerd 配 registry 镜像加速"
echo "     (/etc/docker/daemon.json 的 registry-mirrors),compose 内镜像已用阿里云源。"

# --- 1. .env 生成与密钥填充(幂等)---------------------------------------
log "1/6 生成 .env 与密钥(缺项才生成)"
if [ ! -f "${ENV_FILE}" ]; then
  cp deploy/env.example "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
  echo "已从模板创建 .env"
fi

fill() {   # fill KEY VALUE:仅当该键为空(KEY= 结尾)时写入
  local key="$1" val="$2"
  if grep -qE "^${key}=$" "${ENV_FILE}"; then
    sed -i "s|^${key}=$|${key}=${val}|" "${ENV_FILE}"
    echo "  填充 ${key}"
  fi
}
# PG_PASSWORD 模板里是占位中文,单独处理
if grep -qE "^PG_PASSWORD=请生成强口令$" "${ENV_FILE}"; then
  sed -i "s|^PG_PASSWORD=.*$|PG_PASSWORD=$(openssl rand -hex 24)|" "${ENV_FILE}"
  echo "  填充 PG_PASSWORD"
fi
fill MASTER_KEY_HEX "$(openssl rand -hex 32)"

# --- 2. 自签证书 ---------------------------------------------------------
log "2/6 自签证书(已存在则跳过)"
bash deploy/gen_selfsigned_certs.sh

# --- 3. 阶段一:起底座 + IdP ---------------------------------------------
log "3/6 阶段一:启动 postgres / redis / idp"
${DC} up -d --build postgres redis idp
echo "等待 idp 健康..."
for _ in $(seq 1 30); do
  if ${DC} exec -T idp python3 -c "import urllib.request,sys; \
     sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9000/healthz',timeout=2).status==200 else 1)" \
     2>/dev/null; then echo "idp 就绪"; break; fi
  sleep 2
done

# --- 4. 登记四 RP 客户端并回填 .env -------------------------------------
log "4/6 登记 SSO 客户端并回填密钥"
# 在 idp 容器内执行(与 idp 同库同主密钥),只取 KEY=VALUE 行回填宿主 .env
REG_OUT="$(${DC} exec -T idp python3 scripts/register_sso_clients.py \
  --base-domain "${BASE_DOMAIN}" 2>/dev/null || true)"
echo "${REG_OUT}" | grep -E '^[A-Z0-9_]+_SSO_SECRET=' | while read -r line; do
  key="${line%%=*}"
  if grep -qE "^${key}=$" "${ENV_FILE}"; then
    sed -i "s|^${key}=.*$|${line}|" "${ENV_FILE}"
    echo "  回填 ${key}"
  fi
done
# 校验四密钥均已就位
missing=0
for k in CV_SSO_SECRET NVR_SSO_SECRET F3D_SSO_SECRET QUIZ_SSO_SECRET; do
  grep -qE "^${k}=.+" "${ENV_FILE}" || { echo "  缺 ${k}(可能已登记过,去管理台取或删客户端重登记)"; missing=1; }
done
[ "${missing}" = "1" ] && echo "  注意:存在未回填密钥;RP 会因缺密钥启动失败。"

# --- 5. 阶段二:起全部服务 ----------------------------------------------
log "5/6 阶段二:启动全部六系统 + nginx"
${DC} up -d --build

# --- 6. 冒烟 -------------------------------------------------------------
log "6/6 冒烟(容器内 healthz)"
sleep 5
for svc in idp:9000 certvault:9100 nvr:9200 factory3d:9300 quiz:9400; do
  name="${svc%%:*}"; port="${svc##*:}"
  if ${DC} exec -T "${name}" python3 -c "import urllib.request,sys; \
     r=urllib.request.urlopen('http://127.0.0.1:${port}/healthz',timeout=3); \
     print('${name}', r.status)" 2>/dev/null; then :; else
    echo "  ${name} healthz 未通过(查 ${DC} logs ${name})"; fi
done

cat <<TIP

部署引导完成。后续:
  1) 初始化管理员:  ${DC} exec idp python3 scripts/create_admin.py --account op_admin
  2) 访问者解析六域名到本机 IP,并导入 deploy/certs/gangdian-ca.crt 消除告警;
     六域名:sso./cv./nvr./f3d./quiz./adapter.${BASE_DOMAIN}
  3) 外部冒烟(带 -k 因自签):
     curl -k https://sso.${BASE_DOMAIN}/healthz
  4) 常用:  ${DC} ps   |   ${DC} logs -f idp   |   ${DC} down
TIP
