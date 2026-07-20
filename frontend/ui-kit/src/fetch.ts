// =============================================================================
// @file  fetch.ts
// @brief 统一 fetch 封装(H11 §五):自动携带 CSRF 令牌、生成/透传 X-Request-Id
//        (02-F 全链路贯通)、统一处理 401/403/423/429 分层文案、解析
//        AdapterResult 信封与字段级校验错误。凭据一律走 HttpOnly Cookie
//        (H11 §四.3),本封装不读写任何 localStorage/sessionStorage。
// @author 港电实验室平台组
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { classifyError, type LayeredError } from './errors';
import {
  makeRequestId,
  parseEnvelope,
  parseFieldErrors,
  type Envelope,
  type FieldErrors,
} from './envelope';

/** apiFetch 的统一返回:ok 为业务成功;否则 error 必有 */
export interface ApiResult<T = unknown> {
  ok: boolean;
  /** HTTP 状态码 */
  status: number;
  /** 成功时的响应体 */
  data: T | null;
  /** 分层错误(失败时必有) */
  error: LayeredError | null;
  /** 字段级校验错误(表单逐项映射,H11 §三) */
  fieldErrors: FieldErrors;
  /** AdapterResult 信封(响应体为信封形态时解析) */
  envelope: Envelope | null;
  /** 本次请求的 X-Request-Id(生成或透传) */
  requestId: string;
}

/** 401 时的默认行为:跳登录并保留站内 next。可由 setUnauthorizedHandler 覆盖(测试注入)。 */
let unauthorizedHandler: (redirect: string) => void = (redirect) => {
  window.location.href = redirect;
};

/** @brief 覆盖 401 处理器(E2E/单测注入;传 null 恢复默认) */
export function setUnauthorizedHandler(fn: ((redirect: string) => void) | null): void {
  unauthorizedHandler = fn ?? ((redirect) => (window.location.href = redirect));
}

/** @brief 读取 CSRF 令牌:优先 <meta name="gd-csrf">,其次非 HttpOnly 的 gd_csrf Cookie */
export function readCsrfToken(): string {
  const meta = document.querySelector('meta[name="gd-csrf"]');
  const fromMeta = meta?.getAttribute('content') ?? '';
  if (fromMeta) return fromMeta;
  const m = document.cookie.match(/(?:^|;\s*)gd_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : '';
}

/** apiFetch 附加选项 */
export interface ApiOptions {
  /** JSON 请求体(自动 stringify 并置 Content-Type) */
  json?: unknown;
  /** multipart 表单体(FormData,浏览器自动置 boundary) */
  form?: FormData;
  /** urlencoded 表单体 */
  urlencoded?: Record<string, string>;
  /** Bearer 令牌(certvault JWT 内存特例,H11 §四.3;来源须为内存变量) */
  bearer?: string;
  /** 覆盖登录页路径(缺省 /login) */
  loginPath?: string;
  /** 401 时不自动跳转(登录页自身等场景) */
  noRedirect?: boolean;
  /** 额外请求头 */
  headers?: Record<string, string>;
}

/**
 * @brief 统一请求入口。同源、携带 Cookie;非 GET 自动附 CSRF 头;
 *        统一生成 X-Request-Id 并从响应回读透传值。
 */
export async function apiFetch<T = unknown>(
  method: string,
  url: string,
  opts: ApiOptions = {},
): Promise<ApiResult<T>> {
  const rid = makeRequestId();
  const headers: Record<string, string> = {
    'X-Request-Id': rid,
    Accept: 'application/json',
    ...(opts.headers ?? {}),
  };
  if (method.toUpperCase() !== 'GET') {
    const csrf = readCsrfToken();
    if (csrf) headers['X-CSRF-Token'] = csrf;
  }
  if (opts.bearer) headers['Authorization'] = `Bearer ${opts.bearer}`;
  let body: string | FormData | undefined;
  if (opts.json !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(opts.json);
  } else if (opts.form !== undefined) {
    body = opts.form; // Content-Type 交浏览器带 boundary
  } else if (opts.urlencoded !== undefined) {
    headers['Content-Type'] = 'application/x-www-form-urlencoded';
    body = new URLSearchParams(opts.urlencoded).toString();
  }

  let resp: Response;
  try {
    resp = await fetch(url, { method, headers, body, credentials: 'same-origin' });
  } catch {
    return {
      ok: false,
      status: 0,
      data: null,
      error: {
        status: 0,
        kind: 'other',
        text: '网络异常或服务不可达,请检查连接后重试',
        waitSeconds: null,
        loginRedirect: null,
      },
      fieldErrors: {},
      envelope: null,
      requestId: rid,
    };
  }

  const respRid = resp.headers.get('X-Request-Id') ?? rid;
  let parsed: unknown = null;
  const text = await resp.text();
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
  }

  if (resp.ok) {
    return {
      ok: true,
      status: resp.status,
      data: parsed as T,
      error: null,
      fieldErrors: {},
      envelope: parseEnvelope(parsed),
      requestId: respRid,
    };
  }

  const headerMap: Record<string, string> = {};
  resp.headers.forEach((v, k) => (headerMap[k.toLowerCase()] = v));
  const err = classifyError(
    resp.status,
    headerMap,
    (parsed as Record<string, unknown>) ?? null,
    window.location.pathname + window.location.search,
    opts.loginPath ?? '/login',
  );
  if (err.kind === 'unauthorized' && err.loginRedirect && !opts.noRedirect) {
    unauthorizedHandler(err.loginRedirect);
  }
  return {
    ok: false,
    status: resp.status,
    data: null,
    error: err,
    fieldErrors: parseFieldErrors(parsed),
    envelope: parseEnvelope(parsed),
    requestId: respRid,
  };
}

/** @brief GET 便捷入口 */
export function apiGet<T = unknown>(url: string, opts: ApiOptions = {}): Promise<ApiResult<T>> {
  return apiFetch<T>('GET', url, opts);
}

/** @brief POST 便捷入口 */
export function apiPost<T = unknown>(
  url: string,
  json?: unknown,
  opts: ApiOptions = {},
): Promise<ApiResult<T>> {
  return apiFetch<T>('POST', url, { ...opts, json });
}

/** @brief PUT 便捷入口 */
export function apiPut<T = unknown>(
  url: string,
  json?: unknown,
  opts: ApiOptions = {},
): Promise<ApiResult<T>> {
  return apiFetch<T>('PUT', url, { ...opts, json });
}
