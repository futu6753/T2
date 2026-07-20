// =============================================================================
// @file  errors.ts
// @brief 四态错误分层文案(06-E8 / H11 §四.6):401/403/423/429 区分,
//        423/429 显示等待时长;401 跳登录并保留站内 next(06-E13 站内约束)。
//        纯函数零副作用——H09 §二 I.3 组件测试经 node 直测本文件。
// @author 港电实验室平台组
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================

/** 分层错误的结构化描述(供界面渲染与跳转决策) */
export interface LayeredError {
  /** HTTP 状态码 */
  status: number;
  /** 展示给用户的中文文案(不回显口令/验证码,H11 §四.5) */
  text: string;
  /** 语义类别:unauthorized=未登录 forbidden=无权限 locked=锁定 ratelimited=限速 other=其它 */
  kind: 'unauthorized' | 'forbidden' | 'locked' | 'ratelimited' | 'other';
  /** 等待秒数(423/429 时给出;无法解析时为 null) */
  waitSeconds: number | null;
  /** 401 时应跳转的登录地址(含站内 next);其余为 null */
  loginRedirect: string | null;
}

/** @brief 站内相对路径校验(06-E13):仅接受以单个 / 开头且非 // 协议相对形式 */
export function isSafeNext(next: string): boolean {
  if (!next.startsWith('/')) return false;
  if (next.startsWith('//')) return false; // 协议相对 = 外站
  if (next.includes('\\')) return false; // 反斜杠混淆
  // eslint-disable-next-line no-control-regex -- 校验意图即匹配控制字符
  if (/[\u0000-\u001f]/.test(next)) return false;
  return true;
}

/** @brief 组装 401 登录跳转:保留站内 next;next 不安全时丢弃 */
export function buildLoginRedirect(loginPath: string, next: string): string {
  if (!isSafeNext(next)) return loginPath;
  return `${loginPath}?next=${encodeURIComponent(next)}`;
}

/**
 * @brief 从响应头/响应体解析等待秒数。
 *        优先 Retry-After 头(整数秒);其次 body.retry_after / body.wait_seconds;
 *        再从中文文案「请 N 分钟后」提取。全部失败返回 null。
 */
export function parseWaitSeconds(
  headers: Record<string, string>,
  body: Record<string, unknown> | null,
): number | null {
  const ra = headers['retry-after'];
  if (ra !== undefined) {
    const n = Number.parseInt(ra, 10);
    if (Number.isFinite(n) && n >= 0) return n;
  }
  if (body) {
    for (const key of ['retry_after', 'wait_seconds']) {
      const v = body[key];
      if (typeof v === 'number' && Number.isFinite(v) && v >= 0) return Math.round(v);
    }
    const msg = typeof body['error'] === 'string' ? (body['error'] as string) : '';
    const m = msg.match(/(\d+)\s*分钟/);
    if (m) return Number.parseInt(m[1], 10) * 60;
    const s = msg.match(/(\d+)\s*秒/);
    if (s) return Number.parseInt(s[1], 10);
  }
  return null;
}

/** @brief 等待秒数 → 人读文案(分钟向上取整,保底 1 分钟粒度以内显秒) */
export function formatWait(waitSeconds: number | null): string {
  if (waitSeconds === null) return '请稍后再试';
  if (waitSeconds < 60) return `请 ${waitSeconds} 秒后再试`;
  return `请 ${Math.ceil(waitSeconds / 60)} 分钟后再试`;
}

/**
 * @brief 四态错误分层映射(06-E8)。429(IP 限速)与 423(账号锁定)文案区分并
 *        显示等待时长;401 与 403 区分。文案不含任何用户输入回显。
 * @param status    HTTP 状态码
 * @param headers   小写键的响应头
 * @param body      已解析的 JSON 响应体(解析失败传 null)
 * @param currentPath 当前站内路径(用于 401 保留 next)
 * @param loginPath 登录页路径(缺省 /login)
 */
export function classifyError(
  status: number,
  headers: Record<string, string>,
  body: Record<string, unknown> | null,
  currentPath: string,
  loginPath: string = '/login',
): LayeredError {
  if (status === 401) {
    return {
      status,
      kind: 'unauthorized',
      text: '未登录或会话已过期,请重新登录',
      waitSeconds: null,
      loginRedirect: buildLoginRedirect(loginPath, currentPath),
    };
  }
  if (status === 403) {
    return {
      status,
      kind: 'forbidden',
      text: '当前账号无此操作权限,如需访问请联系管理员',
      waitSeconds: null,
      loginRedirect: null,
    };
  }
  if (status === 423) {
    const wait = parseWaitSeconds(headers, body);
    return {
      status,
      kind: 'locked',
      text: `账号已锁定,${formatWait(wait)}`,
      waitSeconds: wait,
      loginRedirect: null,
    };
  }
  if (status === 429) {
    const wait = parseWaitSeconds(headers, body);
    return {
      status,
      kind: 'ratelimited',
      text: `操作过于频繁(IP 限速),${formatWait(wait)}`,
      waitSeconds: wait,
      loginRedirect: null,
    };
  }
  const fallback =
    body && typeof body['error'] === 'string' ? (body['error'] as string) : `请求失败(${status})`;
  return { status, kind: 'other', text: fallback, waitSeconds: null, loginRedirect: null };
}
