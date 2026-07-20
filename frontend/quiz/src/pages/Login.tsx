// =============================================================================
// @file  Login.tsx
// @brief 登录页:SSO 按钮显隐依据 GET /sso/status(H08 §3);游客双入口
//        (新分配 5 位 ID / 输 ID 载入,H03 §6)。401 跳转到此并保留 next。
// @author 港电实验室平台组
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { useEffect, useState } from 'react';
import { Card, ErrorBar, isSafeNext, useNavigate, type LayeredError } from '@gd/ui-kit';
import { qzGuestLoad, qzGuestNew, qzSsoStatus } from '../api';
import type { IdentityState } from '../App';

/** @brief 读取 URL ?next= 并做站内校验(06-E13) */
function readNext(): string {
  const next = new URLSearchParams(window.location.search).get('next') ?? '';
  return next && isSafeNext(next) ? next : '/app';
}

/** @brief 登录页 */
export function LoginPage(props: { id: IdentityState }): JSX.Element {
  const nav = useNavigate();
  const [ssoEnabled, setSsoEnabled] = useState<boolean | null>(null);
  const [code, setCode] = useState<string>('');
  const [error, setError] = useState<LayeredError | null>(null);
  const [busy, setBusy] = useState<boolean>(false);

  useEffect(() => {
    let alive = true;
    void qzSsoStatus().then((r) => {
      if (alive) setSsoEnabled(r.ok ? Boolean(r.data?.enabled) : false);
    });
    return () => {
      alive = false;
    };
  }, []);

  const sso_cb = (): void => {
    // SSO 为整页跳转(IdP F1 页),next 由回调后落回站内
    const next = readNext();
    window.location.href = `/sso/login?next=${encodeURIComponent(next)}`;
  };

  const guestNew_cb = async (): Promise<void> => {
    setBusy(true);
    const r = await qzGuestNew();
    setBusy(false);
    if (r.ok) {
      await props.id.refresh();
      nav(readNext());
    } else {
      setError(r.error);
    }
  };

  const guestLoad_cb = async (): Promise<void> => {
    if (!/^\d{5}$/.test(code)) {
      setError({
        status: 0,
        kind: 'other',
        text: '游客 ID 为 5 位数字',
        waitSeconds: null,
        loginRedirect: null,
      });
      return;
    }
    setBusy(true);
    const r = await qzGuestLoad(code);
    setBusy(false);
    if (r.ok) {
      await props.id.refresh();
      nav(readNext());
    } else {
      setError(r.error);
    }
  };

  return (
    <div>
      <ErrorBar error={error} />
      <Card title="统一登录">
        {ssoEnabled === null ? (
          <p>登录方式检测中…</p>
        ) : ssoEnabled ? (
          <button className="gd-btn" onClick={sso_cb}>
            使用统一身份认证(SSO)登录
          </button>
        ) : (
          <p className="gd-help">统一身份认证暂未启用,可使用下方游客方式。</p>
        )}
      </Card>
      <Card title="游客模式(仅刷题数据,不涉个人信息)">
        <p>
          <button className="gd-btn ghost" onClick={guestNew_cb} disabled={busy}>
            分配新的 5 位游客 ID
          </button>
        </p>
        <label className="gd-field">
          <span>已有游客 ID?输入 5 位数字载入进度</span>
          <input
            value={code}
            onChange={(e) => setCode(e.target.value.trim())}
            inputMode="numeric"
            maxLength={5}
            placeholder="例如 10234"
          />
        </label>
        <button className="gd-btn" onClick={guestLoad_cb} disabled={busy}>
          载入进度
        </button>
        <p className="gd-help">
          游客数据仅以此 ID 关联;换设备前请记住 ID,或在「迁移码」页迁移到 SSO 账号。
        </p>
      </Card>
    </div>
  );
}
