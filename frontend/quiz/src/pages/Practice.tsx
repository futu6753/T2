// =============================================================================
// @file  Practice.tsx
// @brief 刷题页(H02-E1 / H11 §二):背题/做题双模式、单题/列表双视图即时切换、
//        题型与底色双分类过滤;做题模式判分落链,背题模式直看答案解析;
//        出题策略(顺序 / 邻域采样,后者须先在偏好开启,R-QZ-2)。
// @author 港电实验室平台组
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { useEffect, useState } from 'react';
import { Card, Empty, ErrorBar, useNavigate, useParams, type LayeredError } from '@gd/ui-kit';
import { qzAnswer, qzBankSummary, qzList, qzNext, qzOne, type Question } from '../api';

const QTYPE_LABEL: Record<string, string> = {
  single: '单选',
  multi: '多选',
  judge: '判断',
  risk: '风险排查',
  image: '识图',
};
const COLOR_LABEL: Record<string, string> = {
  none: '无底色',
  yellow: '黄',
  cyan: '青',
  green: '绿',
};
const COLOR_BG: Record<string, string> = {
  none: 'transparent',
  yellow: '#fdf3d0',
  cyan: '#d9f2f2',
  green: '#ddefdd',
};

type Mode = 'quiz' | 'recite';
type View = 'single' | 'list';

/** @brief 单题卡片(含作答/判分/解析) */
function QuestionCard(props: { qno: number; mode: Mode }): JSX.Element {
  const [q, setQ] = useState<Question | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [result, setResult] = useState<{
    correct?: boolean;
    answer?: string;
    analysis?: string;
  } | null>(null);
  const [error, setError] = useState<LayeredError | null>(null);
  const nav = useNavigate();

  useEffect(() => {
    let alive = true;
    setQ(null);
    setResult(null);
    setPicked([]);
    void qzOne(props.qno, props.mode).then((r) => {
      if (!alive) return;
      if (r.ok && r.data) setQ(r.data.question);
      else setError(r.error);
    });
    return () => {
      alive = false;
    };
  }, [props.qno, props.mode]);

  if (!q) {
    return (
      <Card>
        <ErrorBar error={error} />
        <Empty text="题目加载中…" />
      </Card>
    );
  }

  const isMulti = q.qtype === 'multi';
  const toggle_cb = (letter: string): void => {
    setPicked((p) =>
      isMulti
        ? p.includes(letter)
          ? p.filter((x) => x !== letter)
          : [...p, letter].sort()
        : [letter],
    );
  };

  const submit_cb = async (): Promise<void> => {
    const answer = picked.join('');
    const r = await qzAnswer(q.qno, answer, props.mode);
    if (r.ok && r.data) setResult(r.data);
    else setError(r.error);
  };

  const next_cb = async (): Promise<void> => {
    const r = await qzNext('sequence');
    if (r.ok && r.data?.qno) nav(`/app/q/${r.data.qno}`);
    else if (r.ok && r.data?.done)
      setError({
        status: 0,
        kind: 'other',
        text: '顺序练习已全部完成,可去错题本或今日复习巩固',
        waitSeconds: null,
        loginRedirect: null,
      });
    else setError(r.error);
  };

  return (
    <Card
      title={`第 ${q.qno} 题 · ${QTYPE_LABEL[q.qtype] ?? q.qtype} · ${COLOR_LABEL[q.color] ?? q.color}`}
    >
      <ErrorBar error={error} />
      <div style={{ background: COLOR_BG[q.color] ?? 'transparent', padding: 10, borderRadius: 6 }}>
        <p style={{ marginTop: 0 }}>{q.stem}</p>
        {q.image ? <p className="gd-help">配图:{q.image}</p> : null}
        {q.options.map((opt, i) => {
          const letter = String.fromCharCode(65 + i);
          return (
            <label key={letter} style={{ display: 'block', margin: '4px 0' }}>
              <input
                type={isMulti ? 'checkbox' : 'radio'}
                name={`q${q.qno}`}
                checked={picked.includes(letter)}
                onChange={() => toggle_cb(letter)}
              />{' '}
              {letter}. {opt}
            </label>
          );
        })}
      </div>
      {props.mode === 'recite' ? (
        <div className="gd-alert">
          <strong>答案:{q.answer ?? '—'}</strong>
          <p style={{ marginBottom: 0 }}>{q.analysis ?? ''}</p>
        </div>
      ) : result ? (
        <div className={result.correct ? 'gd-alert' : 'gd-alert error'}>
          <strong>
            {result.correct ? '回答正确' : '回答错误'} · 正确答案:{result.answer ?? '—'}
          </strong>
          <p style={{ marginBottom: 0 }}>{result.analysis ?? ''}</p>
        </div>
      ) : (
        <p>
          <button className="gd-btn" onClick={submit_cb} disabled={picked.length === 0}>
            提交作答
          </button>
        </p>
      )}
      <p>
        <button className="gd-btn ghost" onClick={next_cb}>
          下一题(顺序)
        </button>
      </p>
    </Card>
  );
}

