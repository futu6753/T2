// =============================================================================
// @file  Alerts.tsx  告警页:firing/resolved 过滤、恢复即解含时长。
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { useEffect, useState } from 'react';
import { Card, Dot, Empty, ErrorBar, type LayeredError } from '@gd/ui-kit';
import { nvAlerts } from '../api';

/** @brief 告警页 */
export function AlertsPage(): JSX.Element {
  const [alerts, setAlerts] = useState<Record<string, unknown>[]>([]);
  const [state, setState] = useState<string>('firing');
  const [activeTotal, setActiveTotal] = useState<number>(0);
  const [error, setError] = useState<LayeredError | null>(null);

  useEffect(() => {
    let alive = true;
    void nvAlerts(state).then((r) => {
      if (!alive) return;
      if (r.ok && r.data) {
        setAlerts(r.data.alerts);
        setActiveTotal(r.data.active_total);
      } else setError(r.error);
    });
    return () => {
      alive = false;
    };
  }, [state]);

  return (
    <Card title={`告警(当前活动 ${activeTotal} 条)`}>
      <ErrorBar error={error} />
      <label>
        状态:
        <select value={state} onChange={(e) => setState(e.target.value)} aria-label="告警状态">
          <option value="firing">活动中</option>
          <option value="resolved">已恢复</option>
          <option value="">全部</option>
        </select>
      </label>
      {alerts.length === 0 ? (
        <Empty text="当前筛选下没有告警" />
      ) : (
        <table className="gd-table">
          <thead>
            <tr>
              <th>设备</th>
              <th>范围</th>
              <th>触发状态</th>
              <th>开始(本地)</th>
              <th>恢复/时长</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            {alerts.map((a, i) => {
              const dev = a['device'] as { name: string; region: string; station: string } | null;
              const resolved = a['resolved_at'];
              return (
                <tr key={i}>
                  <td>
                    {dev
                      ? `${dev.name}(${dev.region}/${dev.station})`
                      : `#${String(a['device_id'])}`}
                  </td>
                  <td>{a['scope'] === 'channel' ? '通道' : '本体'}</td>
                  <td>
                    <Dot kind={resolved ? 'ok' : 'danger'} label={String(a['trigger_status'])} />
                  </td>
                  <td>{new Date(String(a['started_at'])).toLocaleString()}</td>
                  <td>
                    {resolved
                      ? `${new Date(String(resolved)).toLocaleString()}(持续 ${String(a['duration_seconds'])}s)`
                      : '进行中'}
                  </td>
                  <td>{String(a['detail'] ?? '')}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Card>
  );
}
