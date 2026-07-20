// =============================================================================
// @file  DeadLetters.tsx
// @brief 死信页(13-R-AD-3):导出 JSON Lines → 检视/人工修复 → 重放
//        (下游恰一次;已投递/重复混入 skip)。
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { useState } from 'react';
import { apiGet, apiPost, Card, ErrorBar, type LayeredError } from '@gd/ui-kit';

/** @brief 死信导出/重放页 */
export function DeadLettersPage(): JSX.Element {
  const [jsonl, setJsonl] = useState<string>('');
  const [count, setCount] = useState<number | null>(null);
  const [replay, setReplay] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<LayeredError | null>(null);

  const export_cb = async (): Promise<void> => {
    const r = await apiGet<{ count: number; jsonl: string }>('/api/v1/deadletters/export');
    if (r.ok && r.data) {
      setCount(r.data.count);
      setJsonl(r.data.jsonl);
      setReplay(null);
    } else setError(r.error);
  };

  const replay_cb = async (useEdited: boolean): Promise<void> => {
    const body = useEdited && jsonl.trim() !== '' ? { jsonl } : {};
    const r = await apiPost<Record<string, unknown>>('/api/v1/deadletters/replay', body);
    if (r.ok && r.data) setReplay(r.data);
    else setError(r.error);
  };

  return (
    <div>
      <ErrorBar error={error} />
      <Card title="① 导出死信(JSON Lines,一行一事件)">
        <button className="gd-btn" onClick={export_cb}>
          导出当前死信队列
        </button>
        {count !== null ? (
          <span className="gd-badge" style={{ marginLeft: 8 }}>
            共 {count} 条
          </span>
        ) : null}
      </Card>
      <Card title="② 检视 / 人工修复(可直接编辑后重放)">
        <textarea
          value={jsonl}
          onChange={(e) => setJsonl(e.target.value)}
          rows={10}
          style={{ width: '100%', fontFamily: 'var(--gd-mono)', fontSize: 'var(--gd-fs-xs)' }}
          aria-label="死信 JSONL"
          placeholder="先导出;每行一个 JSON 事件,可修复字段后重放"
        />
      </Card>
      <Card title="③ 重放(下游恰一次;已投递与重复混入自动 skip)">
        <button className="gd-btn" onClick={() => void replay_cb(true)}>
          重放编辑框内容
        </button>{' '}
        <button className="gd-btn ghost" onClick={() => void replay_cb(false)}>
          直接重放当前队列
        </button>
        {replay ? (
          <pre style={{ background: '#f7f9fb', padding: 10, borderRadius: 6, marginTop: 10 }}>
            {JSON.stringify(replay, null, 2)}
          </pre>
        ) : null}
      </Card>
    </div>
  );
}