/** @brief 刷题页主体 */
export function PracticePage(): JSX.Element {
  const params = useParams();
  const nav = useNavigate();
  const [mode, setMode] = useState<Mode>('quiz');
  const [view, setView] = useState<View>('single');
  const [qtype, setQtype] = useState<string>('');
  const [color, setColor] = useState<string>('');
  const [list, setList] = useState<Question[]>([]);
  const [summary, setSummary] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<LayeredError | null>(null);
  const qno = Number.parseInt(params['qno'] ?? '1', 10) || 1;

  useEffect(() => {
    let alive = true;
    void qzBankSummary().then((r) => {
      if (alive && r.ok) setSummary(r.data as Record<string, unknown>);
    });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (view !== 'list') return;
    let alive = true;
    void qzList(qtype, color, 100, 0).then((r) => {
      if (!alive) return;
      if (r.ok && r.data) setList(r.data.questions);
      else setError(r.error);
    });
    return () => {
      alive = false;
    };
  }, [view, qtype, color]);

  const total = (summary?.['total'] as number | undefined) ?? null;

  return (
    <div>
      <ErrorBar error={error} />
      <Card>
        <span className="gd-badge">
          {total !== null ? `题库共 ${total} 题` : '题库统计加载中…'}
        </span>{' '}
        <label>
          模式:
          <select value={mode} onChange={(e) => setMode(e.target.value as Mode)} aria-label="模式">
            <option value="quiz">做题(判分并记录)</option>
            <option value="recite">背题(直看答案)</option>
          </select>
        </label>{' '}
        <label>
          视图:
          <select value={view} onChange={(e) => setView(e.target.value as View)} aria-label="视图">
            <option value="single">单题</option>
            <option value="list">列表</option>
          </select>
        </label>{' '}
        <label>
          题型:
          <select value={qtype} onChange={(e) => setQtype(e.target.value)} aria-label="题型">
            <option value="">全部</option>
            {Object.entries(QTYPE_LABEL).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>{' '}
        <label>
          底色:
          <select value={color} onChange={(e) => setColor(e.target.value)} aria-label="底色">
            <option value="">全部</option>
            {Object.entries(COLOR_LABEL).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
      </Card>
      {view === 'single' ? (
        <QuestionCard qno={qno} mode={mode} />
      ) : (
        <Card title="题目列表(点击进入单题)">
          {list.length === 0 ? (
            <Empty text="当前筛选下暂无题目" />
          ) : (
            <table className="gd-table">
              <thead>
                <tr>
                  <th>题号</th>
                  <th>题型</th>
                  <th>底色</th>
                  <th>题干</th>
                </tr>
              </thead>
              <tbody>
                {list.map((q) => (
                  <tr
                    key={q.qno}
                    style={{ cursor: 'pointer' }}
                    onClick={() => nav(`/app/q/${q.qno}`)}
                  >
                    <td>{q.qno}</td>
                    <td>{QTYPE_LABEL[q.qtype] ?? q.qtype}</td>
                    <td>
                      <span
                        style={{
                          background: COLOR_BG[q.color],
                          padding: '0 8px',
                          borderRadius: 8,
                          border: '1px solid var(--gd-line)',
                        }}
                      >
                        {COLOR_LABEL[q.color] ?? q.color}
                      </span>
                    </td>
                    <td>{q.stem.length > 40 ? `${q.stem.slice(0, 40)}…` : q.stem}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}
    </div>
  );
}
