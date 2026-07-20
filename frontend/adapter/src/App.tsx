// =============================================================================
// @file  App.tsx
// @brief adapter 管理/文档 SPA 壳(H11 §一 F2)。运维单文件 /console 保留为
//        低依赖备用面;本 SPA 提供状态/死信/文档三页。
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { Link, ModeBanner, Router, TopBar, useHealth } from '@gd/ui-kit';
import { StatusPage } from './pages/Status';
import { DeadLettersPage } from './pages/DeadLetters';
import { DocsPage } from './pages/Docs';

/** @brief 应用壳 */
export function App(): JSX.Element {
  const health = useHealth();
  const routes = [
    { pattern: '/app/deadletters', element: <DeadLettersPage /> },
    { pattern: '/app/docs', element: <DocsPage /> },
    { pattern: '/app', element: <StatusPage /> },
    { pattern: '/app/*', element: <StatusPage /> },
  ];
  return (
    <div>
      <ModeBanner health={health} />
      <TopBar
        title="港电 · 云云适配器"
        nav={
          <>
            <Link to="/app" exact>
              运行状态
            </Link>
            <Link to="/app/deadletters">死信重放</Link>
            <Link to="/app/docs">接入文档</Link>
          </>
        }
        right={
          <a className="gd-badge" href="/console">
            单文件运维台 →
          </a>
        }
      />
      <main className="gd-main">
        <Router routes={routes} />
      </main>
    </div>
  );
}
