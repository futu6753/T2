// =============================================================================
// @file  main.tsx
// @brief 刷题 SPA 入口(React 18 createRoot)
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import React from 'react';
import { createRoot } from 'react-dom/client';
import '@gd/ui-kit/tokens.css';
import { App } from './App';

const rootEl = document.getElementById('root');
if (rootEl) {
  createRoot(rootEl).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}
