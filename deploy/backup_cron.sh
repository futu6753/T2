#!/usr/bin/env bash
# 港电统一平台每日备份脚本(H09 交付物 1):PostgreSQL 全量 + blob 目录 +
# 保留 14 天;备份文件权限 600。crontab 样例:
#   30 2 * * * /opt/gangdian/deploy/backup_cron.sh >> /var/log/gd_backup.log 2>&1
# 恢复演练要求:每季度按 docs/deployment_manual.md §7 实做一次恢复验证。
# Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
set -euo pipefail
BACKUP_ROOT=${BACKUP_ROOT:-/opt/gangdian/backup}
BLOB_DIR=${BLOB_DIR:-/opt/gangdian/data/blobs}
RETENTION_DAYS=${RETENTION_DAYS:-14}
STAMP=$(date +%Y%m%d_%H%M%S)
DEST="$BACKUP_ROOT/$STAMP"
mkdir -p "$DEST"

# 1) 数据库(容器内 pg_dump;PG_PASSWORD 来自 .env)
docker compose exec -T postgres pg_dump -U gd gangdian | gzip > "$DEST/gangdian.sql.gz"

# 2) 证件与笔记图密文 blob(已是信封密文,备份仍按敏感数据管理)
if [ -d "$BLOB_DIR" ]; then
  tar czf "$DEST/blobs.tar.gz" -C "$(dirname "$BLOB_DIR")" "$(basename "$BLOB_DIR")"
fi

chmod -R 600 "$DEST"/* && chmod 700 "$DEST"

# 3) 保留策略:清理超期备份
find "$BACKUP_ROOT" -maxdepth 1 -mindepth 1 -type d -mtime +"$RETENTION_DAYS" \
  -exec rm -rf {} +
echo "backup ok: $DEST"
