// =============================================================================
// @file  Progress.tsx
// @brief 进度页 + 掌握度概览(R-QZ-2):做题数/正确率/错题量/整数能力评分;
//        邻域采样偏好开关(默认关)与邻域出题入口。
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { useEffect, useState } from 'react';
import { Card, ErrorBar, useNavigate, type LayeredError } from '@gd/ui-kit';
import { qzBankSummary, qzNext, qzPrefs, qzProgress, qzSetPrefs } from '../api';

interface ProgressData {
  attempted: number;
  correct_total: number;
  wrong_total: number;
  wrongbook: number;
  rating: number;
  games: number;
}

/** @brief 进度与掌握度页 */
export function ProgressPage(): JSX.Element {
  const [p, setP] = useState<ProgressData | null>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [elo, setElo] = useState<boolean>(false);
  const [error, setError] = useState<LayeredError | null>(null);
  const nav = useNavigate();

  useEffect(() => {
    let alive = true;
    void qzProgress().then((r) => {
      if (!alive) return;
      if (r.ok && r.data) setP(r.data);
      else setError(r.error);
    });
    void qzBankSummary().then((r) => {
      if (alive && r.ok && r.data) setTotal(r.data.total);
    });
    void qzPrefs().then((r) => {
      if (alive && r.ok && r.data) setElo(r.data.elo_sampling);
    });
    return () => {
      alive = false;
    };
  }, []);

  const toggleElo_cb = async (v: boolean): Promise<void> => {
    const r = await qzSetPrefs(v);
    if (r.ok && r.data) setElo(r.data.elo_sampling);
    else setError(r.error);
  };

  const neighborhood_cb = async (): Promise<void> => {
    const r = await qzNext('neighborhood');
    if (r.ok && r.data?.qno) nav(`/app/q/${r.data.qno}`);
    else setError(r.error);
  };

  const answers = p ? p.correct_total + p.wrong_total : 0;
  const rate = p && answers > 0 ? Math.round((p.correct_total / answers) * 100) : null;
  const coverage = p && total ? Math.round((p.attempted / total) * 100) : null;

  return (
    <div>
      <ErrorBar error={error} />
      <Card title="掌握度概览(R-QZ-2)">
        {!p ? (
          <p>进度加载中…</p>
        ) : (
          <table className="gd-table">
            <tbody>
              <tr>
                <th>已做题数</th>
                <td>
                  {p.attempted}
                  {total !== null ? ` / ${total}(覆盖 ${coverage}%)` : ''}
                </td>
              </tr>
              <tr>
                <th>累计作答正确率</th>
                <td>
                  {rate !== null
                    ? `${rate}%(对 ${p.correct_total} / 错 ${p.wrong_total})`
                    : '暂无作答'}
                </td>
              </tr>
              <tr>
                <th>错题本待巩固</th>
                <td>{p.wrongbook} 题</td>
              </tr>
              <tr>
                <th>能力评分(整数 ELO)</th>
                <td>
                  {p.rating}(共 {p.games} 次计分作答)
                </td>
              </tr>
            </tbody>
          </table>
        )}
      </Card>
      <Card title="按能力出题(邻域采样,默认关闭)">
        <label>
          <input
            type="checkbox"
            checked={elo}
            onChange={(e) => void toggleElo_cb(e.target.checked)}
          />{' '}
          开启邻域采样(按能力评分匹配相近难度的题)
        </label>
        <p>
          <button className="gd-btn" onClick={neighborhood_cb} disabled={!elo}>
            来一道匹配我能力的题
          </button>
        </p>
      </Card>
    </div>
  );
}
