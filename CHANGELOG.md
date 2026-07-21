# CHANGELOG

> 默认值/语义变更必须写入「行为变化」章节(H06-E6)。

## v0.1.0(2026-07-18)· 里程碑 1

### 新增
- 共享库四件套接口全部落定:`gd_crypto`(intl 套件全量实现)、`gd_policy`
  (设置服务 + SecurityProfile)、`gd_storage`(双方言 Database/审计链/迁移/
  易失态存储)、`gd_sso_client`(接口定义);
- 自检 DSL(D1–D9)与 `selfcheck_prod` fail-closed 自检;
- 敏感信息扫描(P0-1)与 DEMO_MODE 单一入口静态检查进 CI;
- 回归测试 35 项(锚点命名:E2/E9/E10/A.4/A.5/J/K-R-IDP-3 等)。

### 行为变化(相对遗留系统)
- `DEMO_MODE` 默认值由遗留的 1 反转为 **0=生产**(不配置即安全,H05 §1.1);
- 口令最小长度默认值由 certvault 遗留的 8 统一提升为 **10**(H03 §2);
- 生产模式对低于等保下限的显式配置执行**强制钳制**并告警(H00 G3)。

## 里程碑 2(2026-07-18)· IdP 统一认证中心与 sso_client 实现

### 新增
- `apps/idp/`:OIDC 授权码+PKCE S256 协议引擎(五端点:发现/authorize/token/
  userinfo/jwks + logout back-channel 扇出);授权码一次性防重放、应用访问控制
  双点拒绝(02-A1/A6)。
- 登录方式:口令+TOTP 两步(RFC 6238 标准库实现,两步失败同计次)、短信验证码
  (5 分钟有效、散列入易失态、一次性)、D2 测试码/D3 回显/D4 证书入口/D5 微信
  模拟均仅经 SecurityProfile 判定(H05 清单制)。
- 账户体系:统一用户目录迁移 v2(idp_users/groups/clients/links/consents,
  手机号信封加密+HMAC 索引)、锁定计数入 Redis 键 TTL 到期自动解锁、
  登录成功透明重哈希、首登强改密、演示账号种子/停用。
- 会话:全易失态、空闲+绝对双超时、按用户/按 demo 标记吊销(H05 §3.2.4)。
- 模式切换:`ModeService` 落 DEMO→生产自动恢复清单(顺序幂等、fail-closed
  自检含 http 级 D4/D5 断言)与生产→DEMO 二次确认+原因审计;演示派生主密钥
  进生产被阻止(H05 §3.2.5)。
- 管理台 /admin(02-A4):用户建号/停用(末位 admin 守护+即刻断线)/解锁、
  应用注册(一次性明文密钥)、审计查看与哈希链一键校验、模式页;
  全部 POST 强制 CSRF 令牌。
- `gd_sso_client`:SsoClient 实现(GAP-05 解除)——state 一次性+10 分钟、
  nonce 防重放、PKCE、next 仅站内相对路径、back-channel 全量吊销、
  传输层可注入;`jwt_verify` 按 JWKS kid RS256 验签(±60s 偏移)。
- 自检:HTTP_CHECKS 注册 + 进程内 ASGI 客户端(`selfcheck/asgi.py`,
  离线环境无 httpx 的一键执行入口,06-E17),GAP-02 解除;
  `selfcheck_prod` 与模式切换共用同源 DSL 执行 http 断言。
- 脚本:`run_idp.py`(绑定检查/生产开机自检 fail-closed/DEMO 每小时审计
  心跳,GAP-07 部分解除)、`create_admin.py`(一次性随机口令+首登强改,H03 §7)。

### 变更
- `SecurityProfile` 增补五种登录方式开关字段(设置驱动热生效,H03 §4)。
- `Database`:sqlite 跨线程许可 + 连接级互斥锁(Web 线程池防御);
  `MasterKeyRing.current_key` 便捷属性。
- 演示种子口令改为满足三类字符复杂度(建号统一校验不豁免演示账号)。

### 测试(65 项全绿)
- `test_idp_oidc.py`(6):发现/JWKS、全流程、防重放、PKCE 篡改、
  access_denied、重启 kid 稳定+进行中登录存活(C08 等价)。
- `test_idp_login.py`(6):锁定两步同计次+管理员解锁、TOTP 真码、
  D2/D3 成对断言、口令策略、首登强改。
- `test_b_mode_switch.py`(4):B.2 完整恢复清单、B.3 环境检查、
  env 锁定拒热切、演示密钥阻止。
- `test_c_sso_client.py`(7):RP 端到端、state 一次性、nonce/伪签名拒绝、
  back-channel 下线、开放重定向防护、未配 env 不启用(06-E13 语义)。
- `test_h_admin.py`(6):鉴权矩阵、CSRF、末位 admin 守护、停用断线、
  解锁审计+链校验、应用密钥一次性。

## 里程碑 3(2026-07-18)· RP 生态接入与多实例拓扑

### 新增
- `apps/rp_common/`:RP 共享装配层——`RpAccountService`(SSO 按 sub 自动建号:
  最小角色、无口令旁路、显示名冲突后缀、每次登录刷新口令时间戳防 90 天误伤
  =06-E16;四级角色字典;停用对 SSO 同样生效)与 `build_sso_router` 五路由工厂
  (/sso/status|login|callback|logout + POST /backchannel-logout,H08 §3)、
  `require_session` 逐请求回库校验(H03 §3)、`require_role` 角色闸门。
