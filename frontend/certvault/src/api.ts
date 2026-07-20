// =============================================================================
// @file  api.ts
// @brief certvault API 层。JWT 特例(H11 §四.3):令牌仅存内存,绝不入
//        localStorage/sessionStorage;页面刷新经 /auth/sso/exchange 换取或
//        重新登录。表单类接口按后端 read_form/read_any_form 契约用 FormData。
// @author 港电实验室平台组
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { apiFetch, classifyError, type ApiResult, type LayeredError } from '@gd/ui-kit';

export const LOGIN_PATH = '/app/login';

/** 内存令牌(模块级变量;刷新即失,靠 exchange 恢复) */
let jwtToken = '';
let currentUser: { username: string; role: string } | null = null;

export function getUser(): { username: string; role: string } | null {
  return currentUser;
}
export function clearAuth(): void {
  jwtToken = '';
  currentUser = null;
}

/** @brief 登录成功后写入内存态 */
export function setAuth(token: string, username: string, role: string): void {
  jwtToken = token;
  currentUser = { username, role };
}

/** @brief 尝试用 SSO Cookie 会话换取 JWT(刷新恢复路径,H08 §3) */
export async function tryExchange(): Promise<boolean> {
  const r = await apiFetch<{ token: string; role: string }>('POST', '/auth/sso/exchange', {
    noRedirect: true,
    loginPath: LOGIN_PATH,
  });
  if (r.ok && r.data?.token) {
    jwtToken = r.data.token;
    const me = await cvGet<{ username: string; role: string }>('/auth/me', true);
    if (me.ok && me.data) currentUser = { username: me.data.username, role: me.data.role };
    return true;
  }
  return false;
}

/** @brief 带 Bearer 的 GET;401 时先试 exchange 重放一次,再失败按未登录处理 */
export async function cvGet<T>(url: string, noRetry = false): Promise<ApiResult<T>> {
  const r = await apiFetch<T>('GET', url, {
    headers: jwtToken ? { Authorization: `Bearer ${jwtToken}` } : {},
    noRedirect: true,
    loginPath: LOGIN_PATH,
  });
  if (r.status === 401 && !noRetry && (await tryExchange())) return cvGet<T>(url, true);
  return r;
}

/** @brief 带 Bearer 的 FormData POST/DELETE(multipart 由浏览器定界) */
export async function cvForm<T>(
  method: string,
  url: string,
  form: FormData | URLSearchParams | null,
  noRetry = false,
): Promise<ApiResult<T>> {
  const rid = `web-${Date.now().toString(16)}`;
  let resp: Response;
  try {
    resp = await fetch(url, {
      method,
      headers: {
        ...(jwtToken ? { Authorization: `Bearer ${jwtToken}` } : {}),
        'X-Request-Id': rid,
        Accept: 'application/json',
      },
      body: form ?? undefined,
      credentials: 'same-origin',
    });
  } catch {
    const error: LayeredError = {
      status: 0,
      kind: 'other',
      text: '网络异常或服务不可达,请检查连接后重试',
      waitSeconds: null,
      loginRedirect: null,
    };
    return {
      ok: false,
      status: 0,
      data: null,
      error,
      fieldErrors: {},
      envelope: null,
      requestId: rid,
    };
  }
  if (resp.status === 401 && !noRetry && (await tryExchange()))
    return cvForm<T>(method, url, form, true);
  const text = await resp.text();
  let parsed: unknown = null;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    parsed = null;
  }
  if (resp.ok) {
    return {
      ok: true,
      status: resp.status,
      data: parsed as T,
      error: null,
      fieldErrors: {},
      envelope: null,
      requestId: resp.headers.get('X-Request-Id') ?? rid,
    };
  }
  const headerMap: Record<string, string> = {};
  resp.headers.forEach((v, k) => (headerMap[k.toLowerCase()] = v));
  const error = classifyError(
    resp.status,
    headerMap,
    (parsed as Record<string, unknown>) ?? null,
    window.location.pathname,
    LOGIN_PATH,
  );
  return {
    ok: false,
    status: resp.status,
    data: parsed as T | null,
    error,
    fieldErrors: {},
    envelope: null,
    requestId: resp.headers.get('X-Request-Id') ?? rid,
  };
}

/** @brief 拉取受保护图片为 objectURL(Bearer 头无法用于 <img src>) */
export async function cvImageUrl(url: string): Promise<string | null> {
  try {
    const resp = await fetch(url, {
      headers: jwtToken ? { Authorization: `Bearer ${jwtToken}` } : {},
      credentials: 'same-origin',
    });
    if (!resp.ok) return null;
    return URL.createObjectURL(await resp.blob());
  } catch {
    return null;
  }
}

// ---- 领域类型 --------------------------------------------------------------

/** 引擎描述(/engines) */
export interface EngineInfo {
  id: string;
  name: string;
  available: boolean;
  detail: string;
  recommended_strength?: number;
  [k: string]: unknown;
}

/** 备案条目(字段与 cv_records 列一致,含撤销标记) */
export interface RecordItem {
  id: number;
  tracer_id: string;
  engine_id: string;
  issuer_id: number;
  recipient: string;
  purpose: string;
  validity: string;
  medium: string;
  created_at: string;
  revoked_at: string | null;
  revoked_by: string | null;
  is_standalone: number;
  [k: string]: unknown;
}

/** 溯源结果 */
export interface TraceResult {
  found: boolean;
  message?: string;
  tracer_id?: string;
  engine?: string;
  engine_name?: string;
  confidence?: string;
  vote_detail?: Record<string, string>;
  tried_engines?: string[];
  engine_errors?: Record<string, string>;
  record?: RecordItem;
  revoked?: boolean;
  [k: string]: unknown;
}

/** 介质枚举(recommend.py VALID_MEDIA) */
export const MEDIA: { value: string; label: string }[] = [
  { value: 'electronic', label: '电子流转' },
  { value: 'print', label: '打印' },
  { value: 'recapture', label: '翻拍' },
];
