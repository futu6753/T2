# 港电统一平台部署手册(V2.0 · 里程碑 10)

> 内部资料。适用:内网 Linux x86_64 单机或双实例参考拓扑。
> Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)

## 1. 前置条件
- Docker 24+ 与 docker compose;离线环境先在有网机器执行
  `deploy/fetch_libs.sh` 下载前端库与 wheels。
- 内网 NTP 源可达(TOTP 与审计时间线强依赖对时,偏移 >30s 会导致
  动态码验证失败;`deploy/harden.sh` 第 5 步启用 systemd-timesyncd,
  无 systemd 环境请配置 chrony)。
- 主机目录约定:`/opt/gangdian/{data,backup,.env}`。

## 2. 安装步骤
1. 解压交付包到 `/opt/gangdian`,复制 `deploy/env.example` 为 `.env`
   并按注释填写(`chmod 600 .env`)。
2. 生成主密钥:`python3 scripts/gen_master_key.py`,填入 `.env` 的
   `MASTER_KEY_HEX`(保管要求见输出提示)。
3. 执行加固:`sudo bash deploy/harden.sh`(幂等可重跑)。
4. 启动:`docker compose -f deploy/docker-compose.reference.yml up -d`。
5. 初始化管理员:`python3 scripts/create_admin.py --account op_admin`。
6. 冒烟:`python3 scripts/smoke.py --idp https://sso.内网域名 \
   --rp cv=https://cv.内网域名`(退出码 0 = 全绿且套件/模式一致)。

## 3. 密码套件
- 所有 profile 统一预置 `CRYPTO_SUITE=intl`(默认即国际套件);
  切换 gm 必须全实例一致,并先阅读 `docs/runbook_suite_migration.md`。
- gm 生效时服务启动日志出现大写提示,审计出现 `crypto_suite_changed`。

## 4. 备份
- `deploy/backup_cron.sh` 挂入 crontab(样例见脚本头注释):
  PG 全量 + blob 密文目录,保留 14 天,权限 600。
- 主密钥与 `.env` 不入自动备份,单独离线保管(H04 §九)。

## 5. 升级
- 按 `docs/upgrade_notes_template.md` 出具当次升级说明后执行;
  数据库迁移随服务启动自动应用(expand 型,支持回退观察期)。

## 6. 生产自检
- `python3 selfcheck/selfcheck_prod.py` 输出模式/套件/密钥/时钟等
  检查报告;任何 FAIL 项禁止对外开放服务。

## 7. 恢复演练(每季度)
1. 取最近一份备份,在隔离环境恢复 PG 与 blob 目录;
2. 注入同一主密钥,启动后随机抽 3 份证件执行溯源验证;
3. `verify_chain` 审计链校验必须全绿;演练记录归档 backup/drills/。
