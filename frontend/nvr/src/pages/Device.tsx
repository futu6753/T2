// =============================================================================
// @file  Device.tsx
// @brief 设备详情:本体/通道分离、统一时间线、检测明细(证据链可展开,
//        R-NVR-4:判定树 detail 逐条查看)、手动检测。
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { useEffect, useState } from 'react';
import { Card, Dot, Empty, ErrorBar, useParams, type LayeredError } from '@gd/ui-kit';
import { nvCheck, nvDeviceChannels, nvResults, nvTimeline } from '../api';

/** @brief 设备详情页 */
export function DevicePage(): JSX.Element {
  const id = Number.parseInt(useParams()['id'] ?? '0', 10);
  const [channels, setChannels] = useState<{
    channels: Record<string, unknown>[];
    summary: { total: number; online: number; offline: number };
  } | null>(null);
  const [timeline, setTimeline] = useState<Record<string, unknown>[]>([]);
  const [results, setResults] = useState<Record<string, unknown>[]>([]);
  const [error, setError] = useState<LayeredError | null>(null);
  const [notice, setNotice] = useState<string>('');

  const load = async (): Promise<void> => {
    const [c, t, r] = await Promise.all([nvDeviceChannels(id), nvTimeline(id), nvResults(id)]);
    if (c.ok && c.data) setChannels(c.data);
    else setError(c.error);
    if (t.ok && t.data) setTimeline(t.data.timeline as unknown as Record<string, unknown>[]);
    if (r.ok && r.data) setResults(r.data.results as unknown as Record<string, unknown>[]);
  };
  useEffect(() => {
    void load();
  }, [id]);

  const check_cb = async (): Promise<void> => {
    const r = await nvCheck(id);
    if (r.ok) {
      setNotice('手动检测完成(source=manual,同样进入状态机与告警判定)');
      await load();
    } else setError(r.error);
  };

  return (
    <div>
      <ErrorBar error={error} />
      {notice ? <div className="gd-alert">{notice}</div> : null}
      <Card title={`设备 #${id} · 录像通道(与本体状态分开)`}>
        <p>
          <button className="gd-btn" onClick={check_cb}>
            立即手动检测本设备
          </button>
        </p>
        {!channels ? (
          <p>通道加载中…</p>
        ) : channels.summary.total === 0 ? (
          <Empty text="该设备暂无通道记录(首次检测后生成)" />
        ) : (
          <div>
            <p>
              汇总:{channels.summary.online}/{channels.summary.total} 在线
              {channels.summary.offline > 0 ? `,${channels.summary.offline} 路离线` : ''}
            </p>
            <table className="gd-table">
              <thead>
                <tr>
                  <th>通道号</th>
                  <th>名称</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {channels.channels.map((ch, i) => (
                  <tr key={i}>
                    <td>{String(ch['channel_no'] ?? '')}</td>
                    <td>{String(ch['name'] ?? '')}</td>
                    <td>
                      <Dot
                        kind={
                          ch['status'] === 'online'
                            ? 'ok'
                            : ch['status'] === 'offline'
                              ? 'danger'
                              : 'idle'
                        }
                        label={String(ch['status'] ?? '')}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Card title="检测明细(证据链可展开,R-NVR-4)">
        {results.length === 0 ? (
          <Empty text="暂无检测记录" />
        ) : (
          results.map((r, i) => (
            <details key={i} style={{ borderBottom: '1px solid var(--gd-line)', padding: '6px 0' }}>
              <summary>
                <Dot
                  kind={
                    r['status'] === 'online' ? 'ok' : r['status'] === 'offline' ? 'danger' : 'warn'
                  }
                  label={`${String(r['status'])} · ${String(r['source'])} · ${new Date(String(r['checked_at'])).toLocaleString()} · ${String(r['latency_ms'])}ms`}
                />
              </summary>
              <p className="gd-help" style={{ margin: '6px 0 0 14px' }}>
                判定证据:{String(r['detail'] ?? '(无)')}
              </p>
            </details>
          ))
        )}
      </Card>
      <Card title="统一时间线(状态跃迁 + 通道跃迁)">
        {timeline.length === 0 ? (
          <Empty text="暂无跃迁记录" />
        ) : (
          <table className="gd-table">
            <thead>
              <tr>
                <th>时间(本地)</th>
                <th>类型</th>
                <th>通道</th>
                <th>跃迁</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              {timeline.map((t, i) => (
                <tr key={i}>
                  <td>{new Date(String(t['occurred_at'])).toLocaleString()}</td>
                  <td>{t['event_type'] === 'channel_change' ? '通道' : '本体'}</td>
                  <td>{t['channel_no'] === null ? '—' : String(t['channel_no'])}</td>
                  <td>
                    {String(t['from_status'])} → {String(t['to_status'])}
                  </td>
                  <td>{String(t['detail'] ?? '')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
