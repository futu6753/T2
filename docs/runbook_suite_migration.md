# Runbook:密码套件迁移(intl⇄gm,13-R-IDP-2)

> 触发:合规要求启用国密。全实例必须同套件;迁移期允许双写窗口过渡。

## 步骤
1. 演练:在恢复演练环境先完整走一遍本 Runbook。
2. 开双写窗口:全实例 `.env` 增 `CRYPTO_DUAL_WRITE=gm` 滚动重启
   (新写入两套件并存,旧套件故障可自动回退)。
3. 存量迁移(逐库,低峰):
   ```
   python3 scripts/migrate_crypto_suite.py --db-url <URL> --target gm \
     --blob-dir /opt/gangdian/data/blobs --backup-dir /opt/gangdian/backup
   ```
   SQLite 自动备份;PG 先 pg_dump 并携 `--i-have-backup`。中断重跑即续迁。
4. 切换:全实例 `CRYPTO_SUITE=gm` 且移除 `CRYPTO_DUAL_WRITE`,滚动重启;
   启动日志出现大写 GM 提示,审计出现 `crypto_suite_changed`。
5. 验证:`scripts/smoke.py` 套件一致性;登录/发证/溯源抽测;
   审计含 started/progress/completed 三锚点;`verify_chain` 全绿。
6. 口令:无需批量处理——用户登录时透明重哈希为 PBKDF2-SM3
   (报告中的 pending_rehash 随登录自然收敛)。
## 回退
- 切换后观察期异常:`CRYPTO_SUITE` 改回 intl 滚动重启(存量 gm 对象
  按自描述仍可解);如需彻底回迁,`--target intl` 再跑迁移。
