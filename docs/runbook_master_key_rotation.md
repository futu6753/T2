# Runbook:主密钥轮换(H06-E10 轮换=迁移)

> 触发:年度例行 / 疑似泄露。窗口:低峰;可断点,无需停机(建议只读窗口)。

## 步骤
1. 备份:确认当日备份存在(`deploy/backup_cron.sh` 产物)。
2. 生成新钥:`python3 scripts/gen_master_key.py` → 抄录保管。
3. 迁移执行(逐库):
   ```
   OLD_MASTER_KEY_HEX=<旧> OLD_MASTER_KEY_ID=mk1 \
   MASTER_KEY_HEX=<新> MASTER_KEY_ID=mk2 \
   python3 scripts/rotate_master_key.py --db-url <URL> \
     --blob-dir /opt/gangdian/data/blobs --backup-dir /opt/gangdian/backup
   ```
   中断后原命令重跑即续迁。
4. 验证:输出 `rewrapped` 计数;审计出现 `master_key_rotated`;
   随机抽 3 对象解密验证。
5. 收尾:全实例 `.env` 换新钥双钥并存滚动重启 → 观察 24h →
   移除 OLD_MASTER_KEY_* 再滚动一次。
## 回退
- 观察期内任一异常:恢复旧钥为当前钥(反向再跑一次轮换)。
