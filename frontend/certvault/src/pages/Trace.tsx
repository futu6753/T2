// =============================================================================
// @file  Trace.tsx
// @brief 溯源识别(H11 §二):结果含引擎命中与 engine_errors 明示(06-E7)、
//        置信等级与投票明细展示、双引擎不一致时的告警样式(R-CV-3)、
//        撤销备案命中明示作废(R-CV-5)。
// @author 港电实验室平台组
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { useState } from 'react';
import { Card, ErrorBar, type LayeredError } from '@gd/ui-kit';
import { cvForm, MEDIA, type TraceResult } from '../api';

/** @brief 投票是否存在不一致(多引擎命中不同 tracer → 告警样式) */
function voteMismatch(vote: Record<string, string> | undefined): boolean {
  if (!vote) return false;
  const values = [...new Set(Object.values(vote))];
  return values.length > 1;
}

/** @brief 溯源识别页 */
export function TracePage(): JSX.Element {
  const [file, setFile] = useState<File | null>(null);
  const [medium, setMedium] = useState<string>('');
  const [result, setResult] = useState<TraceResult | null>(null);
  const [error, setError] = useState<LayeredError | null>(null);
  const [busy, setBusy] = useState<boolean>(false);

  const trace_cb = async (): Promise<void> => {
    if (!file) return;
    setBusy(true);
    setResult(null);
    const form = new FormData();
    form.set('file', file);
    if (medium) form.set('medium', medium);
    const r = await cvForm<TraceResult>('POST', '/trace', form);
    setBusy(false);
    if (r.ok && r.data) setResult(r.data);
    else setError(r.error);
  };

  const mismatch = voteMismatch(result?.vote_detail);

  return (
    <div>
      <ErrorBar error={error} />
      <Card title="上传疑似外泄文件">
        <label className="gd-field">
          <span>文件</span>
          <input
            type="file"
            accept="image/*"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </label>
        <label className="gd-field">
          <span>流转介质(可选,辅助引擎排序)</span>
          <select value={medium} onChange={(e) => setMedium(e.target.value)}>
            <option value="">未知</option>
            {MEDIA.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
        <button className="gd-btn" onClick={trace_cb} disabled={busy || !file}>
          {busy ? '识别中…' : '开始溯源识别'}
        </button>
      </Card>
      {result ? (
        <Card title="识别结果">
          {result.found ? (
            <div>
              <div className={mismatch ? 'gd-alert error' : 'gd-alert'}>
                {mismatch ? (
                  <strong>⚠ 组合引擎投票不一致——请人工复核,已同步告警与审计(R-CV-3)</strong>
                ) : (
                  <strong>命中备案 {result.tracer_id}</strong>
                )}
                <p style={{ marginBottom: 0 }}>{result.message ?? ''}</p>
              </div>
              {result.revoked ? (
                <div className="gd-alert warn">
                  <strong>该备案已作废(撤销)</strong>——命中信息仅供追溯参考,发证效力已撤销(R-CV-5)。
                </div>
              ) : null}
              <table className="gd-table">
                <tbody>
                  <tr>
                    <th>命中引擎</th>
                    <td>{result.engine_name ?? result.engine ?? '—'}</td>
                  </tr>
                  <tr>
                    <th>置信等级</th>
                    <td>
                      <span className={result.confidence === '高' ? 'gd-badge' : 'gd-badge amber'}>
                        {result.confidence ?? '单引擎'}
                      </span>
                    </td>
                  </tr>
                  {result.vote_detail ? (
                    <tr>
                      <th>投票明细</th>
                      <td>
                        {Object.entries(result.vote_detail).map(([eng, tid]) => (
                          <div key={eng} style={{ fontFamily: 'var(--gd-mono)' }}>
                            {eng} → {tid}
                          </div>
                        ))}
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="gd-alert warn">
              <strong>未命中任何备案</strong>
              <p style={{ marginBottom: 0 }}>{result.message ?? ''}</p>
            </div>
          )}
          <h3>引擎尝试明细(06-E7:故障引擎明示)</h3>
          <table className="gd-table">
            <thead>
              <tr>
                <th>引擎</th>
                <th>结果</th>
              </tr>
            </thead>
            <tbody>
              {(result.tried_engines ?? []).map((eng) => (
                <tr key={eng}>
                  <td>{eng}</td>
                  <td>
                    {result.engine_errors && result.engine_errors[eng] ? (
                      <span style={{ color: 'var(--gd-danger)' }}>
                        故障:{result.engine_errors[eng]}
                      </span>
                    ) : result.vote_detail && result.vote_detail[eng] ? (
                      '命中'
                    ) : (
                      '未命中'
                    )}
                  </td>
                </tr>
              ))}
              {Object.entries(result.engine_errors ?? {})
                .filter(([eng]) => !(result.tried_engines ?? []).includes(eng))
                .map(([eng, msg]) => (
                  <tr key={eng}>
                    <td>{eng}</td>
                    <td style={{ color: 'var(--gd-danger)' }}>故障:{msg}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </Card>
      ) : null}
    </div>
  );
}
