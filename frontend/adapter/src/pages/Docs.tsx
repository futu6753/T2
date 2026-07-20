// =============================================================================
// @file  Docs.tsx
// @brief 接入文档页:映射 DSL 契约说明、南向四厂商与两阶段 reply 概览、
//        契约防漂移流水线指引(静态说明,不复制规约原文)。
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { Card } from '@gd/ui-kit';

/** @brief 文档页 */
export function DocsPage(): JSX.Element {
  return (
    <div>
      <Card title="接入新厂商(映射 DSL,13-R-AD-1)">
        <p>
          新厂商接入以声明式映射为先:在 <code>harness/mappings/</code> 增加一份 YAML 声明(字段路径 /
          单位换算 / 枚举 / 模板 / reply 语义),通常无需新增代码。 参考基准:第五家厂商曜阳储能 = 48
          行声明 + 0 行代码(B9)。
        </p>
        <p className="gd-help">
          映射声明是契约工件:改动后须执行 <code>scripts/adapter_contract.py export</code>
          重新冻结基线,CI 以 <code>diff</code> 做零漂移机检。
        </p>
      </Card>
      <Card title="命令下发(两阶段 reply)">
        <p>
          命令接口为两阶段语义:受理即返回,凭 <code>command_id</code> 礼貌轮询终态; 条件必填缺失回
          400(R9 信封),同键异体重复提交回 409,ack 超时回 504。
        </p>
      </Card>
      <Card title="死信与重放(13-R-AD-3)">
        <p>
          外发失败经指数退避后进入有界死信队列;「死信重放」页支持导出 → 修复 →
          重放,下游恰一次投递,二次重放与已投递事件混入自动 skip。命令行同能力见
          <code> scripts/adapter_deadletter.py</code>。
        </p>
      </Card>
      <Card title="排障">
        <p className="gd-help">
          所有响应贯通 X-Request-Id;验签失败(织光 strict / 司运 TD-022 / 星逻 token)回 401
          并落审计。低依赖单文件运维台在 <a href="/console">/console</a>。
        </p>
      </Card>
    </div>
  );
}
