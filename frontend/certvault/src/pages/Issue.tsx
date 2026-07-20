// =============================================================================
// @file  Issue.tsx
// @brief 生成水印件(H11 §二):对象/用途拼接文字可手改、引擎选择、浓度/密度/
//        扭曲/强度/分辨率参数面板、「保存为默认选项」;流转介质下拉与引擎
//        推荐理由展示、可覆盖(R-CV-2)。结果含预览与备案号。
//        注:仅界面参数默认值入 localStorage(非凭据,不违 H11 §四.3)。
// @author 港电实验室平台组
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { useEffect, useState } from 'react';
import { Card, ErrorBar, type LayeredError } from '@gd/ui-kit';
import { cvForm, cvGet, MEDIA, type EngineInfo } from '../api';

const DEFAULTS_KEY = 'gd_cv_issue_defaults';

interface IssueParams {
  opacity: number;
  density: number;
  distort_amplitude: number;
  wm_strength: number;
  export_width: number;
  guilloche: boolean;
  smart_anchor: boolean;
}

const FACTORY: IssueParams = {
  opacity: 0.18,
  density: 1.0,
  distort_amplitude: 1.2,
  wm_strength: -1,
  export_width: 0,
  guilloche: true,
  smart_anchor: true,
};

/** @brief 读界面参数默认(仅数值参数,非凭据) */
function loadDefaults(): IssueParams {
  try {
    const raw = window.localStorage.getItem(DEFAULTS_KEY);
    if (!raw) return { ...FACTORY };
    return { ...FACTORY, ...(JSON.parse(raw) as Partial<IssueParams>) };
  } catch {
    return { ...FACTORY };
  }
}

interface Recommendation {
  engine: string;
  strength: number;
  reason: string;
  fallback?: boolean;
}