- `apps/certvault/`:JWT 体系接入特例(H08 §3)——POST /auth/sso/exchange
  用 SSO Cookie 换发本系统 HS256 JWT(密钥由平台主密钥派生,60 分钟),
  Bearer 鉴权逐请求回库 + iat 吊销水位;管理员踢下线 = 刷新 token_valid_after
  (H03 §6)。
- `apps/nvr/`:三角色 RBAC 骨架(admin/operator/auditor,SSO 默认 auditor)。
- `apps/quiz/`:双身份骨架——SSO 统一登录 + 5 位数字游客 ID
  (quiz_guest_mode 独立开关,H03 §6)。
- `apps/factory3d/`:鉴权矩阵骨架——大屏公开、/admin* 统一登录、
  ADMIN_TOKEN 降级脚本/应急通道(02-D1)。
- 迁移 v3:cv_users/nvr_users/f3d_users/quiz_users/quiz_guests
  (sso_sub 唯一映射、certvault 专属 token_valid_after)。
- `deploy/docker-compose.reference.yml` + `deploy/nginx.conf`:
  2×IdP + 2×RP + Redis + PG 无 sticky 参考拓扑(网关登录限速 6r/m、
  HSTS、client_max_body_size 对齐,06-E11/E13)。
- `scripts/unlock_user.py`:管理员自锁自救官方 CLI(仅清锁定不改口令,
  06-E5 / H03 §7)。
- IdpContext.maybe_refresh + 每请求中间件:设置版本轮询,策略热更新
  跨实例 ≤ 下一请求生效(09 §二 G.4)。

### 变更
- `gd_sso_client`:键空间参数化 gd:{system}:sess:{sid}(H12 §五);
  SsoConfig 补 cookie_name/cookie_secure/post_logout/default_role;
  新增 revoke_session/post_logout_url/只读 config。

### 测试(80 项全绿)
- `test_c_rp_ecosystem.py`(10):四系统统一登录+免登跳转、首建号最小角色/
  无口令旁路/固定映射、E16 口令时间戳、显示名后缀、停用拦截+会话回滚、
  back-channel 四系统扇出、certvault exchange 与 iat 吊销踢下线、
  quiz 游客开关、factory-3d 鉴权矩阵。
- `test_g_multi_instance.py`(5):登录上下文跨实例完成/会话跨实例存活、
  锁定计数跨实例累加、审计链交替写入无分叉、策略热更新全实例传播、
  RP 双实例会话共享与跨实例扇出吊销。

## 里程碑 4(2026-07-19)· CertVault 全功能

### 新增
- `apps/certvault/wm/`:水印引擎包——payload(96bit=48bit ID+CRC16+RS nsym=4,
  reedsolo 纠错)、bw(Haar DWT LL→8×8 DCT→SVD σ1 QIM 步长 56,循环重复+
  投票,JPEG q60 实测零误码/PSNR 45dB/缩放85%+二压存活)、visible(微扭曲
  正弦位移场 seed 复现/明水印 30° 平铺+团花网纹/智能锚定文字行形态学梯度)、
  engines(注册表+可用性探测 06-E7:stega/tm 模型目录探测未装即「模型未安装」
  人话、aliyun AK 探测;组合双保险成员校验;/issue 选不可用引擎→400=契约内
  合法状态)、pipeline(顺序固定:微扭曲→明水印→暗码 bw 先嵌→LANCZOS 导出)。
- `apps/certvault/store.py`:证件库(信封加密 blob 落盘+SHA-256、三重上传
  校验、240 宽缩略图、内存解密直出、删除连带销毁=剩余信息保护)。
- `apps/certvault/records.py`:备案台账(全量参数快照+成品密文存档、独立
  备案候选剔除、13-R-CV-5 撤销+48bit ID 空间用量、笔记密文、
  13-R-CV-2 engine_feedback 回流)。
- `apps/certvault/trace.py`:溯源(盲提顺序 tm→stega→aliyun 契约、bw 逐条
  回配 embed_w/h+strength 兜底、引擎异常仅记 engine_errors 故障隔离、
  13-R-CV-3 组合命中交叉校验 confidence standard/high/conflict+告警)。
- `apps/certvault/auth_local.py`:本地口令登录(锁定 5 次/15 分钟易失态跨实例
  累加、423/401 文案契约、2FA 失败同计、TOTP 密钥信封加密三件套、改密吊销
  全部旧令牌、90 天到期强改、口令 ≥10 位三类=H03 §2 统一提标)。
- `apps/certvault/recommend.py`:13-R-CV-2 推荐器(介质优先序×反馈命中率
  调序、人话理由、静默回退默认引擎)。
- web 层 31 条路由分区(web/web_certs/web_issue/web_trace/web_admin):
  /issue Form 全参数+自动拼装文案+推荐回显、/trace 命中人话消息+撤销明示
  作废、管理区(建号一次性口令+首登强改、SSO 用户重置=踢下线、停用即刻
  断线、审计链校验+CSV 导出)、/health 含引擎可用性。
- `apps/rp_common/multipart.py`+`forms.py`:自研 multipart 解析
  (离线无 python-multipart,契约保持;往返保真验证)。
- 迁移 v4(cv_certs/cv_records 含 revoked 字段/cv_notes/cv_note_images/
  cv_engine_feedback)、v5(cv_users 2FA 列+首登强改)。
