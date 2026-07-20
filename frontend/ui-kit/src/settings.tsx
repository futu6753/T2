// =============================================================================
// @file  settings.tsx
// @brief schema 驱动设置页(H11 §三 / H03 §8 界面承接,各子系统同构):
//        每项显示当前生效值、来源层(env 锁定/后台/文件/默认)与等保下限提示;
//        保存整体提交并把后端拒绝原因逐项映射回表单;需重启项明示;
//        「恢复默认」显式按钮承接 null=删除覆盖语义(02-C3),禁止手输 null。
// @author 港电实验室平台组
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import React, { useEffect, useState } from 'react';
import { apiGet, apiPut, apiPost } from './fetch';
import { ErrorBar } from './components';
import type { LayeredError } from './errors';

/** 设置项(后端 /api/settings 契约,02-C3) */
export interface SettingItem {
  key: string;
  label: string;
  type: string;
  value: unknown;
  default: unknown;
  source: string;
  choices: string[];
  unit: string | null;
  restart: boolean;
  env_locked: boolean;
  help: string | null;
}

interface SettingsPayload {
  sections: Record<string, SettingItem[]>;
  version: number;
}

const SOURCE_LABEL: Record<string, string> = {
  env: 'env 锁定',
  override: '后台覆盖',
  file: '配置文件',
  default: '默认值',
};

/** @brief 单项编辑控件:按 type/choices 渲染;env 锁定禁用并明示 */
function SettingField(props: {
  item: SettingItem;
  draft: unknown;
  serverError: string | null;
  onChange: (v: unknown) => void;
}): JSX.Element {
  const { item } = props;
  const disabled = item.env_locked;
  const val = props.draft;
  const change_cb = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>): void => {
    if (item.type === 'int') {
      const n = Number.parseInt(e.target.value, 10);
      props.onChange(Number.isFinite(n) ? n : e.target.value);
    } else if (item.type === 'float') {
      const n = Number.parseFloat(e.target.value);
      props.onChange(Number.isFinite(n) ? n : e.target.value);
    } else {
      props.onChange(e.target.value);
    }
  };
  let control: JSX.Element;
  if (item.type === 'bool') {
    control = (
      <input
        type="checkbox"
        checked={Boolean(val)}
        disabled={disabled}
        onChange={(e) => props.onChange(e.target.checked)}
        aria-label={item.label}
      />
    );
  } else if (item.choices.length > 0) {
    control = (
      <select
        value={String(val ?? '')}
        disabled={disabled}
        onChange={change_cb}
        aria-label={item.label}
      >
        {item.choices.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    );
  } else {
    control = (
      <input
        type={item.type === 'int' || item.type === 'float' ? 'number' : 'text'}
        value={val === null || val === undefined ? '' : String(val)}
        disabled={disabled}
        onChange={change_cb}
        aria-label={item.label}
      />
    );
  }
  return (
    <label className="gd-field">
      <span>
        {item.label}
        {item.unit ? `(${item.unit})` : ''}
        {item.restart ? <strong style={{ color: 'var(--gd-warn)' }}> · 改后需重启生效</strong> : ''}
      </span>
      {control}
      <span className="gd-help">
        来源:{SOURCE_LABEL[item.source] ?? item.source}
        {item.env_locked ? '(环境变量锁定,界面不可改)' : ''}
        {item.help ? ` · ${item.help}` : ''}
      </span>
      {props.serverError ? <span className="gd-err">{props.serverError}</span> : null}
    </label>
  );
}

/**
 * @brief 同构设置页:传入 apiBase(如 '' 或 '/adapter')即可复用。
 *        保存 = 整体 PUT {values};「恢复默认」= POST reset(删除全部覆盖层)。
 */
export function SettingsPage(props: { apiBase?: string; title?: string }): JSX.Element {
  const base = props.apiBase ?? '';
  const [payload, setPayload] = useState<SettingsPayload | null>(null);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [fieldErrs, setFieldErrs] = useState<Record<string, string>>({});
  const [error, setError] = useState<LayeredError | null>(null);
  const [notice, setNotice] = useState<string>('');
  const [busy, setBusy] = useState<boolean>(false);

  const load = async (): Promise<void> => {
    const r = await apiGet<SettingsPayload>(`${base}/api/settings`);
    if (r.ok && r.data) {
      setPayload(r.data);
      const d: Record<string, unknown> = {};
      for (const items of Object.values(r.data.sections)) {
        for (const it of items) d[it.key] = it.value;
      }
      setDraft(d);
      setFieldErrs({});
      setError(null);
    } else {
      setError(r.error);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const save_cb = async (): Promise<void> => {
    if (!payload) return;
    setBusy(true);
    setNotice('');
    const changed: Record<string, unknown> = {};
    for (const items of Object.values(payload.sections)) {
      for (const it of items) {
        if (!it.env_locked && draft[it.key] !== it.value) changed[it.key] = draft[it.key];
      }
    }
    const r = await apiPut<{ applied: Record<string, unknown>; errors: Record<string, string> }>(
      `${base}/api/settings`,
      { values: changed },
    );
    setBusy(false);
    if (r.ok) {
      setNotice('已保存');
      await load();
    } else {
      setError(r.error);
      const errs = (r.data as { errors?: Record<string, string> } | null)?.errors;
      const bodyErrs =
        errs ??
        ((): Record<string, string> => {
          try {
            return {};
          } catch {
            return {};
          }
        })();
      setFieldErrs({ ...r.fieldErrors, ...bodyErrs });
      // 后端 400 时 body 里带 errors 映射(02-C3);fetch 封装 data=null,再取一次:
      if (r.status === 400 && Object.keys(bodyErrs).length === 0) {
        const again = await apiGet<SettingsPayload>(`${base}/api/settings`);
        if (again.ok && again.data) setPayload(again.data);
      }
    }
  };

  const reset_cb = async (): Promise<void> => {
    setBusy(true);
    const r = await apiPost(`${base}/api/settings/reset`);
    setBusy(false);
    if (r.ok) {
      setNotice('已恢复默认(env 锁定项不受影响)');
      await load();
    } else {
      setError(r.error);
    }
  };

  if (!payload) {
    return (
      <div>
        <ErrorBar error={error} />
        <div className="gd-empty">设置加载中…</div>
      </div>
    );
  }
  return (
    <div>
      <ErrorBar error={error} />
      {notice ? <div className="gd-alert">{notice}</div> : null}
      {Object.entries(payload.sections).map(([section, items]) => (
        <section className="gd-card" key={section}>
          <h2>{section}</h2>
          {items.map((it) => (
            <SettingField
              key={it.key}
              item={it}
              draft={draft[it.key]}
              serverError={fieldErrs[it.key] ?? null}
              onChange={(v) => setDraft((d) => ({ ...d, [it.key]: v }))}
            />
          ))}
        </section>
      ))}
      <div className="gd-card">
        <button className="gd-btn" onClick={save_cb} disabled={busy}>
          保存全部修改
        </button>{' '}
        <button className="gd-btn ghost" onClick={reset_cb} disabled={busy}>
          恢复默认
        </button>
        <span className="gd-help" style={{ marginLeft: 12 }}>
          「恢复默认」将删除全部后台覆盖(等保下限与 env 锁定项不受影响)
        </span>
      </div>
    </div>
  );
}
