// =============================================================================
// @file  components.tsx
// @brief 共享基础组件(H11 §一):DEMO 全站红色横幅/生产模式指示(05-D9 成对)、
//        手机号打码组件(H04 §七)、运行模式+密码套件徽标(H04 §8.2 第 7 条)、
//        错误提示条、状态点、卡片等。默认 JSX 转义,禁 dangerouslySetInnerHTML。
// @author 港电实验室平台组
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import React, { useEffect, useState } from 'react';
import { apiGet } from './fetch';
import { maskPhone } from './envelope';
import type { LayeredError } from './errors';

/** /healthz 横切字段(各系统统一附带,H11 §二横切) */
export interface HealthInfo {
  status: string;
  mode?: string;
  crypto_suite?: string;
  sso_enabled?: boolean;
  [k: string]: unknown;
}

/** @brief 拉取 /healthz(失败返回 null,不打断页面) */
export function useHealth(): HealthInfo | null {
  const [health, setHealth] = useState<HealthInfo | null>(null);
  useEffect(() => {
    let alive = true;
    void apiGet<HealthInfo>('/healthz', { noRedirect: true }).then((r) => {
      if (alive && r.ok && r.data) setHealth(r.data);
    });
    return () => {
      alive = false; // 卸载即清理(H11 §四.8)
    };
  }, []);
  return health;
}

/**
 * @brief DEMO/生产成对横幅(05-D9):DEMO=全站红色「演示模式」横幅;
 *        生产=细条模式指示。两态必居其一,加载中渲染生产态占位避免闪红。
 */
export function ModeBanner(props: { health: HealthInfo | null }): JSX.Element {
  const mode = props.health?.mode ?? '';
  if (mode === 'demo') {
    return (
      <div className="gd-banner-demo" role="alert" data-mode="demo">
        ● 演示模式,仅限测试 —— 数据与凭据均为演示用途,禁止录入真实业务数据 ●
      </div>
    );
  }
  return (
    <div className="gd-banner-prod" data-mode={mode || 'unknown'}>
      生产模式运行中
      {props.health?.crypto_suite ? ` · 密码套件 ${props.health.crypto_suite}` : ''}
    </div>
  );
}

/** @brief 运行模式 + 密码套件徽标(管理页展示,H04 §8.2 第 7 条) */
export function ModeSuiteBadge(props: { health: HealthInfo | null }): JSX.Element {
  if (!props.health) return <span className="gd-badge">运行状态获取中…</span>;
  const mode = props.health.mode ?? '未知';
  const suite = props.health.crypto_suite ?? '—';
  return (
    <span>
      <span className={mode === 'demo' ? 'gd-badge amber' : 'gd-badge'}>模式:{mode}</span>{' '}
      <span className="gd-badge">套件:{suite}</span>
    </span>
  );
}

/** @brief 手机号打码展示(H04 §七;title 亦不含明文) */
export function PhoneMasked(props: { phone: string }): JSX.Element {
  return <span className="gd-phone">{maskPhone(props.phone)}</span>;
}

/** @brief 分层错误提示条(06-E8 文案已由 fetch 封装映射;含 request_id 便于排障) */
export function ErrorBar(props: {
  error: LayeredError | null;
  requestId?: string;
}): JSX.Element | null {
  if (!props.error) return null;
  return (
    <div className="gd-alert error" role="alert">
      {props.error.text}
      {props.requestId ? (
        <span style={{ float: 'right', color: 'var(--gd-ink-3)', fontSize: 'var(--gd-fs-xs)' }}>
          请求号 {props.requestId}
        </span>
      ) : null}
    </div>
  );
}

/** @brief 舷窗状态点:全平台统一状态语言 */
export function Dot(props: {
  kind: 'ok' | 'warn' | 'danger' | 'idle';
  label?: string;
}): JSX.Element {
  return (
    <span>
      <span className={`gd-dot ${props.kind}`} aria-hidden="true" />
      {props.label ?? ''}
    </span>
  );
}

/** @brief 内容卡片 */
export function Card(props: { title?: string; children: React.ReactNode }): JSX.Element {
  return (
    <section className="gd-card">
      {props.title ? <h2>{props.title}</h2> : null}
      {props.children}
    </section>
  );
}

/** @brief 空态 */
export function Empty(props: { text: string }): JSX.Element {
  return <div className="gd-empty">{props.text}</div>;
}

/** @brief 顶栏骨架:标题 + 导航区 + 右侧徽标区 */
export function TopBar(props: {
  title: string;
  nav?: React.ReactNode;
  right?: React.ReactNode;
}): JSX.Element {
  return (
    <header className="gd-topbar">
      <h1>{props.title}</h1>
      {props.nav ? <nav>{props.nav}</nav> : null}
      <span className="gd-spacer" />
      {props.right ?? null}
    </header>
  );
}
