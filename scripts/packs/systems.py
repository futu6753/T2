# -*- coding: utf-8 -*-
"""
@file    systems.py
@brief   三包流水线(H10)六系统结构化元数据:全称/目录/功能提纲/发明点候选
         (R-xx 锚点)/基线数据表挂钩。生成器据此产出软著/论文/专利三包。
@author  港电实验室平台组
@date    2026-07-20
Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
"""

PLATFORM_VERSION = "V2.0"
COMPANY = "厦门自贸片区港务电力有限公司(港电实验室)"

# 每系统:name 全称 / short 简称 / dirs 源码目录 / features 功能提纲 /
# inventions [(标题, R 锚点, 摘要)] / baselines 数据表名 / manual_flows 手册主流程
SYSTEMS = {
    "idp": {
        "name": "港电 UniPass 统一身份认证与单点登录平台",
        "short": "UniPass IdP",
        "dirs": ("apps/idp", "packages/gd_sso_client", "packages/gd_crypto"),
        "features": (
            "OIDC 授权码单点登录五端点(authorize/token/jwks/userinfo/logout)",
            "口令/TOTP/短信三因素登录与锁定策略(等保口令基线+到期自动解锁)",
            "管理台五区:用户/客户端/策略/审计/模式(CSRF 双提交+末位管理员守护)",
            "DEMO⇄生产一键切换恢复清单与自检报告",
            "双密码套件(intl/gm)热切换:存量自描述可解、口令透明重哈希、JWKS 双钥",
            "套件迁移脚本:双写窗口、断点续迁、开始/进度/完成审计锚点",
        ),
        "inventions": (
            ("密码套件无损热切换方法", "13-R-IDP-2",
             "密文对象自描述算法元数据 + 双写窗口 + 幂等断点迁移三机制协同,"
             "实现国际/国密套件切换零停机零丢数据"),
            ("登录上下文跨实例无状态存活方法", "H02-A3/C08",
             "签名算法自描述的三段式上下文令牌,进程重启与多实例间无缝存活"),
        ),
        "baselines": ("b2_auth_availability", "b4_mode_switch"),
        "manual_flows": ("管理员登录与首登强改密", "创建用户与分组授权",
                         "接入一个新 RP(客户端登记)", "DEMO 切生产操作",
                         "套件迁移操作(migrate_crypto_suite)"),
    },
    "certvault": {
        "name": "港电证件安全托管与盲水印溯源系统",
        "short": "CertVault",
        "dirs": ("apps/certvault",),
        "features": (
            "证件库信封加密托管(每对象独立 DEK+主密钥包裹)",
            "bw 频域盲水印引擎(DWT→DCT→SVD QIM,96bit 含 CRC16+RS 纠错)",
            "固定顺序发证流水线:微扭曲→明水印→团花→智能锚定→暗码",
            "溯源盲提与多引擎组合投票置信(R-CV-3)",
            "介质自适应参数推荐器(R-CV-2)与备案撤销(R-CV-5)",
        ),
        "inventions": (
            ("抗翻拍频域盲水印嵌提方法", "13-R-CV-1/R-CV-4",
             "σ1 奇异值 QIM 调制结合 RS 纠错与智能锚定,JPEG q60 零误码,"
             "翻拍矩阵基准量化鲁棒域"),
            ("多引擎溯源组合投票方法", "13-R-CV-3",
             "异构水印引擎并行盲提+置信投票,单引擎故障隔离不阻断溯源"),
        ),
        "baselines": ("erasure", "recapture"),
        "manual_flows": ("注册登录与 2FA", "上传证件", "发证(参数与预览)",
                         "溯源与结果解读", "撤销与备案"),
    },
    "nvr": {
        "name": "港电 NVR 设备集中监控与告警运维系统",
        "short": "NVR Monitor",
        "dirs": ("apps/nvr",),
        "features": (
            "检测判定树五分支契约与可注入探针",
            "去抖策略族五模式(连续/时长/EWMA/滞回/自适应,R-NVR-1)",
            "告警生命周期:每设备每范围单条活动、恢复即解、通道并存",
            "Webhook/阿里云 RPC 双渠道派发(签名逐字锁定+线性退避续跑)",
            "推送设备三行格式接入、周报事实层(R-NVR-3)、Prometheus 暴露",
        ),
        "inventions": (
            ("场站自适应告警去抖方法", "13-R-NVR-1",
             "五策略族统一快照接口与误报-延迟 Pareto 回放选型,"
             "抖动场站误报归零同时约束检出延迟"),
        ),
        "baselines": ("debounce",),
        "manual_flows": ("设备台账录入", "告警策略选型", "渠道配置与验签",
                         "巡检与单台隔离", "周报生成"),
    },
    "factory3d": {
        "name": "港电三维物联工厂监控大屏系统",
        "short": "Factory-3D",
        "dirs": ("apps/factory3d",),
        "features": (
            "布局模型结构操作全集(重叠校验回弹)与默认工厂模板",
            "告警状态机(抖动静默/再掉线新告警/历史有界)",
            "帧率闭环降级阶梯(滞回分档+自动回升,R-F3D-1)",
            "AI 助手事务化:23 动作注册表、dry-run→确认→原子回滚(R-F3D-2)",
            "WS 实时通道与 22-200 台规模线性帧预算(R-F3D-4)",
        ),
        "inventions": (
            ("三维大屏端帧率闭环降级方法", "13-R-F3D-1",
             "端侧 fps 回报驱动服务端滞回分档降级与自动回升,弱终端可用性兜底"),
            ("AI 助手事务化安全执行方法", "13-R-F3D-2",
             "动作白名单+形状校验+dry-run 预览+确认令牌+原子回滚五层防线,"
             "50 条恶意指令误执行为零"),
        ),
        "baselines": ("b7_assist_security", "f3d_scale"),
        "manual_flows": ("大屏接入与布局编辑", "AI 助手指令与确认",
                         "降级观察", "外部数据注入(HMAC)"),
    },
    "quiz": {
        "name": "港电电力安全知识刷题训练系统",
        "short": "SafeQuiz",
        "dirs": ("apps/quiz",),
        "features": (
            "题库 233 题五题型(seed 幂等生成,84 配图)",
            "背题/做题双模式与四类判分",
            "SM-2 变体分层排期与今日复习队列(R-QZ-1)",
            "整数 ELO 能力画像与邻域采样(R-QZ-2)",
            "一次性迁移码无损迁移(R-QZ-3,零个人信息)",
        ),
        "inventions": (
            ("游客学习记录无损迁移方法", "13-R-QZ-3",
             "一次性散列迁移码 + TTL + 用后作废,不采集个人信息完成跨身份迁移"),
        ),
        "baselines": ("quiz_learning",),
        "manual_flows": ("游客开始刷题", "错题本与进度", "复习队列",
                         "迁移码换绑账号"),
    },
    "adapter": {
        "name": "港电光伏云平台云云对接多平台适配器",
        "short": "Cloud Adapter",
        "dirs": ("apps/adapter",),
        "features": (
            "六边形架构:core 纯标准库、api 薄壳、14 路由显式 operation_id",
            "映射 DSL(yamlite 受限声明:字段路径/单位换算/枚举/模板,R-AD-1)",
            "南向织光/星逻/司运三平台验签与采集,司空2 预留 501",
            "两阶段命令 reply(条件必填/幂等 409/ack 超时 504)",
            "契约导出+diff 防漂移流水线(五份映射升格契约工件)",
        ),
        "inventions": (
            ("声明式云云映射防漂移方法", "13-R-AD-1",
             "受限映射 DSL 升格为版本化契约工件,导出-diff 流水线阻断语义漂移"),
        ),
        "baselines": ("adapter_onboard",),
        "manual_flows": ("新平台接入(写映射)", "契约导出与 diff",
                         "命令下发与 reply 观察"),
    },
}
