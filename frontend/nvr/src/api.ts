// =============================================================================
// @file  api.ts
// @brief nvr API 封装(SSO Cookie 会话;401 由 ui-kit 统一跳登录保留 next)
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { apiGet, apiPost, type ApiResult } from '@gd/ui-kit';

export const LOGIN_PATH = '/app/login';
const opts = { loginPath: LOGIN_PATH };

/** 设备(不含密码) */
export interface Device {
  id: number;
  name: string;
  kind: 'nvr' | 'push';
  region: string;
  station: string;
  host?: string;
  enabled: number | boolean;
  state?: { status: string; since?: string };
  active_alert?: { scope: string; trigger_status: string; duration_seconds: number } | null;
  channels?: { total: number; online: number; offline: number };
  [k: string]: unknown;
}

export interface Overview {
  summary: { online: number; offline: number; abnormal: number; unchecked: number };
  by_status: Record<string, number>;
  by_kind: Record<string, Record<string, number>>;
  active_alerts: number;
  alerts_by_scope: Record<string, number>;
  patrol: { running: boolean; next_run_at: string | null; last_cycle: unknown };
}

export const nvOverview = (): Promise<ApiResult<Overview>> => apiGet('/api/status/overview', opts);
export const nvStatusDevices = (
  status: string,
  region: string,
): Promise<ApiResult<{ devices: Device[] }>> =>
  apiGet(
    `/api/status/devices?${new URLSearchParams({ ...(status ? { status } : {}), ...(region ? { region } : {}) })}`,
    opts,
  );
export const nvDeviceChannels = (
  id: number,
): Promise<
  ApiResult<{
    channels: Record<string, unknown>[];
    summary: { total: number; online: number; offline: number };
  }>
> => apiGet(`/api/devices/${id}/channels`, opts);
export const nvTimeline = (
  id: number,
): Promise<
  ApiResult<{
    timeline: {
      id: number;
      event_type: string;
      channel_no: number | null;
      from_status: string;
      to_status: string;
      detail: string;
      occurred_at: string;
    }[];
  }>
> => apiGet(`/api/devices/${id}/timeline`, opts);
export const nvResults = (
  id: number,
): Promise<
  ApiResult<{
    results: {
      id: number;
      status: string;
      source: string;
      detail: string;
      latency_ms: number;
      checked_at: string;
    }[];
  }>
> => apiGet(`/api/devices/${id}/results?limit=50`, opts);
export const nvCheck = (id: number): Promise<ApiResult<Record<string, unknown>>> =>
  apiPost(`/api/devices/${id}/check`, undefined, opts);
export const nvPatrolRun = (): Promise<ApiResult<Record<string, unknown>>> =>
  apiPost('/api/patrol/run', undefined, opts);

export const nvAlerts = (
  state: string,
): Promise<ApiResult<{ alerts: Record<string, unknown>[]; active_total: number }>> =>
  apiGet(`/api/alerts?limit=100${state ? `&state=${state}` : ''}`, opts);

export const nvReportLatest = (): Promise<ApiResult<Record<string, unknown>>> =>
  apiGet('/api/reports/latest', { ...opts, noRedirect: false });
export const nvReportGenerate = (): Promise<ApiResult<Record<string, unknown>>> =>
  apiPost('/api/reports/generate', undefined, opts);
export const nvChannelsReadiness = (): Promise<
  ApiResult<{ channels: Record<string, unknown>[] }>
> => apiGet('/api/notifications/channels', opts);
export const nvSsoStatus = (): Promise<ApiResult<{ enabled: boolean }>> =>
  apiGet('/sso/status', { noRedirect: true });
