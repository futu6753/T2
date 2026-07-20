// =============================================================================
// @file  Overview.tsx
// @brief 总览:四态摘要卡、设备列表(本体状态与通道状态分离展示,
//        「录像机在线」措辞,02-C1)、手动巡检入口。
// @author 港电实验室平台组
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { useEffect, useState } from 'react';
import { Card, Dot, Empty, ErrorBar, useNavigate, type LayeredError } from '@gd/ui-kit';
import { nvOverview, nvPatrolRun, nvStatusDevices, type Device, type Overview } from '../api';

const STATUS_LABEL: Record<string, string> = {
  online: '录像机在线',
  offline: '录像机离线',
  unchecked: '未检测',
  auth_failed: '认证失败',
  service_fault: '服务故障',
  degraded: '在线(通道异常)',
  timeout: '超时',
  unknown: '未知',
};

/** @brief 状态 → 舷窗点色 */
function dotKind(status: string): 'ok' | 'warn' | 'danger' | 'idle' {
  if (status === 'online') return 'ok';
  if (status === 'offline') return 'danger';
  if (status === 'unchecked' || status === 'unknown') return 'idle';
  return 'warn';
}

/** @brief 总览页 */
export function OverviewPage(): JSX.Element {
  const [ov, setOv] = useState<Overview | null>(null);
  const [devices, setDevices] = useState<Device[]>([]);
  const [filter, setFilter] = useState<string>('');
  const [error, setError] = useState<LayeredError | null>(null);
  const [notice, setNotice] = useState<string>('');
  const nav = useNavigate();

  const load = async (statusFilter: string): Promise<void> => {
    const [o, d] = await Promise.all([nvOverview(), nvStatusDevices(statusFilter, '')]);
    if (o.ok && o.data) setOv(o.data);
    else setError(o.error);
    if (d.ok && d.data) setDevices(d.data.devices);
    else setError(d.error);
  };

  useEffect(() => {
    void load(filter);
    const timer = window.setInterval(() => void load(filter), 15000);
    return () => window.clearInterval(timer); // 卸载即清理(H11 §四.8)
  }, [filter]);

  const patrol_cb = async (): Promise<void> => {
    const r = await nvPatrolRun();
    if (r.ok) {
      setNotice('巡检已触发(互斥保护:进行中则忽略)');
      await load(filter);
    } else setError(r.error);
  };

  return (
    <div>
      <ErrorBar error={error} />
      {notice ? <div className="gd-alert">{notice}</div> : null}
      <Card title="运行摘要">
        {!ov ? (
          <p>摘要加载中…</p>
        ) : (
          <p style={{ display: 'flex', gap: 18, flexWrap: 'wrap', margin: 0 }}>
            <span>
              <Dot kind="ok" /> 在线 <strong>{ov.summary.online}</strong>
            </span>
            <span>
              <Dot kind="danger" /> 离线 <strong>{ov.summary.offline}</strong>
            </span>
            <span>
              <Dot kind="warn" /> 异常 <strong>{ov.summary.abnormal}</strong>
            </span>
            <span>
              <Dot kind="idle" /> 未检测 <strong>{ov.summary.unchecked}</strong>
            </span>
            <span className="gd-badge amber">活动告警 {ov.active_alerts}</span>
            <span className="gd-badge">
              巡检:{ov.patrol.running ? '进行中' : '空闲'}
              {ov.patrol.next_run_at
                ? ` · 下次 ${new Date(ov.patrol.next_run_at).toLocaleTimeString()}`
                : ''}
            </span>
            <button className="gd-btn ghost" onClick={patrol_cb}>
              立即巡检一轮
            </button>
          </p>
        )}
      </Card>
      <Card title="设备列表(本体与通道状态分开展示)">
        <label>
          按本体状态筛选:
          <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="状态筛选">
            <option value="">全部</option>
            {Object.entries(STATUS_LABEL).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
        {devices.length === 0 ? (
          <Empty text="暂无设备(可在设置区台账中登记)" />
        ) : (
          <table className="gd-table">
            <thead>
              <tr>
                <th>名称</th>
                <th>类型</th>
                <th>场区/楼栋</th>
                <th>本体状态</th>
                <th>录像通道</th>
                <th>活动告警</th>
              </tr>
            </thead>
            <tbody>
              {devices.map((d) => {
                const st = d.state?.status ?? 'unknown';
                return (
                  <tr
                    key={d.id}
                    style={{ cursor: 'pointer' }}
                    onClick={() => nav(`/app/devices/${d.id}`)}
                  >
                    <td>{d.name}</td>
                    <td>{d.kind === 'push' ? '推送设备' : '录像机'}</td>
                    <td>
                      {d.region} / {d.station}
                    </td>
                    <td>
                      <Dot kind={dotKind(st)} label={STATUS_LABEL[st] ?? st} />
                    </td>
                    <td>
                      {d.channels
                        ? `${d.channels.online}/${d.channels.total} 在线${d.channels.offline > 0 ? `(${d.channels.offline} 离线)` : ''}`
                        : '—'}
                    </td>
                    <td>
                      {d.active_alert
                        ? `${d.active_alert.scope === 'channel' ? '通道' : '本体'}·${d.active_alert.trigger_status}`
                        : '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
