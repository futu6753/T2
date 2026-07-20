// =============================================================================
// @file  Login.tsx  nvr 登录页(仅 SSO;按钮显隐依 /sso/status,H08 §3)
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { useEffect, useState } from 'react';
import { Card, isSafeNext } from '@gd/ui-kit';
import { nvSsoStatus } from '../api';

/** @brief 登录页 */
export function LoginPage(): JSX.Element {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  useEffect(() => {
    let alive = true;
    void nvSsoStatus().then((r) => {
      if (alive) setEnabled(r.ok ? Boolean(r.data?.enabled) : false);
    });
    return () => {
      alive = false;
    };
  }, []);
  const login_cb = (): void => {
    const raw = new URLSearchParams(window.location.search).get('next') ?? '/app';
    const next = isSafeNext(raw) ? raw : '/app';
    window.location.href = `/sso/login?next=${encodeURIComponent(next)}`;
  };
  return (
    <Card title="登录">
      {enabled === null ? (
        <p>登录方式检测中…</p>
      ) : enabled ? (
        <button className="gd-btn" onClick={login_cb}>
          使用统一身份认证(SSO)登录
        </button>
      ) : (
        <p>统一身份认证未启用,请联系管理员在 IdP 侧启用后再访问本系统。</p>
      )}
    </Card>
  );
}
