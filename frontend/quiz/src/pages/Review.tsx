// =============================================================================
// @file  Review.tsx
// @brief "今日复习"队列(R-QZ-1:SM-2 变体分层排期,到期优先、逾期靠前)。
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { useEffect, useState } from 'react';
import { Card, Empty, ErrorBar, useNavigate, type LayeredError } from '@gd/ui-kit';
import { qzReviewToday } from '../api';

/** @brief 今日复习页 */
export function ReviewPage(): JSX.Element {
  const [queue, setQueue] = useState<Record<string, unknown>[]>([]);
  const [error, setError] = useState<LayeredError | null>(null);
  const [loaded, setLoaded] = useState<boolean>(false);
  const nav = useNavigate();

  useEffect(() => {
    let alive = true;
    void qzReviewToday().then((r) => {
      if (!alive) return;
      if (r.ok && r.data) setQueue(r.data.queue as unknown as Record<string, unknown>[]);
      else setError(r.error);
      setLoaded(true);
    });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <Card title={`今日复习队列(${queue.length} 题,逾期靠前)`}>
      <ErrorBar error={error} />
      {!loaded ? (
        <p>队列加载中…</p>
      ) : queue.length === 0 ? (
        <Empty text="今天没有到期的复习——按计划推进即可" />
      ) : (
        <table className="gd-table">
          <thead>
            <tr>
              <th>题号</th>
              <th>到期时间(本地)</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {queue.map((it, i) => {
              const qno = Number(it['qno'] ?? 0);
              const due = typeof it['due_at'] === 'string' ? (it['due_at'] as string) : '';
              return (
                <tr key={`${qno}-${i}`}>
                  <td>{qno}</td>
                  <td>{due ? new Date(due).toLocaleString() : '已到期'}</td>
                  <td>
                    <button className="gd-btn ghost" onClick={() => nav(`/app/q/${qno}`)}>
                      去复习
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Card>
  );
}
