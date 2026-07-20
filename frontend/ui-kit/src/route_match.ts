// =============================================================================
// @file  route_match.ts
// @brief 路由模板匹配纯函数(从 router.tsx 抽出以便 node 直测)。
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================

/** @brief 路径模板匹配:/a/:id/b ↔ /a/7/b;尾部 * 通配。不匹配返回 null */
export function matchPath(pattern: string, path: string): Record<string, string> | null {
  const pSegs = pattern.split('/').filter((s) => s !== '');
  const aSegs = path.split('/').filter((s) => s !== '');
  const params: Record<string, string> = {};
  for (let i = 0; i < pSegs.length; i += 1) {
    const p = pSegs[i];
    if (p === '*') return params;
    const a = aSegs[i];
    if (a === undefined) return null;
    if (p.startsWith(':')) {
      params[p.slice(1)] = decodeURIComponent(a);
    } else if (p !== a) {
      return null;
    }
  }
  return aSegs.length === pSegs.length ? params : null;
}