- `benchmarks/erasure_benchmark.py`(13-R-CV-1:TELEA 经典基线擦除后暗码
  100% 存活+残留量化;LaMa Provider 口随 GAP-13)、
  `benchmarks/recapture_matrix.py`(13-R-CV-4:角度×光照×二压矩阵,
  如实呈现 bw 诚实边界=几何可矫/光照非线性破坏 QIM,留 stega 对照基线)。

### 测试(102 项全绿)
- `test_k_certvault.py`(11):载荷回环+纠错、锁定文案契约、改密吊销、
  2FA 回环、上传校验+owner 隔离、删除销毁、发证响应契约、不可用引擎 400、
  溯源三态、独立备案剔除。
- `test_k_certvault_advanced.py`(11):引擎故障隔离、组合投票 high/conflict
  +审计、笔记越权 403、推荐器介质/反馈/回退、管理区全套、/health 引擎上报。

### GAP
- GAP-13:stega/tm 模型权重与 aliyun SDK 目标环境导入后挂接
  (docs/GAP_LEDGER.md)。

## 里程碑 5(2026-07-19)· NVR 监控全功能

### 新增
- `apps/nvr/checker.py`:检测判定树五分支契约(200=online/401·403=
  auth_failed/超时+TCP=timeout/TCP 不通+ping=abnormal/全不通=offline;
  通道离线连带异常双开关默认关;凭据不出任何输出;探针可注入)。
- `apps/nvr/devices.py`:设备台账(密码信封加密 AAD 隔离、任何接口不返回
  密码、PUT password=轮换、推送设备发 token 而 NVR 无该字段)+ 状态机
  (since/consecutive_fails/EWMA 持久化)+ 统一时间线 + 保留期清理
  (时间线不清)。
- `apps/nvr/debounce.py`:13-R-NVR-1 去抖策略族五模式(遗留双模式 +
  EWMA/滞回/自适应,快照驱动无内部态=重启不重置语义天然满足)。
- `apps/nvr/alerts.py`:告警生命周期(部分唯一索引兜底每设备每 scope 一条
  活动、活动期间子状态切换不重复、恢复立即解除带故障总时长、通道 scope
  并存正文点名 ≤5 路、NVR 不可达通道 unknown 不误触发、resolve_now)。
- `apps/nvr/dispatch.py`:渠道派发(pending→sent/failed→abandoned 线性
  退避持久化重启续跑;Webhook 签名 sha256=HMAC(secret,"{ts}."+body) 与
  阿里云 RPC HMAC-SHA1 零 SDK 签名逐字单测锁定;模板变量 20 字截断)。
- `apps/nvr/patrol.py`:巡检(互斥 409/并发上限/单台解密失败隔离=异常+
  原因现于面板/主密钥缺失整轮拒绝/手动检测同驱状态机)。
- `apps/nvr/ingest.py`:通道台账(自动发现/removed 保留历史/0.0.0.0 忽略/
  跃迁入 channel_change)+ 推送接入(变化才落库防心跳灌爆/超阈值同套去抖/
  恢复当场解除/首联宽限/TCP 三行格式每行一答)。
- `apps/nvr/report.py`:13-R-NVR-3 周报(事实层聚合七维随报告落库、
  Claude Messages 直连零 SDK 请求形态锁定、生成后锚点数值校验、
  无 Key/失败/锚点缺失降级确定性模板 generated_by=template+原因)。
- `apps/nvr/exposition.py`:Prometheus 0.0.4 手写零依赖(登录或 Bearer
  常数时间)+ 对外 /public/v1 HMAC(五行待签串/容差 300s/密钥密文落库
  明文仅一次/吊销/失败一律 401「鉴权失败」记 public_auth_failed)。
- web 层三模块(web/web_status/web_ops):设备 CRUD 与巡检、状态总览
  by_kind 分桶、明细/时间线/变化流、告警/通知/通道查询、报告三路由、
  /metrics、对外三路由、/ingest 双别名;三角色 RBAC。
- 迁移 v6(nvr 九表:设备/明细/状态/时间线/通道/告警部分唯一索引/
  通知队列/报告/APIKey)。
- `benchmarks/debounce_replay.py`(R-NVR-1:四剧本×五模式误报-延迟
  Pareto 表)。

### 测试(128 项全绿)
- `test_k_nvr.py`(14):判定树契约、去抖五模式语义、告警生命周期
  (抑制/恢复时长/通道并存/unknown)、巡检隔离与密钥缺失、推送契约
  (心跳去重/宽限/当场解除)、RBAC 矩阵、密码轮换不回显。
- `test_k_nvr_ops.py`(12):双签名逐字锁定、短信 Code:OK 校验、重试
  退避+重启续跑+abandoned、周报三态+请求形态、/metrics 鉴权与格式、
  对外 HMAC 全负例、保留期、by_kind、回放工件冒烟。

### GAP
- GAP-14:ISAPI 真设备探针目标环境挂接(docs/GAP_LEDGER.md)。

## 完工审计(2026-07-19)· 里程碑 1–5 契约盘点与缺口补齐

### 审计方法
递归遍历 FastAPI 路由树逐条对照 L02/L04 API 清单;certvault 42 条零缺口;
nvr 发现 6 条路由缺口与 3 个工件缺口,全部补齐。

