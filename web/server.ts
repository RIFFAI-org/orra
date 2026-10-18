// Front door: Better Auth, the admin API, the built pages, saved reports, and a signed-in-only pass-through to FastAPI.
import { randomUUID } from 'node:crypto'
import { serve } from '@hono/node-server'
import { serveStatic } from '@hono/node-server/serve-static'
import { getMigrations } from 'better-auth/db/migration'
import { type Context, Hono, type Next } from 'hono'
import { bodyLimit } from 'hono/body-limit'
import { proxy } from 'hono/proxy'
import { auth, isStaff, pool } from './auth.ts'
import { mapLines } from './lines.ts'

if (process.argv[2] === 'migrate') {  // creates or updates Better Auth's tables, and ours
  await (await getMigrations(auth.options)).runMigrations()
  await pool.query(`create table if not exists report (id uuid primary key, user_id text not null,
    created_at timestamptz not null default now(), lat float8 not null, lon float8 not null, state jsonb not null)`)
  process.exit()
}

const API = process.env.API_URL ?? 'http://127.0.0.1:8000'  // FastAPI, never exposed directly
const STATS = `select
  (select count(*) from "user")::int as users,
  (select count(*) from "user" where "createdAt" > now() - interval '7 days')::int as new_week,
  (select count(distinct "userId") from "session" where "expiresAt" > now())::int as signed_in,
  (select count(*) from "user" where role = 'admin')::int as admins,
  (select count(*) from "user" where banned)::int as banned,
  (select count(*) from report)::int as reports,
  (select count(*) from report where created_at > now() - interval '1 day')::int as reports_day`

const session = (c: Context) => auth.api.getSession({ headers: c.req.raw.headers })
type Env = { Variables: { user: typeof auth.$Infer.Session.user } }
const staffOnly = async (c: Context, next: Next) =>
  isStaff((await session(c))?.user) ? next() : c.json({ message: 'Staff only.' }, 403)

const app = new Hono<Env>()
app.use('/api/auth/admin/*', staffOnly)  // the admin plugin checks the role; this adds the STAFF_DOMAIN rule
app.all('/api/auth/*', c => auth.handler(c.req.raw))
app.get('/api/admin/stats', staffOnly, async c => c.json((await pool.query(STATS)).rows[0]))
app.use('/api/*', bodyLimit({ maxSize: 1 << 20 }))  // the API takes small JSON, and its dicts go straight into Gemini prompts
app.use('/api/*', async (c, next) => {
  const s = await session(c)
  if (!s) return c.json({ message: 'Sign in first.' }, 401)
  c.set('user', s.user)
  return next()
})

// Earth Engine runs cost money and any Google account can sign in, so each person gets a daily allowance.
// ponytail: counts live per instance, so with max-instances 3 the real cap is up to 3x; count in Postgres if that matters
const DAILY = 20, used = new Map<string, number>()
let today = ''
const capped = async (c: Context<Env>, next: Next) => {
  const u = c.get('user'), d = new Date().toISOString().slice(0, 10)
  if (d !== today) { used.clear(); today = d }
  const n = (used.get(u.id) ?? 0) + 1
  if (n > DAILY && !isStaff(u)) return c.json({ message: `Daily limit of ${DAILY} checks reached.` }, 429)
  used.set(u.id, n)
  return next()
}
app.post('/api/report', capped)
app.post('/api/claim', capped)

// Every report is saved as FastAPI streams it out, and the browser gets its id. The browser never writes one,
// so a shared link or a printed brief can be checked against this copy.
async function save(line: string, userId: string) {
  if (!line.startsWith('{"type": "report"')) return line  // json.dumps writes the type first
  try {
    const ev = JSON.parse(line), id = randomUUID()
    await pool.query('insert into report (id, user_id, lat, lon, state) values ($1, $2, $3, $4, $5)',
      [id, userId, ev.state.lat, ev.state.lon, ev.state])
    return JSON.stringify({ ...ev, id })
  } catch (e) {
    console.error('report not saved', e)  // the person still gets the report, just without a link
    return line
  }
}
app.post('/api/report', async c => {
  const res = await proxy(API + c.req.path, c.req.raw), userId = c.get('user').id
  if (!res.ok || !res.body) return res
  const headers = new Headers(res.headers)
  headers.delete('content-length')
  return new Response(mapLines(res.body, line => save(line, userId)), { status: res.status, headers })
})
// shortcut: no index on user_id, so this scans the table; add one in migrate once reports reach the tens of thousands
app.get('/api/reports', async c => c.json((await pool.query(`select id, created_at, lat, lon, left(state->>'text', 120) as text
  from report where user_id = $1 order by created_at desc limit 50`, [c.get('user').id])).rows))
app.get('/api/reports/:id', async c => {  // any signed-in person with the link
  const id = c.req.param('id')
  const { rows } = /^[0-9a-f-]{36}$/.test(id) ? await pool.query('select state from report where id = $1', [id]) : { rows: [] }
  return rows[0] ? c.json({ ...rows[0].state, id }) : c.json({ message: 'No such report.' }, 404)
})
app.all('/api/*', c => proxy(API + c.req.path, c.req.raw))
app.use('*', serveStatic({ root: './dist' }))
app.get('*', serveStatic({ path: './dist/index.html' }))  // client-side pages such as /admin

serve({ fetch: app.fetch, port: Number(process.env.PORT ?? 3000) })
