// =============================================================================
// @file  Migrate.tsx
// @brief 游客→SSO 迁移码页面(R-QZ-3):游客侧生成一次性码(明文仅展示一次,
//        15 分钟 TTL);SSO 侧兑换合并进度(零个人信息)。按身份渲染对应侧。
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { useState } from 'react';
import { Card, ErrorBar, type LayeredError } from '@gd/ui-kit';
import { qzMigrateCode, qzMigrateRedeem } from '../api';
import type { IdentityState } from '../App';

/** @brief 迁移码页 */
export function MigratePage(props: { id: IdentityState }): JSX.Element {
  const kind = props.id.identity?.kind ?? null;
  const [code, setCode] = useState<string>('');
  const [issued, setIssued] = useState<{ code: string; ttl_seconds: number; note: string } | null>(
    null,
  );
  const [redeemed, setRedeemed] = useState<boolean>(false);
  const [error, setError] = useState<LayeredError | null>(null);

  const issue_cb = async (): Promise<void> => {
    const r = await qzMigrateCode();
    if (r.ok && r.data) setIssued(r.data);
    else setError(r.error);
  };

  const redeem_cb = async (): Promise<void> => {
    const r = await qzMigrateRedeem(code.trim());
    if (r.ok) setRedeemed(true);
    else setError(r.error);
  };

  return (
    <div>
      <ErrorBar error={error} />
      {kind === 'guest' ? (
        <Card title="第一步(游客侧):生成迁移码">
          <p>迁移码为一次性凭证,明文仅展示一次;请在 SSO 登录后 15 分钟内于本页兑换。</p>
          <button className="gd-btn" onClick={issue_cb}>
            生成迁移码
          </button>
          {issued ? (
            <div className="gd-alert" style={{ marginTop: 10 }}>
              <strong style={{ fontSize: 'var(--gd-fs-xl)', letterSpacing: '0.2em' }}>
                {issued.code}
              </strong>
              <p style={{ marginBottom: 0 }}>{issued.note}</p>
            </div>
          ) : null}
        </Card>
      ) : kind === 'sso' ? (
        <Card title="第二步(SSO 侧):兑换迁移码">
          {redeemed ? (
            <div className="gd-alert">迁移完成:游客进度已合并到当前 SSO 账号。</div>
          ) : (
            <div>
              <label className="gd-field">
                <span>输入游客侧生成的迁移码</span>
                <input
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  placeholder="迁移码"
                />
              </label>
              <button className="gd-btn" onClick={redeem_cb} disabled={code.trim() === ''}>
                兑换并合并进度
              </button>
            </div>
          )}
        </Card>
      ) : (
        <Card title="迁移码">
          <p>请先登录(游客侧生成迁移码;SSO 侧兑换合并)。</p>
        </Card>
      )}
      <Card title="说明">
        <p className="gd-help">
          迁移码经散列存储、15 分钟有效、用后即作废,不携带任何个人信息;合并遵循「做过即保留、
          错题并集」语义(R-QZ-3)。
        </p>
      </Card>
    </div>
  );
}
