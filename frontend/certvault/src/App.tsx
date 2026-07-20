// =============================================================================
// @file  App.tsx
// @brief 证件库 SPA 壳:横幅、认证门(内存 JWT,刷新走 exchange 静默恢复)、
//        导航(证件库/生成水印件/备案台账/溯源识别/管理)。
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { useEffect, useState } from 'react';
import { Link, ModeBanner, ModeSuiteBadge, Router, TopBar, useHealth } from '@gd/ui-kit';
import { getUser, tryExchange } from './api';
import { LoginPage } from './pages/Login';
import { CertsPage } from './pages/Certs';
import { IssuePage } from './pages/Issue';
import { RecordsPage } from './pages/Records';
import { TracePage } from './pages/Trace';
import { AdminPage } from './pages/Admin';

/** @brief 应用壳 */
export function App(): JSX.Element {
  const health = useHealth();
  const [ready, setReady] = useState<boolean>(false);
  const [tick, setTick] = useState<number>(0);

  useEffect(() => {
    // 刷新恢复:先静默尝试 SSO 会话换 JWT(H08 §3 特例)
    void tryExchange().finally(() => setReady(true));
  }, []);

  const user = getUser();
  const authed_cb = (): void => setTick((t) => t + 1);
  void tick;

  const routes = [
    { pattern: '/app/login', element: <LoginPage onAuthed={authed_cb} /> },
    { pattern: '/app/issue', element: <IssuePage /> },
    { pattern: '/app/records', element: <RecordsPage /> },
    { pattern: '/app/trace', element: <TracePage /> },
    { pattern: '/app/admin', element: <AdminPage /> },
    { pattern: '/app', element: <CertsPage /> },
    { pattern: '/app/*', element: <CertsPage /> },
  ];

  return (
    <div>
      <ModeBanner health={health} />
      <TopBar
        title="港电 · 证件库"
        nav={
          <>
            <Link to="/app" exact>
              证件库
            </Link>
            <Link to="/app/issue">生成水印件</Link>
            <Link to="/app/records">备案台账</Link>
            <Link to="/app/trace">溯源识别</Link>
            {user?.role === 'admin' ? <Link to="/app/admin">管理</Link> : null}
          </>
        }
        right={
          <span>
            <ModeSuiteBadge health={health} />{' '}
            <span className="gd-badge">{ready ? (user ? user.username : '未登录') : '…'}</span>
          </span>
        }
      />
      <main className="gd-main">
        {ready ? <Router routes={routes} /> : <div className="gd-empty">会话恢复中…</div>}
      </main>
    </div>
  );
}
