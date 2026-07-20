# 港电实验室统一平台(重建版)

> 依据 harness v2.0(H00–H13)从零重建。密级:公司内部资料,不得外发(H00)。
> 本仓库当前处于 **里程碑 8 已交付**(adapter 云云对接多平台适配器:映射 DSL 契约迁移/南向四厂商/死信重放/接入成本基准)状态,详见下方里程碑计划。

## 一、当前进度

- 里程碑 1(共享库与平台底座)✅
- 里程碑 2(IdP 统一认证中心 + sso_client)✅:OIDC 五端点、口令/TOTP/短信登录、
  管理台五区(CSRF+末位 admin 守护)、DEMO⇄生产切换恢复清单、`scripts/run_idp.py`
  启动入口;65 项回归 + CI 门禁全绿。
- 里程碑 3(RP 生态接入)✅:`apps/rp_common` 共享层(SSO 自动建号/五路由工厂/
  逐请求回库校验)、certvault JWT exchange 特例(iat 吊销/踢下线)、nvr 三角色
  RBAC、quiz 双身份(游客 5 位 ID)、factory-3d 鉴权矩阵、多实例参考拓扑
  (`deploy/`)与 G 组跨实例语义测试;80 项回归 + CI 门禁全绿。
- 里程碑 4(certvault 全功能)✅:bw 频域盲水印引擎完整落地(Haar DWT→
  8×8 DCT→SVD σ1 QIM,96bit=48ID+CRC16+RS,JPEG q60 实测零误码、
  PSNR 45dB)、固定顺序流水线(微扭曲→明水印30°平铺+团花+智能锚定→暗码)、
  证件库信封加密、31 条路由(本地登录锁定文案契约/2FA/发证全参数/溯源
  盲提顺序契约+bw 回配+故障隔离/备案撤销 R-CV-5/笔记越权 403/管理区)、
  R-CV-2 推荐器、R-CV-3 组合投票置信、benchmarks 两评测工件
  (R-CV-1/R-CV-4);stega/tm/aliyun 引擎骨架随模型导入激活(GAP-13)。
  102 项回归 + CI 门禁全绿。
- 里程碑 5(nvr-monitor 全功能)✅:检测判定树五分支契约(探针可注入)、
  设备台账密码信封+状态机+统一时间线、13-R-NVR-1 去抖策略族五模式
  (consecutive/duration/EWMA/滞回/自适应)+回放 Pareto 工件、告警生命周期
  (每设备每 scope 一条活动/恢复即解带时长/通道并存/unknown 不误触发)、
  渠道派发(Webhook 与阿里云 RPC 签名逐字锁定/线性退避持久化续跑)、
  巡检互斥与单台隔离、推送设备(HTTP+TCP 三行格式/心跳去重/首联宽限/
  恢复当场解除)、13-R-NVR-3 周报事实层(Claude 直连锁定+锚点校验降级)、
  Prometheus 0.0.4 手写、对外 /public/v1 HMAC 五行待签串;ISAPI 真探针随
  目标环境挂接(GAP-14)。128 项回归 + CI 门禁全绿。
- 里程碑 6(factory-3d 三维物联大屏)✅:布局模型(默认模板 1 场区/
  4 栋/23 台 + 结构操作全集 + 重叠校验回弹)、告警状态机(抖动 silent/
  再掉线新告警/删除清态/历史有界)、13-R-F3D-1 降级阶梯(滞回分档+
  自动回升,fps 回报端到端闭环)、13-R-F3D-2 助手事务化(23 动作注册表/
  dry-run→确认→原子回滚/B7 50 条恶意指令零误执行)、13-R-F3D-4 规模基准
  (22 台帧 3.3KB<5KB 预算,至 200 台线性)、WS 实时通道、外部 HMAC 注入、
  `/` 内容协商数据壳大屏(Three.js 场景随里程碑 9,GAP-16)。
  187 项回归 + CI 门禁全绿。
- 里程碑 7(安全刷题系统)✅:题库 233 题五题型(seed 幂等/84 配图/
  四底色)、背题/做题双模式与四类判分、错题本与进度按账号持久化、
  13-R-QZ-1 SM-2 变体分层排期+今日复习队列、13-R-QZ-2 整数 ELO 画像
  +邻域采样(默认关)、13-R-QZ-3 一次性迁移码无损迁移(散列/TTL/
  用后作废/零个人信息)、13-B10 模拟用户学习效果基准(SRS +4.1pp)。
  198 项回归 + CI 门禁全绿。
- 里程碑 8(adapter 云云对接多平台适配器)✅:六边形重建(core 纯
  stdlib/api FastAPI 薄壳,14 路由显式 operation_id);13-R-AD-1 映射
  DSL(yamlite 受限声明:字段路径/单位换算/枚举/模板,reply 语义并入
  厂商声明,五份映射升格契约工件入 export+diff 防漂移流水线);南向
  织光/星逻/司运验签与采集、司空2 planned→501;两阶段命令 reply
  (400 条件必填/幂等 409/ack 超时 504/礼貌轮询 succeeded);Forwarder
  出站签名+指数退避+有界死信,13-R-AD-3 导出/重放下游恰一次;
  13-B9 接入成本基准(曜阳第五厂商:DSL 48 行声明+0 行代码 vs 硬编码
  18 行代码,200/200 逐样等价);单文件运维控制台前端路径⊆后端路由
  静态锁定。235 项回归 + CI 门禁全绿。
