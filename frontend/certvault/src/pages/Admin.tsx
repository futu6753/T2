// =============================================================================
// @file  Admin.tsx
// @brief 管理页(H11 §二):锁定解锁、用户管理、审计链一键校验/导出、
//        逐引擎可用性、48bit ID 空间用量监控;顶部模式+套件徽标由壳承载。
// @author 港电实验室平台组
// Copyright (c) 2026 厦门自贸片区港务电力有限公司(港电实验室)
// =============================================================================
import { useEffect, useState } from 'react';
import { Card, Dot, Empty, ErrorBar, type LayeredError } from '@gd/ui-kit';
import { cvForm, cvGet, type EngineInfo } from '../api';

interface LockItem {
  username: string;
  locked_until?: string;
  failures?: number;
  [k: string]: unknown;
}
interface UserItem {
  id: number;
  username: string;
  role: string;
  disabled?: number;
  totp_enabled?: number;
  [k: string]: unknown;
}

/** @brief 管理页 */
export function AdminPage(): JSX.Element {
  const [locks, setLocks] = useState<LockItem[]>([]);
  const [users, setUsers] = useState<UserItem[]>([]);
  const [engines, setEngines] = useState<EngineInfo[]>([]);
  const [idSpace, setIdSpace] = useState<{ used: number; utilization: number } | null>(null);
  const [verify, setVerify] = useState<{
    ok?: boolean;
    checked?: number;
    broken_at?: unknown;
  } | null>(null);
  const [error, setError] = useState<LayeredError | null>(null);
  const [notice, setNotice] = useState<string>('');
  const [newUser, setNewUser] = useState<string>('');
  const [newPass, setNewPass] = useState<string>('');

  const load = async (): Promise<void> => {
    const l = await cvGet<{ locks: LockItem[] }>('/admin/locks');
    if (l.ok && l.data) setLocks(l.data.locks);
    else if (l.error) setError(l.error);
    const u = await cvGet<{ users: UserItem[] }>('/admin/users');
    if (u.ok && u.data) setUsers(u.data.users);
    const e = await cvGet<{ engines: EngineInfo[] }>('/engines');
    if (e.ok && e.data) setEngines(e.data.engines);
    const r = await cvGet<{ id_space: { used: number; utilization: number } }>('/records');
    if (r.ok && r.data) setIdSpace(r.data.id_space);
  };
  useEffect(() => {
    void load();
  }, []);

  const unlock_cb = async (username: string): Promise<void> => {
    const form = new URLSearchParams();
    form.set('username', username);
    const r = await cvForm('POST', '/admin/unlock', form);
    if (r.ok) {
      setNotice(`已解锁 ${username}`);
      await load();
    } else setError(r.error);
  };

  const userAction_cb = async (id: number, action: string): Promise<void> => {
    const r = await cvForm('POST', `/admin/users/${id}/${action}`, new URLSearchParams());
    if (r.ok) {
      setNotice('操作完成');
      await load();
    } else setError(r.error);
  };

  const createUser_cb = async (): Promise<void> => {
    const form = new URLSearchParams();
    form.set('username', newUser);
    form.set('password', newPass);
    const r = await cvForm('POST', '/admin/users', form);
    if (r.ok) {
      setNotice(`已创建用户 ${newUser}`);
      setNewUser('');
      setNewPass('');
      await load();
    } else setError(r.error);
  };

  const verify_cb = async (): Promise<void> => {
    const r = await cvGet<{ ok: boolean; checked: number; broken_at?: unknown }>(
      '/admin/audit/verify',
    );
    if (r.ok && r.data) setVerify(r.data);
    else setError(r.error);
  };

  return (
    <div>
      <ErrorBar error={error} />
      {notice ? <div className="gd-alert">{notice}</div> : null}
      <Card title="逐引擎可用性(06-E7)">
        <table className="gd-table">
          <tbody>
            {engines.map((e) => (
              <tr key={e.id}>
                <th>{e.name}</th>
                <td>
                  <Dot
                    kind={e.available ? 'ok' : 'danger'}
                    label={e.available ? '可用' : '不可用'}
                  />
                </td>
                <td className="gd-help">{e.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card title="48bit ID 空间用量监控">
        {idSpace ? (
          <p>
            已用 <strong>{idSpace.used}</strong> 个备案号 · 占用率{' '}
            {(idSpace.utilization * 100).toExponential(2)}%(容量 2⁴⁸)
          </p>
        ) : (
          <Empty text="用量获取中…" />
        )}
      </Card>
      <Card title="审计链校验与导出">
        <button className="gd-btn" onClick={verify_cb}>
          一键校验审计链
        </button>{' '}
        <a className="gd-btn ghost" href="/admin/audit/export" download>
          导出审计(CSV)
        </a>
        {verify ? (
          <div className={verify.ok ? 'gd-alert' : 'gd-alert error'} style={{ marginTop: 8 }}>
            {verify.ok
              ? `链完整:${verify.checked} 条记录逐条哈希校验通过`
              : `链断裂:断点 ${JSON.stringify(verify.broken_at)}`}
          </div>
        ) : null}
      </Card>
      <Card title="账号锁定">
        {locks.length === 0 ? (
          <Empty text="当前无锁定账号" />
        ) : (
          <table className="gd-table">
            <thead>
              <tr>
                <th>用户</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {locks.map((l) => (
                <tr key={l.username}>
                  <td>{l.username}</td>
                  <td>
                    锁定至{' '}
                    {l.locked_until ? new Date(String(l.locked_until)).toLocaleString() : '—'}(失败{' '}
                    {l.failures ?? '—'} 次)
                  </td>
                  <td>
                    <button className="gd-btn" onClick={() => void unlock_cb(l.username)}>
                      解锁
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <Card title="用户管理">
        <div style={{ marginBottom: 10 }}>
          <label className="gd-field">
            <span>新用户名</span>
            <input
              value={newUser}
              onChange={(e) => setNewUser(e.target.value)}
              autoComplete="off"
            />
          </label>
          <label className="gd-field">
            <span>初始口令(首登强制改)</span>
            <input
              type="password"
              value={newPass}
              onChange={(e) => setNewPass(e.target.value)}
              autoComplete="new-password"
            />
          </label>
          <button className="gd-btn" onClick={createUser_cb} disabled={!newUser || !newPass}>
            创建用户
          </button>
        </div>
        <table className="gd-table">
          <thead>
            <tr>
              <th>用户</th>
              <th>角色</th>
              <th>2FA</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>{u.username}</td>
                <td>{u.role}</td>
                <td>{u.totp_enabled ? '已启用' : '未启用'}</td>
                <td>{u.disabled ? '已停用' : '正常'}</td>
                <td>
                  <button
                    className="gd-btn ghost"
                    onClick={() => void userAction_cb(u.id, 'reset_password')}
                  >
                    重置口令
                  </button>{' '}
                  <button
                    className="gd-btn ghost"
                    onClick={() => void userAction_cb(u.id, 'reset_2fa')}
                  >
                    重置 2FA
                  </button>{' '}
                  {u.disabled ? (
                    <button className="gd-btn" onClick={() => void userAction_cb(u.id, 'enable')}>
                      启用
                    </button>
                  ) : (
                    <button
                      className="gd-btn danger"
                      onClick={() => void userAction_cb(u.id, 'disable')}
                    >
                      停用
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
