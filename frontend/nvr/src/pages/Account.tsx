// =============================================================================
// @file  Account.tsx
// @brief 账户页:当前身份/角色/登出;安全密钥(WebAuthn)管理依赖后端能力,
//        端到端随 GAP-25 落地后开放(不做假界面,H11 ai_directives 精神)。
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { Card } from '@gd/ui-kit';

/** @brief 账户页 */
export function AccountPage(props: { me: { username: string; role: string } | null }): JSX.Element {
  return (
    <div>
      <Card title="当前账户">
        {props.me ? (
          <table className="gd-table">
            <tbody>
              <tr>
                <th>用户名</th>
                <td>{props.me.username}</td>
              </tr>
              <tr>
                <th>角色</th>
                <td>{props.me.role}</td>
              </tr>
            </tbody>
          </table>
        ) : (
          <p>未登录</p>
        )}
        <p>
          <a className="gd-btn ghost" href="/sso/logout">
            退出登录(SSO 单点登出)
          </a>
        </p>
      </Card>
      <Card title="安全密钥(WebAuthn)">
        <p className="gd-help">
          安全密钥注册与管理依赖服务端 WebAuthn 能力,当前后端尚未启用该能力, 待联调环境具备后随
          GAP-25 一并开放(不提供占位操作,避免误导)。
        </p>
      </Card>
    </div>
  );
}
