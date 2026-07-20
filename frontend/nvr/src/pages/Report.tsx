// =============================================================================
// @file  Report.tsx  周报页:最新报告 + 降级原因展示(R-NVR-3)。
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { useEffect, useState } from 'react';
import { Card, Empty, ErrorBar, type LayeredError } from '@gd/ui-kit';
import { nvReportGenerate, nvReportLatest } from '../api';

/** @brief 周报页 */
export function ReportPage(): JSX.Element {
  const [report, setReport] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<LayeredError | null>(null);
  const [busy, setBusy] = useState<boolean>(false);
  const [missing, setMissing] = useState<boolean>(false);

  const load = async (): Promise<void> => {
    const r = await nvReportLatest();
    if (r.ok && r.data) {
      setReport(r.data);
      setMissing(false);
    } else if (r.status === 404) setMissing(true);
    else setError(r.error);
  };
  useEffect(() => {
    void load();
  }, []);

  const gen_cb = async (): Promise<void> => {
    setBusy(true);
    const r = await nvReportGenerate();
    setBusy(false);
    if (r.ok) await load();
    else setError(r.error);
  };

  const generatedBy = report ? String(report['generated_by'] ?? '') : '';
  const degradeReason = report ? String(report['reason'] ?? report['degrade_reason'] ?? '') : '';

  return (
    <div>
      <ErrorBar error={error} />
      <Card title="值守周报(事实层锚定)">
        <p>
          <button className="gd-btn" onClick={gen_cb} disabled={busy}>
            立即生成本周周报
          </button>
        </p>
        {missing ? (
          <Empty text="尚无周报,点上方按钮生成第一份" />
        ) : !report ? (
          <p>周报加载中…</p>
        ) : (
          <div>
            <p>
              <span className={generatedBy === 'template' ? 'gd-badge amber' : 'gd-badge'}>
                生成方式:{generatedBy === 'template' ? '确定性模板(已降级)' : generatedBy || '—'}
              </span>
              {generatedBy === 'template' && degradeReason ? (
                <span className="gd-help"> · 降级原因:{degradeReason}</span>
              ) : null}
            </p>
            <pre
              style={{
                whiteSpace: 'pre-wrap',
                fontFamily: 'var(--gd-font)',
                background: '#f7f9fb',
                padding: 12,
                borderRadius: 6,
              }}
            >
              {String(report['content'] ?? report['text'] ?? '')}
            </pre>
          </div>
        )}
      </Card>
    </div>
  );
}
