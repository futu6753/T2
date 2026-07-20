// =============================================================================
// @file  Status.tsx
// @brief 运行状态:features 十项、接线/队列/外发统计(runtime)、M17 环境
//        告警面板、最近事件。
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { useEffect, useState } from 'react';
import { apiGet, Card, Dot, Empty, ErrorBar, type LayeredError } from '@gd/ui-kit';

interface Feature {
  id: string;
  status: string;
  detail?: string;
}

/** @brief 状态页 */
export function StatusPage(): JSX.Element {
  const [features, setFeatures] = useState<Feature[]>([]);
  const [runtime, setRuntime] = useState<Record<string, unknown> | null>(null);
  const [events, setEvents] = useState<Record<string, unknown>[]>([]);
  const [error, setError] = useState<LayeredError | null>(null);

  useEffect(() => {
    const alive = true;
    const load = async (): Promise<void> => {
      const [f, rt, ev] = await Promise.all([
        apiGet<{ features: Feature[] }>('/api/v1/status/features'),
        apiGet<Record<string, unknown>>('/api/v1/status/runtime'),
        apiGet<{ events: Record<string, unknown>[] }>('/api/v1/events/recent'),
      ]);
      if (!alive) return;
      if (f.ok && f.data) setFeatures(f.data.features);
      else setError(f.error);
      if (rt.ok && rt.data) setRuntime(rt.data);
      if (ev.ok && ev.data) setEvents(ev.data.events ?? []);
    };
    void load();
    const timer = window.setInterval(() => void load(), 10000);
    return () => window.clearInterval(timer); // 卸载即清理(H11 §四.8)
  }, []);

  const warnings = (runtime?.['env_warnings'] as string[] | undefined) ?? [];
  const providers =
    (runtime?.['providers'] as Record<string, { configured: boolean }> | undefined) ?? {};

  return (
    <div>
      <ErrorBar error={error} />
      {warnings.length > 0 ? (
        <div className="gd-alert warn">
          <strong>环境配置告警(M17)</strong>
          {warnings.map((w, i) => (
            <p key={i} style={{ margin: '4px 0 0' }}>
              {w}
            </p>
          ))}
        </div>
      ) : null}
      <Card title="功能开关(features)">
        {features.length === 0 ? (
          <Empty text="feature 清单加载中…" />
        ) : (
          <table className="gd-table">
            <thead>
              <tr>
                <th>功能</th>
                <th>状态</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              {features.map((f) => (
                <tr key={f.id}>
                  <td>{f.id}</td>
                  <td>
                    <Dot
                      kind={
                        f.status === 'enabled' ? 'ok' : f.status === 'planned' ? 'idle' : 'warn'
                      }
                      label={f.status}
                    />
                  </td>
                  <td className="gd-help">{f.detail ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <Card title="南向接线(providers)">
        <p style={{ display: 'flex', gap: 16, flexWrap: 'wrap', margin: 0 }}>
          {Object.entries(providers).map(([name, p]) => (
            <span key={name}>
              <Dot
                kind={p.configured ? 'ok' : 'idle'}
                label={`${name}:${p.configured ? '已配置' : '未配置'}`}
              />
            </span>
          ))}
        </p>
      </Card>
      <Card title="最近事件(旁路环形缓冲)">
        {events.length === 0 ? (
          <Empty text="暂无事件" />
        ) : (
          <table className="gd-table">
            <tbody>
              {events.slice(0, 20).map((e, i) => (
                <tr key={i}>
                  <td style={{ fontFamily: 'var(--gd-mono)', fontSize: 'var(--gd-fs-xs)' }}>
                    {JSON.stringify(e)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