### 补齐
- `/api/settings` GET/PUT + `/api/settings/reset`(02-C3 完整语义:统一策略
  层 schema 驱动、env>后台>文件>默认与来源层展示、values 中 null=删除覆盖、
  未知键/越界/非法 choice 逐键报错、cron 保存时校验、env 锁定拒改并明示、
  reset 清覆盖,全程写审计)。NVR 22 参数并入 gd_policy 统一 schema 四分区。
- `/api/notifications/channels`:渠道就绪度(Webhook/短信 ready+缺项明示,
  不回显密钥)。
- `/api/logs/events`(状态跃迁∪通道跃迁∪告警启停 SQL UNION 三源下推,
  region/station/type/时间窗过滤)与 `/api/logs/stations`。
- `apps/nvr/cron.py`:5 段 UTC cron 解析与 next_run(* , - / 支持、
  日/周双限取或=标准语义、7 归一周日、非法表达式人话拒绝)。
- `scripts/nvr_check_cli.py`(退出码契约/凭据不出输出/--json)与
  `scripts/manage_api_keys.py`(create 明文一次/list/revoke)。

### 深度回归新增(150 项全绿)
- `test_k_nvr_settings.py`(8):C3 三路由全语义、渠道就绪度无泄漏、
  日志 UNION 与过滤、cron 契约、两 CLI 工件回环。
- `test_deep_regression.py`(9):export_width 缩放件溯源回配、三备案并存
  精确匹配无串扰、JPEG q80 二压信道命中、缩略图契约、offline_duration
  端到端+引擎重建(重启)不重置窗口、EWMA 端到端、滞回恢复需连续 2 次
  成功、并发巡检确定性互斥(事件同步)、多系统审计交织 16+ 条链校验
  +防篡改双保险(在线 UPDATE 触发器拒绝/离线篡改链校验失败)。
- 测试基座:深度用例改用 480×720 大图消除小图边际信噪 flake
  (生产图长边 ≤1600 冗余充裕,边界如实记录)。

## 热修(2026-07-19)· E18/E19 IdP 浏览器形态

- E18 登录页人机可操作化:真实表单(account/password/otp,隐藏 rid 转义透传)、
  PRG 人话报错(`?err=cred|locked|ctx`)、`#login-error/#login-notice` 锚点;
  POST /login 浏览器分支 303,JSON 契约逐字不变(17 项 IdP 回归复跑通过)。
