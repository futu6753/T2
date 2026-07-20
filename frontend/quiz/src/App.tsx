// =============================================================================
// @file  App.tsx
// @brief 刷题 SPA 壳:DEMO/生产成对横幅、身份门(SSO/游客)、导航与路由。
//        路由 history 模式(后端 index.html 兜底,H11 §一 F2)。
// @author 港电实验室平台组
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { useEffect, useState } from 'react';
import { Link, ModeBanner, Router, TopBar, useHealth } from '@gd/ui-kit';
import { qzMe, type Identity } from './api';
import { LoginPage } from './pages/Login';
import { PracticePage } from './pages/Practice';
import { WrongbookPage } from './pages/Wrongbook';
import { ProgressPage } from './pages/Progress';
import { ReviewPage } from './pages/Review';
import { MigratePage } from './pages/Migrate';

/** 身份上下文(页面经 props 注入,保持数据流显式) */
export interface IdentityState {
  identity: Identity | null;
  refresh: () => Promise<void>;
}

/** @brief 应用壳 */
export function App(): JSX.Element {
  const health = useHealth();
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [checked, setChecked] = useState<boolean>(false);

  const refresh = async (): Promise<void> => {
    const r = await qzMe();
    setIdentity(r.ok && r.data ? r.data : null);
    setChecked(true);
  };

  useEffect(() => {
    void refresh();
  }, []);

  const who =
    identity?.kind === 'sso'
      ? `SSO:${identity.username ?? ''}`
      : identity?.kind === 'guest'
        ? `游客 ${identity.guest_code ?? ''}`
        : '未登录';

  const idState: IdentityState = { identity, refresh };
  const routes = [
    { pattern: '/app/login', element: <LoginPage id={idState} /> },
    { pattern: '/app/wrongbook', element: <WrongbookPage /> },
    { pattern: '/app/progress', element: <ProgressPage /> },
    { pattern: '/app/review', element: <ReviewPage /> },
    { pattern: '/app/migrate', element: <MigratePage id={idState} /> },
    { pattern: '/app/q/:qno', element: <PracticePage /> },
    { pattern: '/app', element: <PracticePage /> },
    { pattern: '/app/*', element: <PracticePage /> },
  ];

  return (
    <div>
      <ModeBanner health={health} />
      <TopBar
        title="港电 · 安全刷题"
        nav={
          <>
            <Link to="/app" exact>
              刷题
            </Link>
            <Link to="/app/review">今日复习</Link>
            <Link to="/app/wrongbook">错题本</Link>
            <Link to="/app/progress">进度</Link>
            <Link to="/app/migrate">迁移码</Link>
          </>
        }
        right={
          <span className="gd-badge" title="当前身份">
            {checked ? who : '…'}
          </span>
        }
      />
      <main className="gd-main">
        <Router routes={routes} />
      </main>
    </div>
  );
}
