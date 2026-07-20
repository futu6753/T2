// =============================================================================
// @file  api.ts
// @brief quiz 后端 API 类型化封装(路径与 apps/quiz/web.py 契约一一对应)
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { apiGet, apiPost, type ApiResult } from '@gd/ui-kit';

/** SPA 站内登录页(供 401 跳转保留 next) */
export const LOGIN_PATH = '/app/login';

/** 题目公共视图(做题模式无 answer/analysis) */
export interface Question {
  qno: number;
  qtype: 'single' | 'multi' | 'judge' | 'risk' | 'image';
  color: 'none' | 'yellow' | 'cyan' | 'green';
  stem: string;
  options: string[];
  image: string;
  answer?: string;
  analysis?: string;
}

/** 当前身份 */
export interface Identity {
  kind: 'sso' | 'guest';
  username?: string;
  role?: string;
  guest_code?: string;
}

const opts = { loginPath: LOGIN_PATH };

export const qzMe = (): Promise<ApiResult<Identity>> =>
  apiGet<Identity>('/me', { ...opts, noRedirect: true });
export const qzSsoStatus = (): Promise<ApiResult<{ enabled: boolean }>> =>
  apiGet('/sso/status', { ...opts, noRedirect: true });
export const qzGuestNew = (): Promise<ApiResult<{ guest_code: string }>> =>
  apiPost('/guest/new', undefined, { ...opts, noRedirect: true });
export const qzGuestLoad = (code: string): Promise<ApiResult<{ guest_code: string }>> =>
  apiGet(`/guest/load/${encodeURIComponent(code)}`, { ...opts, noRedirect: true });

export const qzBankSummary = (): Promise<
  ApiResult<{
    total: number;
    by_type: Record<string, number>;
    by_color: Record<string, number>;
    images: number;
  }>
> => apiGet('/api/bank/summary', opts);

export const qzList = (
  qtype: string,
  color: string,
  limit: number,
  offset: number,
): Promise<ApiResult<{ questions: Question[] }>> =>
  apiGet(
    `/api/questions?qtype=${encodeURIComponent(qtype)}&color=${encodeURIComponent(color)}&limit=${limit}&offset=${offset}`,
    opts,
  );

export const qzOne = (
  qno: number,
  mode: string,
): Promise<ApiResult<{ mode: string; question: Question }>> =>
  apiGet(`/api/questions/${qno}?mode=${encodeURIComponent(mode)}`, opts);

export const qzAnswer = (
  qno: number,
  answer: string,
  mode: string,
): Promise<ApiResult<{ correct?: boolean; answer?: string; analysis?: string }>> =>
  apiPost('/api/answer', { qno, answer, mode }, opts);

export const qzWrongbook = (): Promise<
  ApiResult<{
    wrongbook: {
      qno: number;
      qtype: string;
      color: string;
      stem: string;
      wrong_count: number;
      updated_at: string;
    }[];
  }>
> => apiGet('/api/wrongbook', opts);
export const qzWrongClear = (qno: number): Promise<ApiResult<{ ok: boolean }>> =>
  apiPost(`/api/wrongbook/${qno}/clear`, undefined, opts);

export const qzProgress = (): Promise<
  ApiResult<{
    attempted: number;
    correct_total: number;
    wrong_total: number;
    wrongbook: number;
    rating: number;
    games: number;
  }>
> => apiGet('/api/progress', opts);

export const qzReviewToday = (): Promise<
  ApiResult<{ queue: { qno: number; due_at?: string; overdue_days?: number; stem?: string }[] }>
> => apiGet('/api/review/today', opts);

export const qzPrefs = (): Promise<ApiResult<{ elo_sampling: boolean }>> =>
  apiGet('/api/prefs', opts);
export const qzSetPrefs = (elo: boolean): Promise<ApiResult<{ elo_sampling: boolean }>> =>
  apiPost('/api/prefs', { elo_sampling: elo }, opts);

export const qzNext = (
  strategy: string,
): Promise<
  ApiResult<{
    strategy: string;
    qno: number | null;
    done?: boolean;
    difficulty?: number;
    rating?: number;
  }>
> => apiGet(`/api/practice/next?strategy=${encodeURIComponent(strategy)}`, opts);

export const qzMigrateCode = (): Promise<
  ApiResult<{ code: string; ttl_seconds: number; note: string }>
> => apiPost('/api/migrate/code', undefined, opts);
export const qzMigrateRedeem = (
  code: string,
): Promise<ApiResult<{ ok: boolean; merged?: unknown }>> =>
  apiPost('/api/migrate/redeem', { code }, opts);
