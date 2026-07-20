// =============================================================================
// @file  Wrongbook.tsx
// @brief 错题本(按账号隔离):按更新时间降序;「已掌握」移出错题本。
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { useEffect, useState } from 'react';
import { Card, Empty, ErrorBar, useNavigate, type LayeredError } from '@gd/ui-kit';
import { qzWrongClear, qzWrongbook } from '../api';

interface WrongItem {
  qno: number;
  qtype: string;
  color: string;
  stem: string;
  wrong_count: number;
  updated_at: string;
}

/** @brief 错题本页 */
export function WrongbookPage(): JSX.Element {
  const [items, setItems] = useState<WrongItem[]>([]);
  const [error, setError] = useState<LayeredError | null>(null);
  const nav = useNavigate();

  const load = async (): Promise<void> => {
    const r = await qzWrongbook();
    if (r.ok && r.data) setItems(r.data.wrongbook);
    else setError(r.error);
  };
  useEffect(() => {
    void load();
  }, []);

  const clear_cb = async (qno: number): Promise<void> => {
    const r = await qzWrongClear(qno);
    if (r.ok) await load();
    else setError(r.error);
  };

  return (
    <Card title={`错题本(${items.length} 题)`}>
      <ErrorBar error={error} />
      {items.length === 0 ? (
        <Empty text="错题本为空——保持住!" />
      ) : (
        <table className="gd-table">
          <thead>
            <tr>
              <th>题号</th>
              <th>题干</th>
              <th>错误次数</th>
              <th>最近作答(本地时间)</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {items.map((w) => (
              <tr key={w.qno}>
                <td>{w.qno}</td>
                <td style={{ cursor: 'pointer' }} onClick={() => nav(`/app/q/${w.qno}`)}>
                  {w.stem.length > 36 ? `${w.stem.slice(0, 36)}…` : w.stem}
                </td>
                <td>{w.wrong_count}</td>
                <td>{w.updated_at ? new Date(w.updated_at).toLocaleString() : '—'}</td>
                <td>
                  <button className="gd-btn ghost" onClick={() => void clear_cb(w.qno)}>
                    已掌握,移出
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
