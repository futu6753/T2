// =============================================================================
// @file  App.tsx
// @brief nvr SPA 壳:横幅 + 导航(总览/告警/周报/设置)+ 路由。
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { Link, ModeBanner, ModeSuiteBadge, Router, TopBar, useHealth } from '@gd/ui-kit';
import { OverviewPage } from './pages/Overview';
import { DevicePage } from './pages/Device';
import { AlertsPage } from './pages/Alerts';
import { ReportPage } from './pages/Report';
import { NvrSettingsPage } from './pages/Settings';
import { LoginPage } from './pages/Login';
import { AccountPage } from './pages/Account';

/** @brief 应用壳 */
export function App(): JSX.Element {
  const health = useHealth();
  const routes = [
    { pattern: '/app/login', element: <LoginPage /> },
    { pattern: '/app/devices/:id', element: <DevicePage /> },
    { pattern: '/app/alerts', element: <AlertsPage /> },
    { pattern: '/app/report', element: <ReportPage /> },
    { pattern: '/app/settings', element: <NvrSettingsPage /> },
    { pattern: '/app/account', element: <AccountPage me={null} /> },
    { pattern: '/app', element: <OverviewPage /> },
    { pattern: '/app/*', element: <OverviewPage /> },
  ];
  return (
    <div>
      <ModeBanner health={health} />
      <TopBar
        title="港电 · 录像机监测"
        nav={
          <>
            <Link to="/app" exact>
              总览
            </Link>
            <Link to="/app/alerts">告警</Link>
            <Link to="/app/report">周报</Link>
            <Link to="/app/settings">设置</Link>
            <Link to="/app/account">账户</Link>
          </>
        }
        right={<ModeSuiteBadge health={health} />}
      />
      <main className="gd-main">
        <Router routes={routes} />
      </main>
    </div>
  );
}
