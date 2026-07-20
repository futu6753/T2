// =============================================================================
// @file  router.tsx
// @brief 极简 history 模式路由(H11 §五「新增运行时依赖走评审」——以 ~90 行
//        自研替代 react-router,守内网供应链最小面;后端须配 index.html 兜底)。
//        支持:静态段、:param 参数段、通配 *;Link 组件;编程式导航。
// @author 港电实验室平台组
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import React, { createContext, useContext, useEffect, useState } from 'react';
import { matchPath } from './route_match';

export { matchPath } from './route_match';

/** 当前路径上下文(pathname + 路由参数) */
interface RouterState {
  path: string;
  navigate: (to: string) => void;
}

const RouterCtx = createContext<RouterState>({ path: '/', navigate: () => undefined });
const ParamsCtx = createContext<Record<string, string>>({});

/** @brief 读取当前路由参数(:param 段) */
export function useParams(): Record<string, string> {
  return useContext(ParamsCtx);
}

/** @brief 编程式导航 */
export function useNavigate(): (to: string) => void {
  return useContext(RouterCtx).navigate;
}

/** @brief 当前 pathname */
export function usePath(): string {
  return useContext(RouterCtx).path;
}

/** Route 声明:pattern → 元素 */
export interface RouteDef {
  pattern: string;
  element: React.ReactNode;
}

/** @brief 路由容器:监听 popstate,渲染首个匹配路由;无匹配渲染 fallback */
export function Router(props: { routes: RouteDef[]; fallback?: React.ReactNode }): JSX.Element {
  const [path, setPath] = useState<string>(window.location.pathname);

  useEffect(() => {
    const onPop = (): void => setPath(window.location.pathname);
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop); // 生命周期清理(H11 §四.8)
  }, []);

  const navigate = (to: string): void => {
    window.history.pushState(null, '', to);
    setPath(new URL(to, window.location.origin).pathname);
  };

  let matched: React.ReactNode = props.fallback ?? <div className="gd-empty">页面不存在</div>;
  let params: Record<string, string> = {};
  for (const r of props.routes) {
    const m = matchPath(r.pattern, path);
    if (m !== null) {
      matched = r.element;
      params = m;
      break;
    }
  }
  return (
    <RouterCtx.Provider value={{ path, navigate }}>
      <ParamsCtx.Provider value={params}>{matched}</ParamsCtx.Provider>
    </RouterCtx.Provider>
  );
}

/** @brief 站内链接:history 导航,不整页刷新;active 类名随当前路径 */
export function Link(props: {
  to: string;
  children: React.ReactNode;
  className?: string;
  exact?: boolean;
}): JSX.Element {
  const { path, navigate } = useContext(RouterCtx);
  const isActive = props.exact ? path === props.to : path.startsWith(props.to);
  const cls = [props.className ?? '', isActive ? 'active' : ''].join(' ').trim();
  const click_cb = (e: React.MouseEvent): void => {
    e.preventDefault();
    navigate(props.to);
  };
  return (
    <a href={props.to} onClick={click_cb} className={cls || undefined}>
      {props.children}
    </a>
  );
}
