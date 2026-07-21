# 港电统一平台 · 单机六系统部署手册(国内网络)

> 适用:单台 Linux 服务器(如 10.158.6.195,PVE 上的 UOS),国内网络在线
> 构建,自签证书,六系统单实例无冗余。冗余/多机拓扑见
> `docker-compose.reference.yml` 与 `deployment_manual.md`。
> Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)

## 0. 前置

- 已装 Docker 24+ 与 compose 插件(`docker compose version` 可用)。
- 服务器能出公网(国内),已配 Docker 镜像加速与国内 pip 源。
- 内存建议 ≥4G(六系统 + PG + Redis + nginx 同机)。

### 0.1 Docker 国内镜像加速(必做)

`/etc/docker/daemon.json`(改后 `systemctl restart docker`):

```json
{
  "registry-mirrors": [
    "https://docker.mirrors.ustc.edu.cn",
    "https://hub-mirror.c.163.com"
  ]
}
```

> compose 内的 postgres/redis/nginx 镜像已写成阿里云公开源
> (`registry.cn-hangzhou.aliyuncs.com/library/*`);若你有内网 registry,
> 把这些前缀整体替换即可。Dockerfile 的 pip/apt 已默认走阿里云源,
> 可用 `--build-arg PIP_INDEX=... APT_MIRROR=...` 覆盖为内网制品库。

## 1. 一键部署(推荐)

```bash
git clone <仓库地址> gangdian && cd gangdian
sudo bash deploy/bootstrap.sh
```

`bootstrap.sh` 会自动完成:生成 `.env`(随机 PG 口令 + 主密钥)→ 自签
证书 → 阶段一起底座+IdP → 在 IdP 内登记四 RP 客户端并回填 SSO 密钥 →
阶段二起全部六系统+nginx → 容器内 healthz 冒烟。幂等,可重复执行。

> 两阶段的原因:四个 RP 启动前必须先在 IdP 里登记拿到 client_secret
> (`?:` 缺密钥即启动失败)。脚本用 IdP 容器内的
> `register_sso_clients.py` 生成密钥并回填 `.env`,解决这个依赖顺序。

完成后:

```bash
# 初始化管理员(首登强制改密)
docker compose -f deploy/docker-compose.single.yml exec idp \
  python3 scripts/create_admin.py --account op_admin
```

## 2. 手工部署(理解每步时用)

```bash
cp deploy/env.example .env && chmod 600 .env
# 填 PG_PASSWORD;生成主密钥:
python3 scripts/gen_master_key.py     # 把 MASTER_KEY_HEX 填进 .env
bash deploy/gen_selfsigned_certs.sh   # 证书 → deploy/certs/

DC="docker compose -f deploy/docker-compose.single.yml"
$DC up -d --build postgres redis idp  # 阶段一
# 登记四 RP,把输出的 *_SSO_SECRET 行写入 .env:
$DC exec idp python3 scripts/register_sso_clients.py --base-domain gangdian.internal
$DC up -d --build                     # 阶段二:全量
```

## 3. 访问与证书信任

六域名需解析到服务器 IP。内网无 DNS 时,在**访问者电脑**的 hosts 加:

```
10.158.6.195  sso.gangdian.internal cv.gangdian.internal nvr.gangdian.internal
10.158.6.195  f3d.gangdian.internal quiz.gangdian.internal adapter.gangdian.internal
```

自签证书告警消除:把 `deploy/certs/gangdian-ca.crt` 导入访问者系统/
浏览器的"受信任的根证书颁发机构"。命令行冒烟用 `-k` 跳过校验:

```bash
curl -k https://sso.gangdian.internal/healthz
```

## 4. 换正式域名

若不用 `gangdian.internal`:改 `.env` 的 `SSO_ISSUER` 与四个 `*_REDIRECT`
→ 改 `nginx.single.conf` 的六个 `server_name` → 改
`gen_selfsigned_certs.sh` 的 `BASE_DOMAIN` 重签证书 → 用新 `--base-domain`
重登记 SSO 客户端(或管理台删旧客户端后重登记)。

## 5. 排障

| 现象 | 处置 |
| ---- | ---- |
| RP 容器启动即退出,日志报缺 `*_SSO_SECRET` | 未回填密钥;跑 register 脚本并写入 `.env` 后 `up -d` |
| 拉镜像超时 | 未配镜像加速(见 0.1);或改内网 registry 前缀 |
| pip 构建慢/超时 | Dockerfile 已默认阿里云源;仍慢可 `--build-arg PIP_INDEX=<内网源>` |
| 大屏 `#scene` 空白 | 确认 `apps/factory3d/web/vendor/three.module.min.js` 在(随镜像);否则先 `bash deploy/fetch_libs.sh` |
| 浏览器证书告警 | 导入 `gangdian-ca.crt` 到受信任根 |
| healthz 报 crypto_suite 不一致 | 全实例 `.env` 的 `CRYPTO_SUITE` 未对齐;对齐后 `up -d` |
| TOTP/短信码全失败 | 服务器时钟漂移;校 NTP(`timedatectl set-ntp true`) |

## 6. 运维常用

```bash
DC="docker compose -f deploy/docker-compose.single.yml"
$DC ps                    # 状态
$DC logs -f idp           # 跟日志
$DC exec idp python3 scripts/smoke.py --idp http://127.0.0.1:9000  # 容器内冒烟
$DC down                  # 停(保留数据卷)
$DC down -v               # 停并删数据卷(慎用:清库)
```

备份、密钥/套件轮换、升级见:`deployment_manual.md`、
`runbook_master_key_rotation.md`、`runbook_suite_migration.md`、
`upgrade_notes_template.md`。
