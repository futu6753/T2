// =============================================================================
// @file  Settings.tsx  设置页:ui-kit SettingsPage 同构复用 + 渠道就绪度。
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { useEffect, useState } from 'react';
import { Card, SettingsPage, Dot } from '@gd/ui-kit';
import { nvChannelsReadiness } from '../api';

/** @brief nvr 设置页 */
export function NvrSettingsPage(): JSX.Element {
  const [channels, setChannels] = useState<Record<string, unknown>[]>([]);
  useEffect(() => {
    let alive = true;
    void nvChannelsReadiness().then((r) => {
      if (alive && r.ok && r.data) setChannels(r.data.channels);
    });
    return () => {
      alive = false;
    };
  }, []);
  return (
    <div>
      <Card title="通知渠道就绪度">
        {channels.length === 0 ? (
          <p className="gd-help">渠道信息获取中或未配置(Webhook / 阿里云 RPC)…</p>
        ) : (
          <table className="gd-table">
            <thead>
              <tr>
                <th>渠道</th>
                <th>就绪</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              {channels.map((c, i) => (
                <tr key={i}>
                  <td>{String(c['name'] ?? c['channel'] ?? '')}</td>
                  <td>
                    <Dot kind={c['ready'] ? 'ok' : 'idle'} label={c['ready'] ? '就绪' : '未配置'} />
                  </td>
                  <td className="gd-help">{String(c['detail'] ?? c['reason'] ?? '')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <SettingsPage />
    </div>
  );
}
