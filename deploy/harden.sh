#!/usr/bin/env bash
# 港电统一平台宿主机加固脚本(等保三级基线,H09 交付物 1)
# 幂等可重跑;每步失败不中断但计入退出码。root 执行。
# Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
set -u
FAILED=0
step() { echo "==== $1"; }

step "1/6 数据与密钥目录权限(600/700)"
for dir in /opt/gangdian/data /opt/gangdian/backup; do
  if [ -d "$dir" ]; then
    chmod 700 "$dir" && find "$dir" -type f -exec chmod 600 {} + || FAILED=1
  fi
done

step "2/6 .env 权限锁定(禁止组/其他可读)"
if [ -f /opt/gangdian/.env ]; then
  chmod 600 /opt/gangdian/.env || FAILED=1
fi

step "3/6 内核网络参数(SYN 防护/禁重定向)"
cat > /etc/sysctl.d/90-gangdian.conf << 'SYSCTL'
net.ipv4.tcp_syncookies = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
SYSCTL
sysctl --system > /dev/null || FAILED=1

step "4/6 防火墙:仅放行 443 与运维口(如已装 ufw)"
if command -v ufw > /dev/null; then
  ufw --force enable > /dev/null
  ufw allow 443/tcp > /dev/null
  ufw allow 22/tcp > /dev/null || FAILED=1
else
  echo "  (未安装 ufw,请按现场防火墙策略放行 443/22)"
fi

step "5/6 NTP 对时(TOTP 与审计时间线依赖,详见 docs/deployment_manual.md)"
if command -v timedatectl > /dev/null; then
  timedatectl set-ntp true || FAILED=1
  timedatectl show -p NTPSynchronized || true
else
  echo "  (无 systemd-timesyncd,请配置 chrony 指向内网 NTP 源)"
fi

step "6/6 Docker 守护限权(禁 iptables 直改提示)"
echo "  请确认 docker 组成员仅限运维账号;容器一律非 root 运行(镜像已内置)。"

exit $FAILED
