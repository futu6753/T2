# Runbook:HMAC 盲索引密钥轮换

> 平台设计:盲索引(如手机号 `phone_index`)以**当前主密钥**为 HMAC 键、
> 算法随套件(HMAC-SHA256 / HMAC-SM3)。因此索引钥不独立存在——
> 它随主密钥轮换与套件迁移自动重算,无单独轮换动作。

## 何时重算
- 主密钥轮换(runbook_master_key_rotation):迁移脚本对 `phone_ct`
  重包时同步重算 `phone_index`(新钥+原套件算法)。
- 套件迁移(runbook_suite_migration):同步重算为目标套件算法
  (前缀 `HMAC-SM3$` / `HMAC-SHA256$` 自描述)。

## 验证
- 抽查:登录后按手机号检索用户命中;
- 库内 `phone_index` 前缀与当前套件一致;
- 迁移报告 phases 中 `db:idp_users` migrated 计数与含手机号用户数一致。

## 事故口径
- 若索引批量失配(检索不命中):按套件迁移 Runbook 用当前目标重跑
  `migrate_crypto_suite.py`(幂等,只补写不达标对象)。