/** @brief 发证页 */
export function IssuePage(): JSX.Element {
  const [certs, setCerts] = useState<{ id: number; cert_type: string; version: number }[]>([]);
  const [engines, setEngines] = useState<EngineInfo[]>([]);
  const [defaultEngine, setDefaultEngine] = useState<string>('');
  const [certId, setCertId] = useState<number>(0);
  const [engine, setEngine] = useState<string>('');
  const [medium, setMedium] = useState<string>('electronic');
  const [rec, setRec] = useState<Recommendation | null>(null);
  const [recipient, setRecipient] = useState<string>('');
  const [purpose, setPurpose] = useState<string>('');
  const [validity, setValidity] = useState<string>('当日有效');
  const [textOverride, setTextOverride] = useState<string>('');
  const [params, setParams] = useState<IssueParams>(loadDefaults);
  const [error, setError] = useState<LayeredError | null>(null);
  const [busy, setBusy] = useState<boolean>(false);
  const [result, setResult] = useState<{
    issuance_id: number;
    tracer_id: string;
    engine_name: string;
    visible_text: string;
    image_b64: string;
    size: number;
  } | null>(null);

  useEffect(() => {
    let alive = true;
    void cvGet<{ certs: { id: number; cert_type: string; version: number }[] }>('/certs').then(
      (r) => {
        if (alive && r.ok && r.data) {
          setCerts(r.data.certs);
          if (r.data.certs.length > 0) setCertId(r.data.certs[0].id);
        } else if (alive) setError(r.error);
      },
    );
    void cvGet<{ engines: EngineInfo[]; default: string }>('/engines').then((r) => {
      if (alive && r.ok && r.data) {
        setEngines(r.data.engines);
        setDefaultEngine(r.data.default);
        setEngine((e) => e || r.data!.default);
      }
    });
    return () => {
      alive = false;
    };
  }, []);

  // 介质/证件变化 → 拉推荐(理由展示、可覆盖,R-CV-2)
  useEffect(() => {
    let alive = true;
    const cert = certs.find((c) => c.id === certId);
    const q = `cert_type=${encodeURIComponent(cert?.cert_type ?? '')}&medium=${encodeURIComponent(medium)}`;
    void cvGet<Recommendation>(`/engines/recommend?${q}`).then((r) => {
      if (alive && r.ok && r.data) setRec(r.data);
    });
    return () => {
      alive = false;
    };
  }, [medium, certId, certs]);

  const saveDefaults_cb = (): void => {
    window.localStorage.setItem(DEFAULTS_KEY, JSON.stringify(params));
  };

  const num_cb = (key: keyof IssueParams) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = Number.parseFloat(e.target.value);
    setParams((p) => ({ ...p, [key]: Number.isFinite(v) ? v : 0 }));
  };

  const issue_cb = async (): Promise<void> => {
    setBusy(true);
    setResult(null);
    const form = new FormData();
    form.set('cert_id', String(certId));
    form.set('engine', engine);
    form.set('medium', medium);
    form.set('recipient', recipient);
    form.set('purpose', purpose);
    form.set('validity', validity);
    if (textOverride) form.set('visible_text_override', textOverride);
    form.set('opacity', String(params.opacity));
    form.set('density', String(params.density));
    form.set('distort_amplitude', String(params.distort_amplitude));
    form.set('wm_strength', String(params.wm_strength));
    form.set('export_width', String(params.export_width));
    form.set('guilloche', params.guilloche ? '1' : '0');
    form.set('smart_anchor', params.smart_anchor ? '1' : '0');
    const r = await cvForm<typeof result>('POST', '/issue', form);
    setBusy(false);
    if (r.ok && r.data) setResult(r.data);
    else setError(r.error);
  };

  const autoText = `限${recipient || '指定对象'}${purpose || '指定用途'} ${validity}`;

  return (
    <div>
      <ErrorBar error={error} />
      <Card title="① 选择证件与流转介质">
        <label className="gd-field">
          <span>证件</span>
          <select value={certId} onChange={(e) => setCertId(Number(e.target.value))}>
            {certs.map((c) => (
              <option key={c.id} value={c.id}>
                #{c.id} {c.cert_type} v{c.version}
              </option>
            ))}
          </select>
        </label>
        <label className="gd-field">
          <span>流转介质(影响引擎推荐,R-CV-2)</span>
          <select value={medium} onChange={(e) => setMedium(e.target.value)}>
            {MEDIA.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
        {rec ? (
          <div className="gd-alert">
            推荐引擎:<strong>{rec.engine}</strong>(建议强度 {rec.strength})—— {rec.reason}
            {rec.fallback ? '(推荐器降级,已回退默认引擎)' : ''}
            {engine !== rec.engine ? (
              <button
                className="gd-btn ghost"
                style={{ marginLeft: 8 }}
                onClick={() => setEngine(rec.engine)}
              >
                采纳推荐
              </button>
            ) : null}
          </div>
        ) : null}
        <label className="gd-field">
          <span>引擎(可覆盖推荐;不可用引擎已标注)</span>
          <select value={engine} onChange={(e) => setEngine(e.target.value)}>
            {engines.map((en) => (
              <option key={en.id} value={en.id} disabled={!en.available}>
                {en.name}
                {en.id === defaultEngine ? '(默认)' : ''}
                {en.available ? '' : ' — 不可用'}
              </option>
            ))}
          </select>
        </label>
      </Card>
      <Card title="② 明水印文字(对象/用途拼接,可手改)">
        <label className="gd-field">
          <span>交付对象</span>
          <input
            value={recipient}
            onChange={(e) => setRecipient(e.target.value)}
            placeholder="如:XX 银行"
          />
        </label>
        <label className="gd-field">
          <span>用途</span>
          <input
            value={purpose}
            onChange={(e) => setPurpose(e.target.value)}
            placeholder="如:办理开户"
          />
        </label>
        <label className="gd-field">
          <span>有效期文案</span>
          <input value={validity} onChange={(e) => setValidity(e.target.value)} />
        </label>
        <label className="gd-field">
          <span>成品文字(留空 = 自动拼接:{autoText})</span>
          <input
            value={textOverride}
            onChange={(e) => setTextOverride(e.target.value)}
            placeholder={autoText}
          />
        </label>
      </Card>
      <Card title="③ 参数面板">
        <label className="gd-field">
          <span>明水印浓度 opacity(0–1)</span>
          <input
            type="number"
            step="0.01"
            min="0"
            max="1"
            value={params.opacity}
            onChange={num_cb('opacity')}
          />
        </label>
        <label className="gd-field">
          <span>平铺密度 density</span>
          <input
            type="number"
            step="0.1"
            min="0.2"
            value={params.density}
            onChange={num_cb('density')}
          />
        </label>
        <label className="gd-field">
          <span>微扭曲幅度 distort_amplitude</span>
          <input
            type="number"
            step="0.1"
            min="0"
            value={params.distort_amplitude}
            onChange={num_cb('distort_amplitude')}
          />
        </label>
        <label className="gd-field">
          <span>暗水印强度 wm_strength(-1 = 按引擎推荐)</span>
          <input
            type="number"
            step="0.5"
            value={params.wm_strength}
            onChange={num_cb('wm_strength')}
          />
        </label>
        <label className="gd-field">
          <span>导出分辨率宽度 export_width(0 = 原尺寸)</span>
          <input
            type="number"
            step="1"
            min="0"
            value={params.export_width}
            onChange={num_cb('export_width')}
          />
        </label>
        <label className="gd-field">
          <span>
            <input
              type="checkbox"
              checked={params.guilloche}
              onChange={(e) => setParams((p) => ({ ...p, guilloche: e.target.checked }))}
            />{' '}
            团花底纹
          </span>
        </label>
        <label className="gd-field">
          <span>
            <input
              type="checkbox"
              checked={params.smart_anchor}
              onChange={(e) => setParams((p) => ({ ...p, smart_anchor: e.target.checked }))}
            />{' '}
            智能锚定(避开人脸/关键字段)
          </span>
        </label>
        <button className="gd-btn ghost" onClick={saveDefaults_cb}>
          保存为默认选项
        </button>
      </Card>
      <Card title="④ 生成">
        <button className="gd-btn" onClick={issue_cb} disabled={busy || certId === 0}>
          {busy ? '生成中…' : '生成水印件并登记备案'}
        </button>
      </Card>
      {result ? (
        <Card title={`已生成 · 备案号 ${result.tracer_id}`}>
          <p>
            引擎:{result.engine_name} · 成品文字:{result.visible_text} · 体积{' '}
            {Math.round(result.size / 1024)} KB
          </p>
          <img
            src={`data:image/jpeg;base64,${result.image_b64}`}
            alt="水印件预览"
            style={{ maxWidth: '100%', border: '1px solid var(--gd-line)' }}
          />
        </Card>
      ) : null}
    </div>
  );
}
