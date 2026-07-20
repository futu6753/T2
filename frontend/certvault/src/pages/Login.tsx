// =============================================================================
// @file  Login.tsx
// @brief certvault 登录页:本地口令(+TOTP)登录与 SSO 换取双路;423 锁定
//        文案含等待时长(06-E8);表单不回显口令。
// @author 港电实验室平台组
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { useEffect, useState } from 'react';
import { apiGet, Card, ErrorBar, useNavigate, type LayeredError } from '@gd/ui-kit';
import { cvForm, setAuth, tryExchange, cvGet, LOGIN_PATH } from '../api';

void LOGIN_PATH;

/** @brief 登录页 */
export function LoginPage(props: { onAuthed: () => void }): JSX.Element {
  const nav = useNavigate();
  const [ssoEnabled, setSsoEnabled] = useState<boolean>(false);
  const [username, setUsername] = useState<string>('');
  const [password, setPassword] = useState<string>('');
  const [totp, setTotp] = useState<string>('');
  const [error, setError] = useState<LayeredError | null>(null);
  const [busy, setBusy] = useState<boolean>(false);

  useEffect(() => {
    let alive = true;
    void apiGet<{ enabled: boolean }>('/sso/status', { noRedirect: true }).then((r) => {
      if (alive && r.ok) setSsoEnabled(Boolean(r.data?.enabled));
    });
    return () => {
      alive = false;
    };
  }, []);

  const local_cb = async (): Promise<void> => {
    setBusy(true);
    const form = new URLSearchParams();
    form.set('username', username);
    form.set('password', password);
    if (totp) form.set('totp', totp);
    const r = await cvForm<{ token: string; totp_enabled: boolean }>(
      'POST',
      '/auth/login',
      form,
      true,
    );
    setBusy(false);
    if (r.ok && r.data?.token) {
      const me = await (async () => {
        setAuth(r.data!.token, username, 'user');
        return cvGet<{ username: string; role: string }>('/auth/me', true);
      })();
      if (me.ok && me.data) setAuth(r.data.token, me.data.username, me.data.role);
      props.onAuthed();
      nav('/app');
    } else {
      setError(r.error);
      setPassword(''); // 失败清空口令,不残留
    }
  };

  const sso_cb = async (): Promise<void> => {
    setBusy(true);
    if (await tryExchange()) {
      props.onAuthed();
      nav('/app');
    } else {
      // 无 SSO 会话 → 整页去统一登录,回跳本页
      window.location.href = `/sso/login?next=${encodeURIComponent('/app')}`;
    }
    setBusy(false);
  };

  return (
    <div>
      <ErrorBar error={error} />
      {ssoEnabled ? (
        <Card title="统一身份认证">
          <button className="gd-btn" onClick={sso_cb} disabled={busy}>
            使用 SSO 登录(自动换取本系统凭证)
          </button>
          <p className="gd-help">登录凭证仅存于本页内存,刷新后自动经 SSO 会话恢复。</p>
        </Card>
      ) : null}
      <Card title="本地账号登录">
        <label className="gd-field">
          <span>用户名</span>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
          />
        </label>
        <label className="gd-field">
          <span>口令</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </label>
        <label className="gd-field">
          <span>动态口令(已启用 2FA 的账号必填)</span>
          <input
            value={totp}
            onChange={(e) => setTotp(e.target.value)}
            inputMode="numeric"
            maxLength={6}
          />
        </label>
        <button className="gd-btn" onClick={local_cb} disabled={busy || !username || !password}>
          登录
        </button>
      </Card>
    </div>
  );
}
