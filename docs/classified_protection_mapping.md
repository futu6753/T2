# 等保三级要求 → 平台实现对照表(摘要)

> 内部资料;测评前以现行标准条款核对补全。
> Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)

| 等保控制点 | 平台实现 | 证据位置 |
| ---- | ---- | ---- |
| 身份鉴别:双因素 | 口令+TOTP/短信;管理员强制 2FA 可配 | apps/idp/auth*;tests/test_idp_login |
| 身份鉴别:口令复杂度与有效期 | 长度/字符类/最长使用期,生产按下限钳制 | gd_policy/profile.py `_clamp_prod` |
| 登录失败处理 | 失败计数锁定,到期自动解锁(Redis TTL) | apps/idp/accounts.record_failure |
| 访问控制:最小授权 | 分组授权 + RP 侧 RBAC;末位管理员守护 | apps/*/;tests G/H 组 |
| 安全审计:不可篡改 | 只增不改触发器 + 逐条链式哈希 + 定期校验 | gd_storage/audit.py;audit 触发器 |
| 安全审计:覆盖面 | 事件字典 ≥20 类统一登记 | gd_storage/events.py |
| 剩余信息保护 | 删除连带销毁密文 blob 与派生物 | certvault store.destroy_blob |
| 数据保密性:存储 | 信封加密(对象独立 DEK+主密钥包裹) | gd_crypto/envelope.py |
| 数据保密性:传输 | TLS 终结于 nginx;Cookie Secure/HttpOnly | deploy/nginx.conf;rp_common |
| 密码技术合规 | 国密可选套件 SM3/SM4/SM2(自描述共存) | gd_crypto/gm/;F 组测试 |
| 数据备份恢复 | 每日备份+保留 14 天+季度恢复演练 | deploy/backup_cron.sh;手册 §7 |
| 时钟同步 | NTP 强制(加固脚本第 5 步) | deploy/harden.sh |
| 恶意代码防范(应用层) | AI 助手动作白名单/dry-run/原子回滚 | 13-R-F3D-2;B7 基线 50/50 拒绝 |
| 集中管控 | 统一身份/统一审计/healthz 一致性冒烟 | scripts/smoke.py |
