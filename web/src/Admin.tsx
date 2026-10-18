import type { UserWithRole } from 'better-auth/plugins'
import { type ReactNode, useEffect, useState } from 'react'
import Header from './Header.tsx'
import { authClient, signIn } from './auth-client.ts'
import { day } from './report.tsx'

type Users = { users: UserWithRole[], total: number }
type Result = { error: { message?: string } | null }
const STATS = { users: 'Users', new_week: 'Joined this week', signed_in: 'With an active session', admins: 'Admins', banned: 'Banned',
                reports: 'Reports saved', reports_day: 'Reports in the last 24 hours' }

const Shell = ({ children }: { children: ReactNode }) => <><Header /><main className="admin">{children}</main></>

function UsersTab() {
  const [q, setQ] = useState('')
  const [data, setData] = useState<Users | null>(null)
  const [err, setErr] = useState('')

  async function load() {
    // shortcut: first 100 matches only; add paging when the user list outgrows it
    const { data, error } = await authClient.admin.listUsers({
      query: { limit: 100, sortBy: 'createdAt', sortDirection: 'desc', ...(q ? { searchValue: q, searchField: 'email' } : {}) },
    })
    setErr(error?.message ?? '')
    setData(data)
  }
  async function act(fn: () => Promise<Result>) {
    const { error } = await fn()
    if (error) setErr(error.message ?? 'That did not work.')
    else load()
  }
  useEffect(() => { load() }, [])

  return <section className="mod">
    <form className="row" onSubmit={e => { e.preventDefault(); load() }}>
      <label htmlFor="q" className="sr">Search by email</label>
      <input type="text" id="q" value={q} onChange={e => setQ(e.target.value)} placeholder="Search by email" autoComplete="off" />
      <button>Search</button>
    </form>
    <p className="err" role="alert">{err}</p>
    {data && <>
      <p className="tech dim">{data.total} users{data.total > data.users.length && `, showing ${data.users.length}`}</p>
      <div className="scroll"><table>
        <thead><tr><th scope="col">User</th><th scope="col">Role</th><th scope="col">Joined</th><th scope="col">Status</th><th scope="col"><span className="sr">Actions</span></th></tr></thead>
        <tbody>{data.users.map(u => <tr key={u.id}>
          <td>{u.name}<span className="src">{u.email}</span></td>
          <td className="tech">{u.role}</td>
          <td className="tech">{day(u.createdAt)}</td>
          <td className="tech">{u.banned ? 'Banned' : 'Active'}</td>
          <td><div className="row">
            {u.banned
              ? <button type="button" className="small" onClick={() => act(() => authClient.admin.unbanUser({ userId: u.id }))}>Unban</button>
              : <button type="button" className="small" onClick={() => act(() => authClient.admin.banUser({ userId: u.id }))}>Ban</button>}
            <button type="button" className="small" onClick={() => act(() => authClient.admin.revokeUserSessions({ userId: u.id }))}>Sign out</button>
            <button type="button" className="small" onClick={() =>
              confirm(`Delete ${u.email}? This cannot be undone.`) && act(() => authClient.admin.removeUser({ userId: u.id }))}>Delete</button>
          </div></td>
        </tr>)}</tbody>
      </table></div>
    </>}
  </section>
}

function PlatformTab() {
  const [stats, setStats] = useState<Record<string, number> | null>(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    fetch('/api/admin/stats')
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`The server answered ${r.status}.`)))
      .then(setStats, e => setErr(e.message))
  }, [])
  return <section className="mod">
    <p className="err" role="alert">{err}</p>
    {stats && <table><tbody>{Object.entries(STATS).map(([k, label]) =>
      <tr key={k}><th scope="row">{label}</th><td className="tech">{stats[k]}</td></tr>)}</tbody></table>}
  </section>
}

export default function Admin() {
  const { data: session, isPending } = authClient.useSession()
  const [tab, setTab] = useState<'users' | 'platform'>('users')
  if (isPending) return <Shell>{null}</Shell>
  if (!session) return <Shell>
    <h2>Admin</h2>
    <p className="hint">Sign in with your staff Google account.</p>
    <button type="button" className="primary" onClick={signIn}>Sign in with Google</button>
  </Shell>
  // the server enforces this too; here it only picks what to show
  if (session.user.role !== 'admin') return <Shell>
    <h2>Admins only</h2>
    <p className="hint">This page is for staff accounts. You are signed in as {session.user.email}.</p>
    <p><a href="/" className="tech" style={{ color: 'var(--white)' }}>Back to Orra</a></p>
  </Shell>
  return <Shell>
    <h2>Admin</h2>
    <div className="row" role="group" aria-label="Section">
      <button type="button" className="small" aria-pressed={tab === 'users'} onClick={() => setTab('users')}>Users</button>
      <button type="button" className="small" aria-pressed={tab === 'platform'} onClick={() => setTab('platform')}>Platform</button>
    </div>
    {tab === 'users' ? <UsersTab /> : <PlatformTab />}
  </Shell>
}
