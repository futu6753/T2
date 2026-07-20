// =============================================================================
// @file  Records.tsx
// @brief 备案台账(H11 §二):列表(48bit 用量随带)、独立备案登记、
//        撤销操作与作废标记展示(R-CV-5:撤销后溯源仍命中但明示作废)。
// @author 港电实验室平台组
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { useEffect, useState } from 'react';
import { Card, Empty, ErrorBar, type LayeredError } from '@gd/ui-kit';
import { cvForm, cvGet, MEDIA, type RecordItem } from '../api';

interface IdSpace {
  used: number;
  capacity_bits: number;
  utilization: number;
}

/** @brief 备案台账页 */
export function RecordsPage(): JSX.Element {
  const [records, setRecords] = useState<RecordItem[]>([]);
  const [idSpace, setIdSpace] = useState<IdSpace | null>(null);
  const [error, setError] = useState<LayeredError | null>(null);
  const [notice, setNotice] = useState<string>('');
  // 独立备案表单
  const [saRecipient, setSaRecipient] = useState<string>('');
  const [saPurpose, setSaPurpose] = useState<string>('');
  const [saMedium, setSaMedium] = useState<string>('electronic');
  const [saFile, setSaFile] = useState<File | null>(null);
  const [busy, setBusy] = useState<boolean>(false);

  const load = async (): Promise<void> => {
    const r = await cvGet<{ records: RecordItem[]; id_space: IdSpace }>('/records');
    if (r.ok && r.data) {
      setRecords(r.data.records);
      setIdSpace(r.data.id_space);
    } else setError(r.error);
  };
  useEffect(() => {
    void load();
  }, []);

  const standalone_cb = async (): Promise<void> => {
    setBusy(true);
    const form = new FormData();
    form.set('recipient', saRecipient);
    form.set('purpose', saPurpose);
    form.set('medium', saMedium);
    if (saFile) form.set('file', saFile);
    const r = await cvForm('POST', '/records/standalone', form);
    setBusy(false);
    if (r.ok) {
      setNotice('独立备案已登记(不生成水印,不可溯源)');
      setSaRecipient('');
      setSaPurpose('');
      setSaFile(null);
      await load();
    } else setError(r.error);
  };

  const revoke_cb = async (tracer: string): Promise<void> => {
    const r = await cvForm('POST', `/records/${tracer}/revoke`, new FormData());
    if (r.ok) {
      setNotice(`备案 ${tracer} 已撤销:溯源仍会命中,但结果将明示作废`);
      await load();
    } else setError(r.error);
  };

  return (
    <div>
      <ErrorBar error={error} />
      {notice ? <div className="gd-alert">{notice}</div> : null}
      <Card title="独立备案登记(外来文件仅登记,不生成水印)">
        <label className="gd-field">
          <span>交付对象</span>
          <input value={saRecipient} onChange={(e) => setSaRecipient(e.target.value)} />
        </label>
        <label className="gd-field">
          <span>用途</span>
          <input value={saPurpose} onChange={(e) => setSaPurpose(e.target.value)} />
        </label>
        <label className="gd-field">
          <span>流转介质</span>
          <select value={saMedium} onChange={(e) => setSaMedium(e.target.value)}>
            {MEDIA.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
        <label className="gd-field">
          <span>文件(可选,登记指纹)</span>
          <input type="file" onChange={(e) => setSaFile(e.target.files?.[0] ?? null)} />
        </label>
        <button className="gd-btn" onClick={standalone_cb} disabled={busy}>
          登记独立备案
        </button>
      </Card>
      <Card
        title={`备案台账(${records.length} 条${
          idSpace
            ? ` · 48bit ID 空间已用 ${idSpace.used},占用率 ${(idSpace.utilization * 100).toExponential(2)}%`
            : ''
        })`}
      >
        {records.length === 0 ? (
          <Empty text="暂无备案" />
        ) : (
          <table className="gd-table">
            <thead>
              <tr>
                <th>备案号</th>
                <th>类型</th>
                <th>对象 / 用途</th>
                <th>介质</th>
                <th>登记时间(本地)</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {records.map((r) => (
                <tr key={r.id} style={r.revoked_at ? { opacity: 0.65 } : undefined}>
                  <td style={{ fontFamily: 'var(--gd-mono)' }}>{r.tracer_id}</td>
                  <td>{r.is_standalone ? '独立备案' : '水印发证'}</td>
                  <td>
                    {r.recipient || '未登记'} / {r.purpose || '未登记'}
                  </td>
                  <td>{MEDIA.find((m) => m.value === r.medium)?.label ?? r.medium ?? '—'}</td>
                  <td>{r.created_at ? new Date(r.created_at).toLocaleString() : '—'}</td>
                  <td>
                    {r.revoked_at ? (
                      <span
                        className="gd-badge"
                        style={{ color: 'var(--gd-danger)', borderColor: 'var(--gd-danger)' }}
                      >
                        已作废({r.revoked_by})
                      </span>
                    ) : (
                      <span className="gd-badge">有效</span>
                    )}
                  </td>
                  <td>
                    {!r.revoked_at ? (
                      <button className="gd-btn danger" onClick={() => void revoke_cb(r.tracer_id)}>
                        撤销
                      </button>
                    ) : (
                      '—'
                    )}
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
