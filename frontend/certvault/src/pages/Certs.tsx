// =============================================================================
// @file  Certs.tsx
// @brief 证件库(H11 §二:多版本管理):列表分组展示同类证件多版本、上传新
//        版本、预览(受保护图经 Bearer 拉取为 objectURL)、删除。
// @author 港电实验室平台组
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { useEffect, useState } from 'react';
import { Card, Empty, ErrorBar, type LayeredError } from '@gd/ui-kit';
import { cvForm, cvGet, cvImageUrl } from '../api';

interface CertItem {
  id: number;
  cert_type: string;
  label: string;
  version: number;
  created_at: string;
  [k: string]: unknown;
}

/** @brief 证件库页 */
export function CertsPage(): JSX.Element {
  const [certs, setCerts] = useState<CertItem[]>([]);
  const [error, setError] = useState<LayeredError | null>(null);
  const [preview, setPreview] = useState<{ id: number; url: string } | null>(null);
  const [certType, setCertType] = useState<string>('');
  const [label, setLabel] = useState<string>('');
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState<boolean>(false);

  const load = async (): Promise<void> => {
    const r = await cvGet<{ certs: CertItem[] }>('/certs');
    if (r.ok && r.data) setCerts(r.data.certs);
    else setError(r.error);
  };
  useEffect(() => {
    void load();
    return () => {
      // 卸载释放 objectURL(生命周期清理,H11 §四.8)
      setPreview((p) => {
        if (p) URL.revokeObjectURL(p.url);
        return null;
      });
    };
  }, []);

  const upload_cb = async (): Promise<void> => {
    if (!file) return;
    setBusy(true);
    const form = new FormData();
    form.set('cert_type', certType || '通用证件');
    form.set('label', label);
    form.set('file', file);
    const r = await cvForm('POST', '/certs/upload', form);
    setBusy(false);
    if (r.ok) {
      setFile(null);
      setLabel('');
      await load();
    } else setError(r.error);
  };

  const preview_cb = async (id: number): Promise<void> => {
    const url = await cvImageUrl(`/certs/${id}/image`);
    setPreview((p) => {
      if (p) URL.revokeObjectURL(p.url);
      return url ? { id, url } : null;
    });
  };

  const del_cb = async (id: number): Promise<void> => {
    const r = await cvForm('DELETE', `/certs/${id}`, null);
    if (r.ok) await load();
    else setError(r.error);
  };

  // 多版本分组:同 cert_type 归并、版本降序
  const groups = new Map<string, CertItem[]>();
  for (const c of certs) {
    const key = c.cert_type || '未分类';
    const arr = groups.get(key) ?? [];
    arr.push(c);
    groups.set(key, arr);
  }

  return (
    <div>
      <ErrorBar error={error} />
      <Card title="上传证件(同类型自动记为新版本)">
        <label className="gd-field">
          <span>证件类型</span>
          <input
            value={certType}
            onChange={(e) => setCertType(e.target.value)}
            placeholder="如:出入证"
          />
        </label>
        <label className="gd-field">
          <span>备注标签(可选)</span>
          <input value={label} onChange={(e) => setLabel(e.target.value)} />
        </label>
        <label className="gd-field">
          <span>证件图片文件</span>
          <input
            type="file"
            accept="image/*"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </label>
        <button className="gd-btn" onClick={upload_cb} disabled={busy || !file}>
          上传
        </button>
      </Card>
      {groups.size === 0 ? (
        <Card title="证件库">
          <Empty text="尚无证件,请先上传" />
        </Card>
      ) : (
        [...groups.entries()].map(([type, items]) => (
          <Card key={type} title={`${type}(${items.length} 个版本)`}>
            <table className="gd-table">
              <thead>
                <tr>
                  <th>版本</th>
                  <th>标签</th>
                  <th>入库时间(本地)</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {[...items]
                  .sort((a, b) => b.version - a.version)
                  .map((c) => (
                    <tr key={c.id}>
                      <td>v{c.version}</td>
                      <td>{c.label || '—'}</td>
                      <td>{c.created_at ? new Date(c.created_at).toLocaleString() : '—'}</td>
                      <td>
                        <button className="gd-btn ghost" onClick={() => void preview_cb(c.id)}>
                          预览
                        </button>{' '}
                        <button className="gd-btn danger" onClick={() => void del_cb(c.id)}>
                          删除
                        </button>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </Card>
        ))
      )}
      {preview ? (
        <Card title={`预览(证件 #${preview.id})`}>
          <img
            src={preview.url}
            alt="证件预览"
            style={{ maxWidth: '100%', border: '1px solid var(--gd-line)' }}
          />
        </Card>
      ) : null}
    </div>
  );
}
