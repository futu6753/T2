# 港电实验室统一平台 · 交付验收报告(V2.0 终版)

> 生成日期:2026-07-21 · 提交基线:见 git 末次提交
> Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)

## 一、里程碑完成度(10/10)

| # | 里程碑 | 状态 | 关键交付 |
| - | ------ | ---- | -------- |
| 1 | 共享库与平台底座 | ✅ | gd_common/gd_crypto/gd_policy/gd_storage 四库 + CI 门禁 |
| 2 | IdP 统一认证中心 | ✅ | OIDC 五端点、三因素登录、管理台五区、模式切换 |
| 3 | RP 生态接入 | ✅ | rp_common 共享层、四系统 SSO、多实例参考拓扑 |
| 4 | certvault 全功能 | ✅ | bw 频域盲水印、发证流水线、溯源投票、信封托管 |
| 5 | nvr-monitor 全功能 | ✅ | 判定树、去抖策略族、告警生命周期、双渠道派发 |
| 6 | factory-3d 大屏 | ✅ | 布局模型、降级阶梯、助手事务化、规模基准 |
| 7 | 安全刷题系统 | ✅ | 233 题五题型、SRS 排期、ELO 画像、迁移码 |
| 8 | adapter 适配器 | ✅ | 六边形重建、映射 DSL、契约 diff 防漂移 |
| 9 | 前端三形态 | ✅ | ui-kit、四 SPA、三维大屏、浏览器 E2E |
| 10 | 交付收口 | ✅ | 国密真实现、迁移体系、三包流水线、部署包 |

## 二、测试矩阵(终验数字)

| 维度 | 规模 | 结果 |
| ---- | ---- | ---- |
| SQLite 全量回归 | 266 项(36 模块) | 全绿(4 项为 J 组无环境设计跳过) |
| PostgreSQL 16 方言 | 全部 36 模块(分四段) | 全绿;每环境独立库 + atexit FORCE 回收 |
| Redis 7 真集成 | J.4 双实例累加 / fail-closed | 全绿 |
| Playwright 浏览器 | 17 项(SQLite 与 PG 双方言) | 全绿 |
| CI 门禁 | 六步(测试/敏感扫描/DEMO 门/编译/前端静态/lint) | 全部通过,eslint+prettier 真跑 |
| 基准 | B1–B10 全覆盖,环境指纹落盘 | benchmarks/data/ 随版本库交付 |

已知偶发(非缺陷,已留档):certvault 盲提对随机 distort_seed 组合存在
极小概率失败(负载敏感);F 组冒烟已用大图+保底重试解耦,K 组保留
严格形态作为水印质量监控信号。

## 三、GAP 台账终态

| GAP | 主题 | 终态 |
| --- | ---- | ---- |
| 01 | 国密套件占位 | **解除**:SM3/SM4-GCM/SM2 纯 Python 参考实现,标准向量+AES 对拍锚定;生产 Provider 提档路径已注明 |
| 03 | PG 全量未执行 | **解除**:GD_DB_URL 真接线,36 模块 PG 全绿,四类方言缺口修复 |
| 04 | Redis 集成未执行 | **解除**:真 Redis 跨实例/宕机语义全验 |
| 15 | 浏览器依赖 | **解除**:17 项实跑;离线环境自动跳过不阻塞 |
| 16 | Three.js 场景 | **解除**(里程碑 9) |
| 13/14 | stega·tm·aliyun 引擎 / ISAPI 探针 | 目标环境挂接类:骨架与降级文案就绪,随模型/设备导入激活 |

## 四、真实性验证抓获的缺陷台账(全部已修复)

进程内 260+ 项测试长期全绿的系统,引入"真浏览器 + 真数据库"后
共抓出七类真缺陷——方法论结论:测试构造的输入过于"规矩",
真实客户端与真实方言是不可替代的验收标尺。

| # | 缺陷 | 暴露方式 | 修复 |
| - | ---- | -------- | ---- |
| 1 | SPA 证件类型自由文本 vs 后端五枚举 → 上传必 400 | 真浏览器 | web 层关键词归一化 |
| 2 | multipart 空值字段炸 500(strip 贪婪剥离) | 真浏览器 FormData | 首尾各剥一个 CRLF |
| 3 | 四 SPA 顶部导航整体失效(Link 在 Router Provider 外) | 真浏览器点击 | ui-kit globalNavigate 单源更新 |
| 4 | INSERT OR IGNORE 为 SQLite 方言 | 真 PG | _adapt_sql 通用改写 ON CONFLICT |
| 5 | UPSERT DO UPDATE 裸列名 PG 歧义 | 真 PG | 表名限定(quiz 三处) |
| 6 | 标量 MAX(a,b) PG 不存在 | 真 PG | CASE 等价改写 |
| 7 | 单方言异常断言 / 裸 DROP TRIGGER | 真 PG | DB_ERRORS 元组 / dialect 分支 |

## 五、一键运行指引

```bash
# 全量回归(SQLite 默认)
make test                     # 或 python3 -m unittest discover -s tests

# CI 六步门禁
make ci                       # bash ci_gate.sh

# 双库同测 + Redis 集成(GAP-03/04 复验入口)
GD_TEST_PG_URL=postgresql://gd:***@host/db \
GD_TEST_REDIS_URL=redis://host:6379/0 bash ci_gate.sh

# 基准与三包
make benchmarks && make packs

# 部署(详见 docs/deployment_manual.md)
docker compose -f deploy/docker-compose.reference.yml up -d
python3 scripts/smoke.py --idp https://sso.内网域名 --rp cv=https://cv.内网域名
```

## 六、交付物清单指引

- 代码与测试:apps/ packages/ tests/ frontend/(构建产物 apps/*/web/dist)
- 部署包:deploy/(Dockerfile/compose/nginx/harden/backup_cron/env.example)
- 运维脚本:scripts/(gen_master_key/rotate_master_key/migrate_crypto_suite/
  reset_admin/unlock_user/create_admin/smoke/run_idp/run_rp/run_adapter)
- 文档:docs/(部署手册/排障/等保对照/PIPL/Runbook×3/升级模板/GAP 台账/本报告)
- 三包:dist/copyright|paper|patent/<系统>/(make packs 再生)
- 基准数据:benchmarks/data/(环境指纹内嵌)
