# 问题解决指南(运维口径 · 常见故障 → 处置)

> 与 H06 事故台账编号挂钩;新事故按模板追加。
> Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)

| 编号 | 现象 | 根因 | 处置 |
| ---- | ---- | ---- | ---- |
| E2 | 登录页报"登录超时"死循环 | 上下文令牌过期未自动续签 | 平台已内置自动续签回登录页;若复现检查负载均衡是否缓存旧实例 |
| E10 | 启动报 `MasterKeyMismatchError` | 直接更换了 MASTER_KEY_HEX | 恢复旧钥,按 `docs/runbook_master_key_rotation.md` 走迁移式轮换 |
| E13 | 多实例行为不一致/会话时有时无 | 实例间环境变量漂移(套件/模式/密钥) | `scripts/smoke.py` 一致性探测;对齐 `.env` 后滚动重启 |
| E16 | SSO 登录后频繁被踢 | 时钟偏移导致 token_valid_after 误判 | 校 NTP;确认所有实例对时同源 |
| D5 | 服务 502 | MASTER_KEY_HEX 非 64 位 hex/带引号 | 按 `deploy/env.example` 格式修正(值不加引号) |
| 新 | TOTP 全员失败 | 服务器时间漂移 | `timedatectl` 校时;偏移超 30s 即触发 |
| 新 | gm 切换后旧数据读不出 | 未按对象自描述解密(自研改动破坏) | 平台默认按信封 alg 选套件;检查是否有本地补丁绕过 `gd_crypto` |
| 新 | 迁移中断 | 断电/误杀进程 | 原命令重跑即断点续迁(状态文件在 backup/),结果与一次性迁移一致 |
| 新 | Redis 故障登录报"暂不可用" | fail-closed 设计,防锁定计数丢失 | 恢复 Redis 即自愈;禁止改为本地降级(H12 ai_directives) |

## 排查工具
- `python3 scripts/smoke.py`:healthz 全量探测 + 套件/模式一致性。
- `python3 selfcheck/selfcheck_prod.py`:生产前置全项自检。
- 审计链校验:任何"数据被改"疑虑先跑 `verify_chain`(gd_storage.audit)。