- E19 门户内容协商:`/portal` 浏览器得 HTML 卡片页(#portal-title/#portal-apps),
  API 侧 JSON 不变。
- 浏览器 E2E 基座:`tests/e2e/live.py`(uvicorn 线程托管 LiveServer、
  真 HTTP 传输层、IdP+RP LiveStack)+ 6 项 Chromium 用例
  (登录可操作/错口令可恢复/首登强改闭环/门户/SSO 全链 rid 透传/免登跳转)。

## 里程碑 6(2026-07-19)· factory-3d 三维物联监控大屏

### 交付
- 统一策略层新增 F3D 四分区 14 参数(TYPE_FLOAT 新增;告警延时/推送周期/
  滞回阈值等全部热生效);迁移 v7 七张业务表(布局单例/告警/历史/事件/
  模型/事务日志/注入密钥)。
- `apps/factory3d/` 十模块:布局模型(默认模板 1 场区/4 栋/23 台、纯函数
  结构操作全集、重叠/越界校验)、告警状态机(pending→active→acked,
  抖动 silent、再掉线新告警、删除清态、历史有界)、降级阶梯
  (R-F3D-1 滞回分档 关阴影→降贴图→降推送,恢复自动回升)、确定性模拟器
  (toggle 标记手动即脱管)、AI 助手事务化引擎(R-F3D-2:23 动作注册表、
  dry-run 预览→tx_id 确认→原子执行、任一失败整体回滚、危险动作 confirm、
  edit 域会话锁、set_ai 禁改密钥、事务日志有界)、事件流与 WS 通道
  (snapshot→update 全量帧,fps 回报驱动阶梯)、外部注入 HMAC
  (密钥明文一次/信封落库/时间戳容差/吊销)、`/` 数据壳大屏。
- 测试 37 项新增:主验收 18(鉴权矩阵/内容协商/模板/CRUD 与 data_rev/
  重叠回弹/设置语义/会话锁/状态机全周期/事件流/KPI/HMAC 全生命周期/
  助手三步流)+ 研究锚点 6(`test_r_f3d1_degrade`/`test_r_f3d2_tx`/
  `test_r_f3d4_scale` + 滞回恢复/dry-run 隔离/B7 50 条恶意指令零误执行)
  + WS 真连接 2 + 浏览器 E2E 5(XSS 用例经变异验证:移除转义必红)。
- `benchmarks/f3d_scale_benchmark.py`(R-F3D-4):22/50/100/200 台帧大小
  3.3KB/7.1KB/13.9KB/27.6KB 线性,22 台守 <5KB 预算。

### 行为变化(如实记录)
- `GET /` 由纯 JSON 改为内容协商:浏览器(Accept: text/html)得数据壳大屏,
  API 侧 JSON `{"public": true}` 契约不变(M3 前瞻测试原样通过)。
- L03 §3.6 海康直连子形态不在 F3D 内重做,收敛至里程碑 8 适配器统一接入
  (与 nvr 复用同一 ISAPI 通道,GAP-14 同源)。
- 三维场景本体(Three.js)按里程碑计划归属里程碑 9(GAP-16);
  MQTT 桥接/GLB 上传/真实 LLM 通道记 GAP-17。

### 验证
全量 187 项测试通过;`ci_gate.sh` 四步(回归/敏感信息扫描/DEMO_MODE
静态检查/编译)全绿。

## 里程碑 7(2026-07-19)· 安全刷题系统全功能

### 交付
- 迁移 v8 七张 quiz 业务表(题库/进度/SRS/能力/偏好/迁移码);
  `migrations.py` 拆分业务分册 `migrations_apps.py`(v7/v8)守 500 行红线,
  版本序与执行语义不变。
- `apps/quiz/` 五模块:题库(233 题五题型 92/34/31/40/36、84 配图路径、
  四底色分层、确定性生成、seed 幂等、四类判分——风险问答关键词命中)、
  SRS(13-R-QZ-1:SM-2 变体 ease ×100 整数化、1→3→ease 增长、答错回炉、
  题型/底色分层因子、"今日复习"队列到期过滤+逾期靠前+错题权重)、
  ELO(13-R-QZ-2:整数双向更新 K=32/16、期望胜率 ×1000 中间量、
  邻域采样默认关 per-owner 可开)、刷题一条龙(背题/做题双模式,
  判分→进度→错题本→SRS→ELO)、迁移(13-R-QZ-3:一次性迁移码
  SHA-256 散列存储、TTL 15 分钟、用后作废、零个人信息合并——
  仅四张刷题数据表,合并后游客侧清空)。
- `web.py` 扩展 14 条业务路由(骨架路由与 M3 契约原样保留):
  题库分布/双分类列表/单题双模式/作答/错题本/进度/今日复习/偏好/
  出题策略(sequence 与 neighborhood)/迁移发码(仅游客)与兑换(仅 SSO)。
- `benchmarks/quiz_learning_benchmark.py`(13-B10):模拟用户指数遗忘
  +间隔效应模型,SM-2 调度 vs 固定轮转同预算对照——三种子均值
  30 天保持率 0.828 vs 0.787(+4.1pp,复习次数还更少);
  真实培训前后测为 MAY,实施前须过 PIPL 影响评估。
- 测试 11 项新增:主验收 7(seed 幂等与分布/双分类过滤/背题 vs 做题
  答案可见性/四类判分/错题本生命周期与账号隔离/进度与偏好/身份边界
  与 guest_mode 开关)+ 研究锚点 3(`test_r_qz1_srs`/`test_r_qz2_elo`/
  `test_r_qz3_migrate`)+ B10 脚本回环 1。

### 验证
全量 198 项测试通过;`ci_gate.sh` 四步全绿。

## 里程碑 8(2026-07-20)· adapter 云云对接多平台适配器(契约迁移 + 映射 DSL)

### 交付
- `apps/adapter/` 六边形重建:core 纯 stdlib(errors/config/features/model/
  yamlite/dsl/sink/vendors/dispatch/poller/ingest/forwarder/simulator/
  tracing),api 为 FastAPI 薄壳(pydantic 模型名=文档 schema 名,
  14 路由显式 operation_id);.env 与 Settings 一一对应,M17 行内注释
  告警+非 latin-1 请求头人话报错防回潮。
- 13-R-AD-1 声明式翻译 DSL:厂商报文→UnifiedOsd/事件的映射以 yamlite
  受限 YAML 声明(字段路径含列表下标/scale+offset 单位换算/枚举表/
  默认值/点路径模板/NaN 拦截),translate 引擎解释执行;reply 语义
  (ack 超时/轮询间隔/终态判定谓词与查询名)并入厂商声明(原 R-AD-2);
  五份映射(织光/星逻/司运/司空2/曜阳)升格契约工件入
  `scripts/adapter_contract.py` export/diff 防漂移流水线
  (openapi.json + mappings.lock.json,人话定位首个分歧点)。
- 南向四厂商 + 采集外发:织光 HMAC 验签三模式(strict/log/off,覆盖
  原始 body 字节)、星逻 token TTL 复用+批次候选解析、司运 TD-022
  逐字签名、司空2 骨架(feature=planned→501);单线程 Poller 六任务
  门控隔离统计、DedupeCache 轮询/推送同键互斥、CompositeSink 真实
  优先合并+旁路环形缓冲;Forwarder canonical-JSON HMAC 出站签名、
  指数退避、有界死信、导出/重放(DedupeCache 协同下游恰一次,
  13-R-AD-3),运维 CLI `scripts/adapter_deadletter.py`。
- 命令下行三路由:条件必填矩阵→400、幂等指纹(同键同体重放缓存/
  异体 409)、两阶段 reply(ack 超时 504/预算内礼貌轮询 succeeded/
  终态失败或明确拒绝 409/预算耗尽 accepted);星逻 takeoff 成功登记
  批次续跟。R9 统一信封 AdapterResult{code,message,request_id,data}
  全分支 mock 活体覆盖;X-Request-Id 中间件+ContextVar 贯通
  响应头/响应体/JSON 日志。
- `web/console.html` 单文件零外链运维控制台(健康点/自动刷新/设备
  电量条形图/事件过滤/三 Tab 命令表单+幂等键生成/运行时 env-warn
  黄条与轮询任务表/Features 表),前端引用路径 ⊆ 后端路由静态锁定。
- `benchmarks/adapter_onboard_benchmark.py`(13-B9):曜阳储能第五
  厂商标准任务,200 固定种子金样,DSL 条件 48 行映射声明+0 行新代码
  vs 硬编码 18 行新代码,双条件 200/200 逐样等价,输出含环境指纹。
- 联调工具:`scripts/simulate_webhook.py`(服务端同款验签器闭环,
  --bad-sig 验 401,--dry-run 出等效 curl)、`scripts/run_adapter.py`
  (.env→uvicorn factory)。
- 测试 37 项新增:core 20(含六边形纯净度子进程封禁第三方导入)+
  api 14(路由契约表/全链/信封/控制台锁定)+ 固定锚点 3
  (`test_r_ad1_dsl`/`test_r_ad3_replay`/`test_r_ad4_cost`)。

### 验证
全量 235 项测试通过;`ci_gate.sh` 四步全绿;契约 export+diff 零漂移。

## 里程碑 9(2026-07-20)· 前端三形态(F1/F2/F3)+ ui-kit + 浏览器 E2E

### 新增
- **ui-kit 内部包**(`frontend/ui-kit`,H11 §一):CSS 设计变量(港口信号色系/
  系统字体栈守 ARC-5/`[hidden]` E3 兜底)、统一 fetch 封装(CSRF 自动携带、
  X-Request-Id 生成透传、401/403/423/429 四态分层文案含等待时长、AdapterResult
  信封与 pydantic/field_errors 双源字段级错误解析、Bearer/form/urlencoded 载体)、
  ~90 行自研 history 路由(零 react-router 依赖)、DEMO/生产成对横幅、
  模式+密码套件徽标、手机号打码、schema 驱动同构设置页(env 锁定/来源层/
  「恢复默认」=null 删除覆盖语义);纯函数经 node --experimental-strip-types
  直载 TS 源测试(26 组断言)并挂入 unittest。
- **F2 四 SPA**(React 18 + Vite + TS strict,构建产物入 `apps/<app>/web/dist`
  随仓交付,后端 `rp_common/spa.py` 统一托管:/app + history 深链兜底 + CSP +
  越界防护 + 产物缺失 503):
  - quiz 六页:刷题(背题/做题双模式、单题/列表双视图、题型/底色双分类)、
    错题本、进度+掌握度概览(R-QZ-2 邻域采样开关)、今日复习(R-QZ-1)、
    迁移码双侧页(R-QZ-3)、登录(SSO 显隐依 /sso/status + 游客双入口);
  - certvault 六页(JWT 仅存内存,刷新经 /auth/sso/exchange 恢复,401 自动
    exchange 重试):证件库、发证(介质下拉+推荐理由可覆盖 R-CV-2、参数
    面板、可手改拼接文字、「保存为默认」仅界面参数入 localStorage)、备案
    台账(独立备案/撤销与作废标记 R-CV-5/48bit ID 用量)、溯源(置信等级+
    投票明细+双引擎不一致告警样式 R-CV-3、engine_errors 明示 06-E7)、
    管理、账户/2FA;
  - nvr 七页:总览(本体/通道分离展示、「录像机在线」措辞 02-C1)、设备
    详情(检测证据链展开 R-NVR-4、统一时间线、手动检测)、告警、周报
    (降级原因徽标 R-NVR-3)、设置(ui-kit 设置页复用+渠道就绪度)、账户
    (WebAuthn 依 GAP-25 不做占位)、登录;
  - adapter 三页:运行状态(features/providers/M17 告警/最近事件)、死信
    导出→修复→重放(R-AD-3)、接入文档;`/console` 保留为低依赖备用面。
- **F3 三维大屏**(GAP-16 解除):`deploy/fetch_libs.sh` 预取 three@0.160.1
  本地副本(ARC-5 禁 CDN,sha256 入库);`apps/factory3d/web/scene.js` 零
  addons 自研轨道相机(拖拽环绕/滚轮距离/双击回 home)、低多边形程序化
  成景(1 场区/4 栋/23 台)、WS 单连接帧事件转发驱动状态着色与离线脉冲环、
  降级阶梯承接(full 开阴影→no_shadow→low_tex/low_push 降 pixelRatio,
  R-F3D-1 端到端);大屏页转 per-response nonce CSP(禁 unsafe-inline)。
- **F1 红线固化**:IdP 全站 HTML 安全头中间件——/admin* 零 JS CSP
  (default-src 'none',script 全禁),其余页同源 CSP + nosniff + Referrer-Policy。
- **I 组验收固化**:`scripts/check_frontend_e3.py`(E3 双条款,含反例自检)、
  `scripts/scan_frontend_external.py`(承载性外链+黑名单域零命中)进 CI
  第 5 步;`ci_gate.sh` 扩至六步(第 6 步 eslint+prettier,无 node 环境跳过);
  `tests/test_i_frontend.py` 10 项 + `tests/e2e/test_e2e_frontend.py` 浏览器
  4 项(横幅成对 05-D9、登录后 storage 零令牌、401 保留站内 next、CSP 下
  scene canvas 装载 + fps 芯片刷新)。

### 行为变化
- quiz `GET /guest/load/{code}`:M3 占位升级——命中即设游客 Cookie 并返回
  真实进度汇总(此前不落会话导致「输 ID 载入」无效);404 语义不变。
- 各 RP `/healthz`:注入 SecurityProfile 后追加 `mode`/`crypto_suite` 两字段
  (H11 §二横切徽标);未注入时响应与历史逐字节一致,既有断言零影响。
- factory3d `/`(HTML 态):新增 CSP/nonce 响应头与 `/static/*` 静态路由;
  `#scene` 占位文案更新;数据壳既有元素与 `window.F3D_VER` 注入不变。
- IdP HTML 响应新增安全头(见上);登录页 CSP 不设 form-action(会拦截
  表单提交后跨源 302 授权链,Chrome 语义),管理区保留全量严格头。


## 里程碑 10(2026-07-20)· 国密套件真实现 + 迁移体系 + 三包流水线 + 部署包

### 新增
- **GAP-01 解除**:`gd_crypto/gm/` 国密四原语纯 Python 参考实现——
  SM3(GB/T 32905 双向量)、SM4(GB/T 32907 单块向量;100 万次迭代
  向量开发期全量验证)、GCM 模式层(SP 800-38D,与 cryptography
  AES-GCM 随机对拍逐字节一致)、SM2 签验(GB/T 32918.5 标准密钥对
  向量)。`GmSuite` 接替占位:SM4-GCM 信封 / HMAC-SM3 索引 /
  PBKDF2-SM3 口令 / SM2-with-SM3 令牌;JWKS 双钥(RSA+SM2)。
- **13-R-IDP-2 迁移体系**:信封双写窗口(`CRYPTO_DUAL_WRITE`,dual 段
  两套件独立可解、主段失败自动回退);`gd_crypto/migrate.py` 声明式
  迁移核心(8 个 DB 信封列 + 2 类文件信封 + AAD 全映射 + HMAC 索引
  重算,断点续迁状态机,开始/进度/完成审计锚点);
  `scripts/migrate_crypto_suite.py`(SQLite 强制自动备份 / PG 须
  `--i-have-backup`);`scripts/rotate_master_key.py`(H06-E10
  轮换=迁移:算法不变仅重包,`master_key_rotated` 锚点)。
- **F 组验收**:`tests/test_f_crypto_suite.py` 11 用例(原语向量×4 /
  gm 冒烟 / 切换存量兼容+透明重哈希 / `test_r_idp2_migrate` 中断续迁
  与一次性等价 / E10 轮换 / DEMO 正交 / profile 双断言)。
- **基线补齐(B1-B10 全覆盖)**:`benchmarks/common.py` 环境指纹 +
  CSV 归档(benchmarks/data/);B2 认证可用性(登录时延/C08 存活/
  迁移吞吐/双写开销)、B4 模式切换(耗时+自检样例)、B7 助手安全
  (六类 50 条恶意指令 50/50 拒绝、误执行 0)。
- **H10 三包流水线**:`make copyright-pack|paper-pack|patent-pack` →
  `dist/` 六系统×3 包(源码打印稿前 30+后 30 页真实抽取、概况页
  行数实统、手册/设计说明、方法稿+可复现说明+基线数据表挂载、
  技术交底书+权利要求候选,全部带 R-xx 锚点与内部标签)。
- **部署包**:`deploy/Dockerfile`、`harden.sh`(六步加固含 NTP)、
  `backup_cron.sh`(每日备份+保留 14 天)、`env.example`、
  `scripts/run_rp.py` 四 RP 统一生产入口(compose 命令对齐);
  `scripts/reset_admin.py`、`scripts/smoke.py`(healthz 套件/模式
  一致性)。
- **文档**:部署手册、问题解决指南、等保三级对照表、PIPL 清单与
  PIA 模板、轮换 Runbook×3(主密钥/套件/HMAC 索引)、升级说明模板。

### 行为变化
- 新增迁移 v9:`platform_meta` 表(key-value,当前仅 crypto_suite)。
- 服务启动新增套件守卫:与上次生效套件不同写 `crypto_suite_changed`
  审计事件(首次 intl 不算切换);gm 生效输出大写提示日志。
- 事件字典新增:`crypto_migration_started/progress/completed`、
  `master_key_rotated`。
- 信封格式向后兼容扩展:双写窗口开启时新增 `dual` 段(旧读取方
  忽略该字段不受影响);`encrypt_envelope` 新增可选 `environ` 参数。
- `deploy/docker-compose.reference.yml` 预置 `CRYPTO_SUITE=intl`。

### 浏览器全链路验证补遗(2026-07-20 晚,Playwright + chromium)
- 新增 `tests/e2e/test_e2e_m10_gm.py` 两条真服务全链:
  ① gm 套件 SSO 链(healthz=gm / JWKS 双钥 / 浏览器登录闭环 =
  SM2-with-SM3 令牌真 HTTP 签发-验签);② gm 套件 certvault SPA 四步链
  (本地登录→上传→生成水印件→溯源命中,JWT 内存特例全程站内导航;
  服务端佐证 blob 信封 alg=SM4-GCM 与审计链)。全量回归 262 项。
- **真缺陷三条(仅真浏览器可暴露,均已修复)**:
  1. SPA↔API 证件类型契约断裂:前端自由文本 vs 后端五枚举 → 浏览器
     上传必 400。修复:`web_certs.normalize_cert_type` 关键词归一化,
     未识别归 other 且原文并入 label;store 枚举约束不动。
  2. 共享 multipart 解析器炸空值字段:`strip(b"\r\n")` 贪婪剥离吃掉
     空体段(浏览器 FormData 常态)的头体分隔符 → 500。修复:首尾各剥
     一个 CRLF(协议语义),同时消除对二进制文件尾 CR/LF 字节的
     潜在损坏风险(`apps/rp_common/multipart.py`)。
  3. 平台级:四 SPA 顶部导航整体失效——TopBar 的 Link 渲染在 Router
     Provider 之外,默认 `navigate` 为空操作;certvault 叠加 JWT 内存态,
     本地用户被困证件库页。修复:ui-kit `globalNavigate`(pushState +
     显式派发 popstate 单源更新)作为上下文默认值,Link 的 active 态
     自行订阅 popstate,Provider 内外行为一致;四 SPA 重建。
- 环境更正:构建机 node v22 实际可用(此前 lint 跳过系 node_modules
  未安装)——eslint + prettier 本次起在 CI 第 6 步真跑并通过。
- 测试基建:`tests/e2e/live.py` LiveIdpEnv/LiveStack 支持
  `idp_extra_environ`(注入 CRYPTO_SUITE=gm 等)。

### 双库同测与真实中间件集成(2026-07-21,GAP-03/GAP-04 解除)
- 排雷:`ci_gate.sh` 的 GD_DB_URL 此前无消费点("入口提供但未接线")。
  接线:`tests/base.make_db_url`(GD_DB_URL 有则每测试环境创建独立 PG 库,
  atexit 统一 `DROP ... WITH (FORCE)` 回收——库堆积曾实测撑爆构建机磁盘);
  `IdpEnv` 增 db_url 与 store 注入参数。
- 新增 `tests/test_j_pg_redis.py`:J.1 PG 迁移幂等/审计禁改触发器/
  R-IDP-2 套件迁移端到端;J.4 真 Redis 跨实例锁定累加 + 宕机 fail-closed。
- 全部 36 测试模块(含 Playwright 浏览器组)在 PostgreSQL 16 方言下全绿;
  修复方言缺口四类:① `INSERT OR IGNORE` → `_adapt_sql` 通用改写
  `ON CONFLICT DO NOTHING`(业务零改动);② UPSERT DO UPDATE 裸列名
  在 PG 歧义 → 表名限定(quiz practice/elo/migrate 三处);③ SQLite
  标量 `MAX(a,b)` PG 不存在 → CASE 等价改写;④ 单方言断言/裸
  DROP TRIGGER → `gd_storage.DB_ERRORS` 双方言异常元组与按 dialect 分支。
- SQLite 默认路径 266 项全量防回归全绿(J 组 4 项在未设环境时跳过)。

### 收官打磨(2026-07-21)
- 基准数据表与三包产物随方言修复后的代码重新生成(指纹更新;
  B2 迁移吞吐 137.6 obj/s)。
- 浏览器 banner 用例时序加固:count() 瞬时采样在全量跑批高负载下
  偶发踩空 health 重渲瞬间 → 改等待式断言(wait_for_selector)。
- GAP-15 状态计数更新(浏览器用例 15 → 17 项,含 PG 方言重跑)。
- SQLite 全量 266 项终验全绿;ci_gate 六步通过。

### 交付完整性终检(2026-07-21)
- 全新克隆自 GitHub 的仓库通过自包含冒烟(crypto/门禁/F 组 26 项全绿)。
- 抓获并补齐交付缺口:仓库缺 `requirements.txt`(Dockerfile 引用必致
  构建失败)——按 wheels 名单与 import 面生成,运行必备/可选(PG、
  Playwright)分层注明。
- 打交付标签 v2.0.0-m10(附注:10/10 里程碑 + 双库同测 + 浏览器全链路)。

### 单机六系统部署包(2026-07-21,针对国内网络在线构建)
- 新增 `deploy/docker-compose.single.yml`:六系统单实例 + PG16(UTF-8 locale
  固定)+ Redis + nginx 统一反代;镜像用阿里云公开源,健康检查 + 依赖顺序
  编排;共享构建一镜像多入口。
- 新增 `deploy/nginx.single.conf`:六域名反代,80→443 强制跳转,登录/API
  限速,f3d WS 升级头透传,certvault 20m 上传上限。
- 新增 `deploy/gen_selfsigned_certs.sh`:内网自签 CA + 六域名 SAN 证书
  (leaf 825 天),实跑验证 SAN 齐全且 CA 校验通过。
- 新增 `deploy/bootstrap.sh`:一键部署引导,两阶段解决"RP 依赖 IdP 先登记
  SSO 客户端"的顺序问题(起 IdP → 容器内登记四 RP → 回填 .env → 起全量 →
  冒烟),幂等。
- 新增 `scripts/register_sso_clients.py`:四 RP 客户端一键幂等登记,输出可
  回填的 .env 密钥行(已存在则跳过不重置);实跑验证登记与幂等重跑。
- Dockerfile 国内源化:pip 走阿里云 PyPI(可 build-arg 覆盖),apt 换源并
  补 opencv 运行所需系统库(libgl1/libglib2.0-0);瘦身只拷运行目录;
  新增 `.dockerignore`。fetch_libs 默认源改 npmmirror。
- env.example 扩为单机六系统全变量(四 RP 的 secret/redirect + issuer)。
- 新增 `docs/deployment_single_host.md`:国内网络单机部署手册(镜像加速/
  自签证书/DNS/换域名/排障/运维)。
- 实证:模拟镜像内环境用 requirements 依赖真起 run_idp 与 run_rp certvault,
  healthz 均 200,自检全绿,无 SSO 变量时优雅降级 sso_enabled=false。