- 完工审计(2026-07-19)✅:路由树递归盘点对照 L02/L04 清单,certvault
  零缺口;nvr 补齐设置区 C3 全语义/渠道就绪度/日志 UNION 区/cron 解析器/
  两 CLI 工件;深度回归 17 项新增(缩放溯源/多备案/二压/去抖三模式端到端/
  互斥/审计防篡改双保险)。150 项回归 + CI 门禁全绿。

### 里程碑 1 交付物(共享库与平台底座)

按 H01/H05 的 `<ai_directives>`,在生成任何子系统骨架前先行交付四个共享库接口与
自检 DSL。本里程碑包含:

| 交付物 | 位置 | 对应规约 |
|---|---|---|
| 密码学统一抽象(intl 套件全量实现,gm 预留) | `packages/gd_crypto/` | H04 §八 / H01 ARC-8 |
| 密文信封(自描述 alg/kid,GCM 完整性) | `gd_crypto/envelope.py` | H12 §三 |
| 口令自描述哈希 + 登录透明重哈希 | `gd_crypto/password.py` | H04 §8.2.5 |
| 无状态登录上下文令牌(重启存活/过期续签) | `gd_crypto/context_token.py` | H02-A3 / H06-E2 |
| 主密钥环与 E10 指引 | `gd_crypto/keyring.py` | H06-E10 / P0-1 |
| 统一设置服务(四层优先级/热生效/审计) | `packages/gd_policy/` | H03 / H02-C3 |
| SecurityProfile(唯一模式入口,等保钳制) | `gd_policy/profile.py` | H05 §1 / H00 G3 |
| 自检 DSL(D1–D9 单一事实来源)+ fail-closed 自检 | `selfcheck/` + `scripts/selfcheck_prod.py` | H05 §4 / 13-R-IDP-3 |
| Database 双方言抽象 + 迁移基线 | `packages/gd_storage/` | H12 §一/§六 |
| 统一审计链(只增不改触发器/逐条 alg/串行写入) | `gd_storage/audit.py` | H12 §四 / H04 §三 |
| 共享易失态存储接口(Redis fail-closed + 本地开发实现) | `gd_storage/volatile.py` | H12 §五 / H06-E13 |
| RP 接入库(SsoClient 实现,里程碑 2 已交付) | `packages/gd_sso_client/` | H08 §3 |
| 敏感信息扫描 / DEMO_MODE 单一入口静态检查 | `scripts/` | H06-P0-1 / H05 |
| 回归测试 35 项(锚点命名) | `tests/` | H09 §二 D/E/K |

## 二、快速开始(离线环境)

```bash
# 依赖:Python 3.11+;离线 wheel 安装(内网镜像见部署文档)
pip install --no-index --find-links=<wheels目录> fastapi uvicorn pydantic \
    cryptography argon2-cffi pyyaml redis psycopg python-dotenv

# 全量回归 + 工程门禁(CI 同一入口)
bash ci_gate.sh

# 生成主密钥(只显示一次,注入环境变量,禁止入库)
python3 scripts/gen_master_key.py

# 等保态自检(fail-closed:任何一项失败即非零退出)
MASTER_KEY_HEX=<64位hex> python3 scripts/selfcheck_prod.py --db sqlite:///data/platform.db
```

## 三、里程碑计划(每个里程碑附 H09 对应验收测试)

| # | 内容 | 主要验收组 | 状态 |
|---|---|---|---|
| 1 | 共享库(crypto/policy/storage/sso_client 接口)+ 自检 DSL + 审计链 | A.4/A.5、B(profile 级)、D、E、J、K(R-IDP-3) | ✅ 本次交付 |
| 2 | IdP 统一认证中心:OIDC 五端点、五种登录方式、/admin 五区、/portal、DEMO⇄生产切换闭环、sso_client 实现 | B(http 级)、C、H、F.1 | ✅ 已交付 |
| 3 | RP 生态接入:certvault(JWT exchange 特例)+ nvr + quiz + factory-3d 统一登录;多实例参考拓扑 | C、G | ✅ 已交付 |
| 4 | certvault 全功能(四模块/三引擎/组合双保险)+ R-CV-1..5 + B1 基线 | K(CV 组) | ✅ 已交付 |
| 5 | nvr-monitor(巡检/去抖族/周报事实层)+ R-NVR-1/3/4 + B5 | K(NVR 组) | ✅ 已交付 |
| 6 | factory-3d(大屏/AI 助手事务化)+ R-F3D-1/2/4 + B7/B8 | K(F3D 组) | ✅ 已交付 |
| 7 | quiz(SM-2/ELO/迁移码)+ R-QZ-1/2/3 + B10 | K(QZ 组) | ✅ 已交付 |
| 8 | adapter(契约迁移/映射 DSL)+ R-AD-1/3/4 + B9 | E(diff 零漂移)、K(AD 组) | ✅ 已交付 |
| 9 | 前端三形态(F1/F2/F3)+ ui-kit + Playwright E2E | I | 待启动 |
| 10 | 套件迁移脚本(双写/断点,R-IDP-2)、gm 冒烟、三包流水线、部署包 | F、交付物清单 | 待启动 |

## 四、工程红线速查

- 业务代码禁直接判 `DEMO_MODE`(唯一入口 `SecurityProfile`,CI 静态检查);
- 业务代码禁直呼算法库(唯一入口 `gd_crypto`,评审否决项);
- 审计写入路径全平台唯一(`gd_storage.audit`),各系统不得自写;
- Redis fail-closed 不得弱化;仓库只允许 `.env.example` 占位模板;
- 未完成项一律 `TODO(GAP-xx):` 入 `docs/GAP_LEDGER.md` 台账。
