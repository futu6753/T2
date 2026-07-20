// =============================================================================
// @file  envelope.ts
// @brief AdapterResult 统一信封与字段级校验错误解析(H11 §五 fetch 封装职责),
//        以及手机号界面打码纯函数(H04 §七)。纯函数零副作用,node 直测。
// @author 港电实验室平台组
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================

/** AdapterResult 信封(R9:code/message/request_id/data) */
export interface Envelope {
  /** 业务错误码(信封内 code 字段) */
  code: number;
  /** 面向运维的中文消息 */
  message: string;
  /** 全链路请求 ID(02-F) */
  requestId: string;
  /** 附加数据 */
  data: Record<string, unknown>;
}

/** 字段级校验错误:字段路径 → 中文提示(pydantic 422 / 后端逐项拒绝映射) */
export type FieldErrors = Record<string, string>;

/** @brief 判断响应体是否为 AdapterResult 信封形态 */
export function isEnvelope(body: unknown): body is { code: number; message: string } {
  if (typeof body !== 'object' || body === null) return false;
  const b = body as Record<string, unknown>;
  return typeof b['code'] === 'number' && typeof b['message'] === 'string';
}

/** @brief 解析 AdapterResult 信封;非信封形态返回 null */
export function parseEnvelope(body: unknown): Envelope | null {
  if (!isEnvelope(body)) return null;
  const b = body as Record<string, unknown>;
  return {
    code: b['code'] as number,
    message: b['message'] as string,
    requestId: typeof b['request_id'] === 'string' ? (b['request_id'] as string) : '',
    data:
      typeof b['data'] === 'object' && b['data'] !== null
        ? (b['data'] as Record<string, unknown>)
        : {},
  };
}

/**
 * @brief 解析字段级校验错误,统一两种来源:
 *        1) FastAPI/pydantic 422:{detail:[{loc:[...,field],msg:...}]}
 *        2) 后端逐项拒绝:{field_errors:{field:reason}} 或信封 data.field_errors
 *        返回 字段名→中文提示 映射;无字段错误返回空对象。
 */
export function parseFieldErrors(body: unknown): FieldErrors {
  const out: FieldErrors = {};
  if (typeof body !== 'object' || body === null) return out;
  const b = body as Record<string, unknown>;

  const direct = b['field_errors'] ?? (parseEnvelope(b)?.data ?? {})['field_errors'];
  if (typeof direct === 'object' && direct !== null) {
    for (const [k, v] of Object.entries(direct as Record<string, unknown>)) {
      if (typeof v === 'string') out[k] = v;
    }
  }

  const detail = b['detail'];
  if (Array.isArray(detail)) {
    for (const item of detail) {
      if (typeof item !== 'object' || item === null) continue;
      const it = item as Record<string, unknown>;
      const loc = it['loc'];
      const msg = it['msg'];
      if (Array.isArray(loc) && loc.length > 0 && typeof msg === 'string') {
        const field = String(loc[loc.length - 1]);
        out[field] = msg;
      }
    }
  }
  return out;
}

/**
 * @brief 手机号界面打码(H04 §七):11 位手机保留前 3 后 4,中间 4 位打码。
 *        非 11 位数字串按「保留首末各 1/4、至少打码一半」的保守规则处理;
 *        空值返回空串。打码字符固定 * 号。
 */
export function maskPhone(phone: string): string {
  const digits = phone.replace(/\D/g, '');
  if (digits.length === 0) return '';
  if (digits.length === 11) return `${digits.slice(0, 3)}****${digits.slice(7)}`;
  const keep = Math.max(1, Math.floor(digits.length / 4));
  const maskedLen = Math.max(digits.length - keep * 2, Math.ceil(digits.length / 2));
  const head = Math.max(1, Math.floor((digits.length - maskedLen) / 2));
  const tail = digits.length - head - maskedLen;
  return `${digits.slice(0, head)}${'*'.repeat(maskedLen)}${tail > 0 ? digits.slice(digits.length - tail) : ''}`;
}

/** @brief 生成 X-Request-Id(02-F):web-前缀 + 时间基 + 随机段,仅小写十六进制 */
export function makeRequestId(): string {
  const rand = Math.floor(Math.random() * 0xffffffff)
    .toString(16)
    .padStart(8, '0');
  return `web-${Date.now().toString(16)}${rand}`;
}
