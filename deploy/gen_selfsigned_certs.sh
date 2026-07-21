#!/usr/bin/env bash
# =============================================================================
# @file  gen_selfsigned_certs.sh
# @brief 生成内网自签证书(单机六系统):一个自签 CA + 一张覆盖六域名的
#        SAN 证书,输出到 deploy/certs/。仅供内网/测试;正式环境请用内网
#        CA 签发或商用证书替换同名文件。
# @usage bash deploy/gen_selfsigned_certs.sh
#        客户端信任:把 deploy/certs/gangdian-ca.crt 导入访问者浏览器/系统
#        受信任根,否则自签证书会告警。
# Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"
CERT_DIR="certs"
DAYS_CA=3650
DAYS_LEAF=825           # 主流浏览器对 leaf 证书 >825 天不信任
mkdir -p "${CERT_DIR}"
cd "${CERT_DIR}"

DOMAINS=(sso cv nvr f3d quiz adapter)
BASE_DOMAIN="gangdian.internal"

if [ -f gangdian.crt ] && [ -f gangdian.key ]; then
  echo "证书已存在(gangdian.crt/key);如需重签请先删除 ${CERT_DIR}/ 下文件。"
  exit 0
fi

echo "[1/3] 生成自签 CA(有效期 ${DAYS_CA} 天)"
openssl genrsa -out gangdian-ca.key 4096
openssl req -x509 -new -nodes -key gangdian-ca.key -sha256 -days "${DAYS_CA}" \
  -out gangdian-ca.crt \
  -subj "/C=CN/O=Gangdian Lab/CN=Gangdian Internal Root CA"

echo "[2/3] 生成服务器私钥与 CSR(SAN 覆盖六域名)"
{
  echo "[req]"
  echo "distinguished_name = dn"
  echo "req_extensions = v3_req"
  echo "prompt = no"
  echo "[dn]"
  echo "C = CN"
  echo "O = Gangdian Lab"
  echo "CN = sso.${BASE_DOMAIN}"
  echo "[v3_req]"
  echo "keyUsage = digitalSignature, keyEncipherment"
  echo "extendedKeyUsage = serverAuth"
  echo "subjectAltName = @alt"
  echo "[alt]"
  index=1
  for sub in "${DOMAINS[@]}"; do
    echo "DNS.${index} = ${sub}.${BASE_DOMAIN}"
    index=$((index + 1))
  done
} > san.cnf

openssl genrsa -out gangdian.key 2048
openssl req -new -key gangdian.key -out gangdian.csr -config san.cnf

echo "[3/3] 用 CA 签发 leaf 证书(有效期 ${DAYS_LEAF} 天)"
openssl x509 -req -in gangdian.csr -CA gangdian-ca.crt -CAkey gangdian-ca.key \
  -CAcreateserial -out gangdian.crt -days "${DAYS_LEAF}" -sha256 \
  -extfile san.cnf -extensions v3_req

chmod 600 gangdian.key gangdian-ca.key
rm -f gangdian.csr san.cnf

echo
echo "完成。产物在 deploy/${CERT_DIR}/:"
echo "  gangdian.crt / gangdian.key   → nginx 使用(compose 已挂载)"
echo "  gangdian-ca.crt               → 分发给访问者导入受信任根,消除告警"
echo
echo "六域名需解析到本机:在访问者 hosts 或内网 DNS 添加(示例):"
for sub in "${DOMAINS[@]}"; do
  echo "  <本机IP>  ${sub}.${BASE_DOMAIN}"
done
