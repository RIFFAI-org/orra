// server.ts, the front door: who gets in, the daily allowance, the 1 MB cap, saving reports as they stream, and the pass-through.
// The real server runs in this process, talking to a fake FastAPI on localhost; Postgres and the session lookup are stubbed
// (pool.query and auth.api.getSession are swapped on the shared objects), so no database, Google or network is involved.
// Local dev tests: from web/, run `node --test "tests/*.test.ts"`.
import assert from 'node:assert/strict'
import http, { type IncomingMessage, type Server } from 'node:http'
import { syncBuiltinESMExports } from 'node:module'
import type { AddressInfo } from 'node:net'
import { after, afterEach, mock, test } from 'node:test'

// ---------- a fake FastAPI ----------
type Seen = { method?: string, url?: string, headers: IncomingMessage['headers'], body: string }
type Reply = { status?: number, type?: string, body: string }
const seen: Seen[] = []
let reply: (req: Seen) => Reply = () => ({ body: '' })
const upstream = http.createServer(async (req, res) => {
  let body = ''
  for await (const chunk of req) body += chunk
  const request: Seen = { method: req.method, url: req.url, headers: req.headers, body }
  seen.push(request)
  const r = reply(request)
  res.writeHead(r.status ?? 200, { 'content-type': r.type ?? 'application/x-ndjson' }).end(r.body)
})
const listen = (s: Server) => new Promise<number>(ok => s.listen(0, '127.0.0.1', () => ok((s.address() as AddressInfo).port)))
const apiPort = await listen(upstream)

// ---------- start the real server on a free port, remembering the http server it creates so it can be closed ----------
const probe = http.createServer()
const webPort = await listen(probe)
await new Promise(ok => probe.close(ok))
process.env.PORT = String(webPort)
process.env.API_URL = `http://127.0.0.1:${apiPort}`
process.env.STAFF_DOMAIN = 'example.org'

const made: Server[] = []
const realCreateServer = http.createServer
Object.assign(http, { createServer: (...args: Parameters<typeof realCreateServer>) => { const s = realCreateServer(...args); made.push(s); return s } })
syncBuiltinESMExports()  // @hono/node-server takes createServer from node:http when it is first loaded
const { auth, pool } = await import('../auth.ts')

type Query = { sql: string, params?: unknown[] }
const queries: Query[] = []
let db: (q: Query) => unknown = () => ({ rows: [] })
Object.assign(pool, { query: async (sql: string, params?: unknown[]) => { const q = { sql, params }; queries.push(q); return db(q) } })
Object.assign(auth.api, {
  getSession: async ({ headers }: { headers: Headers }) => {
    const who = headers.get('x-test-user')
    return who ? { user: JSON.parse(who), session: {} } : null
  },
})
Object.assign(auth, { handler: async () => new Response('auth-handler') })

await import('../server.ts')
Object.assign(http, { createServer: realCreateServer })
syncBuiltinESMExports()
const web = made[0]
if (!web.listening) await new Promise(ok => web.once('listening', ok))

after(async () => {
  for (const s of [web, upstream]) { s.closeAllConnections(); await new Promise(ok => s.close(ok)) }
})
afterEach(() => {
  seen.length = 0
  queries.length = 0
  db = () => ({ rows: [] })
  reply = () => ({ body: '' })
  mock.restoreAll()
})

// ---------- helpers ----------
const url = (path: string) => `http://127.0.0.1:${webPort}${path}`
const person = (id: string, email = `${id}@gmail.com`, emailVerified = true) => ({ 'x-test-user': JSON.stringify({ id, email, emailVerified }) })
const staff = () => person('staff-1', 'dev@example.org')
const post = (path: string, who: Record<string, string> | null, body: unknown = {}) =>
  fetch(url(path), { method: 'POST', headers: { 'content-type': 'application/json', ...who }, body: JSON.stringify(body) })
const get = (path: string, who: Record<string, string> | null = null) => fetch(url(path), { headers: { ...who } })
const UUID = '0b5f3c9e-6f5d-4d3e-9a1b-2c3d4e5f6a7b'
const STEP = '{"type": "step", "phase": "intake", "text": "Reading what you told us"}'
const REPORT_STATE = { lat: 13.75, lon: 100.5, text: 'Single professional', report: { verdict: ['Fine'] } }
const REPORT_LINE = `{"type": "report", "state": ${JSON.stringify(REPORT_STATE)}}`  // Python's json.dumps puts the type first, with a space
const inserts = () => queries.filter(q => q.sql.startsWith('insert into report'))

// ---------- who gets in ----------
test('API calls without a session are refused and never reach FastAPI', async () => {
  for (const [method, path] of [['GET', '/api/reports'], ['POST', '/api/report'], ['POST', '/api/chat'], ['GET', `/api/reports/${UUID}`], ['POST', '/api/anything']]) {
    const r = await fetch(url(path), { method })
    assert.equal(r.status, 401, `${method} ${path}`)
    assert.deepEqual(await r.json(), { message: 'Sign in first.' })
  }
  assert.equal(seen.length, 0)
})

test('the sign-in routes need no session and are answered by Better Auth, not FastAPI', async () => {
  const r = await get('/api/auth/sign-in/social')
  assert.equal(await r.text(), 'auth-handler')
  assert.equal(seen.length, 0)
})

test("the auth plugin's admin routes also need a verified staff account", async () => {
  for (const who of [null, person('ann'), person('x', 'x@example.org', false), person('y', 'y@example.org.evil.com')]) {
    const r = await get('/api/auth/admin/list-users', who)
    assert.equal(r.status, 403, JSON.stringify(who))
    assert.deepEqual(await r.json(), { message: 'Staff only.' })
  }
  assert.equal(await (await get('/api/auth/admin/list-users', staff())).text(), 'auth-handler')
})

test('platform stats are for staff only and come straight from Postgres', async () => {
  db = () => ({ rows: [{ users: 3, reports: 7 }] })
  for (const who of [null, person('ann')]) assert.equal((await get('/api/admin/stats', who)).status, 403)
  assert.equal(queries.length, 0)
  const r = await get('/api/admin/stats', staff())
  assert.equal(r.status, 200)
  assert.deepEqual(await r.json(), { users: 3, reports: 7 })
  assert.match(queries[0].sql, /from "user"/)
  assert.match(queries[0].sql, /from report/)
})

test('request bodies over 1 MB are refused before FastAPI sees them', async () => {
  const big = await fetch(url('/api/chat'), { method: 'POST', headers: { 'content-type': 'application/json', connection: 'close', ...person('big') },  // the early 413 drops the socket: don't pool it
                           body: JSON.stringify({ question: 'x'.repeat(1 << 20) }) })
  assert.equal(big.status, 413)
  assert.equal(seen.length, 0)
  assert.equal((await post('/api/chat', person('big'), { question: 'small' })).status, 200)
})

// ---------- the daily allowance ----------
test('report and claim checks share one daily allowance of 20 per person', async () => {
  reply = () => ({ body: `${STEP}\n` })
  const ann = person('cap-ann')
  for (let i = 0; i < 20; i++) assert.equal((await post(i % 2 ? '/api/claim' : '/api/report', ann)).status, 200, `check ${i + 1}`)
  const sent = seen.length
  for (const path of ['/api/report', '/api/claim']) {
    const r = await post(path, ann)
    assert.equal(r.status, 429, path)
    assert.deepEqual(await r.json(), { message: 'Daily limit of 20 checks reached.' })
  }
  assert.equal(seen.length, sent, 'a refused check never reaches Earth Engine or Gemini')
})

test("one person's allowance does not use up another's, and chat is not counted", async () => {
  reply = () => ({ body: `${STEP}\n` })
  const [cap, other] = [person('cap-carl'), person('cap-dee')]
  for (let i = 0; i < 20; i++) await post('/api/report', cap)
  assert.equal((await post('/api/report', cap)).status, 429)
  assert.equal((await post('/api/report', other)).status, 200)
  for (const path of ['/api/chat', '/api/compare', '/api/places', '/api/translate', '/api/transcribe']) assert.equal((await post(path, cap)).status, 200, path)
})

test('staff are exempt from the allowance', async () => {
  reply = () => ({ body: `${STEP}\n` })
  for (let i = 0; i < 25; i++) assert.equal((await post('/api/report', staff())).status, 200, `check ${i + 1}`)
})

test('the allowance starts again the next day', async () => {
  reply = () => ({ body: `${STEP}\n` })
  const ann = person('cap-eve')
  for (let i = 0; i < 20; i++) await post('/api/report', ann)
  assert.equal((await post('/api/report', ann)).status, 429)
  mock.timers.enable({ apis: ['Date'], now: Date.now() + 24 * 3600 * 1000 })
  assert.equal((await post('/api/report', ann)).status, 200)
  mock.timers.reset()
})

// ---------- saving reports as they stream ----------
test('a finished report is saved as it streams and the browser gets its id', async () => {
  reply = () => ({ body: `${STEP}\n${REPORT_LINE}\n` })
  const r = await post('/api/report', person('ann'), { lat: 13.75, lon: 100.5, text: 'Single professional' })
  assert.equal(r.status, 200)
  const [first, second, tail] = (await r.text()).split('\n')
  assert.equal(first, STEP)  // other events pass through byte for byte
  const saved = JSON.parse(second)
  assert.match(saved.id, /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/)
  assert.deepEqual({ ...saved, id: undefined }, { type: 'report', state: REPORT_STATE, id: undefined })
  assert.equal(tail, '')
  assert.equal(inserts().length, 1)
  assert.match(inserts()[0].sql, /insert into report \(id, user_id, lat, lon, state\) values \(\$1, \$2, \$3, \$4, \$5\)/)
  assert.deepEqual(inserts()[0].params, [saved.id, 'ann', 13.75, 100.5, REPORT_STATE])
  assert.deepEqual([seen[0].method, seen[0].url, JSON.parse(seen[0].body)], ['POST', '/api/report', { lat: 13.75, lon: 100.5, text: 'Single professional' }])
})

test('events that are not the final report are not stored', async () => {
  reply = () => ({ body: `${STEP}\n{"type": "say", "text": "{\\"type\\": \\"report\\""}\n{"type":"report","state":{"lat":1,"lon":2}}\n` })
  const r = await post('/api/report', person('ann'))
  assert.equal((await r.text()).split('\n').length, 4)
  assert.equal(inserts().length, 0)  // the third line has no space after "type":, so it is not Python's report line
})

test('if saving fails the person still gets the report, just without a link', async () => {
  const errors = mock.method(console, 'error', () => {})
  db = q => { if (q.sql.startsWith('insert')) throw new Error('db down'); return { rows: [] } }
  reply = () => ({ body: `${REPORT_LINE}\n` })
  const r = await post('/api/report', person('ann'))
  assert.equal((await r.text()).trimEnd(), REPORT_LINE)
  assert.equal(errors.mock.calls[0].arguments[0], 'report not saved')
})

test('a FastAPI error is passed through untouched and nothing is saved', async () => {
  reply = () => ({ status: 500, type: 'application/json', body: '{"detail":"boom"}' })
  const r = await post('/api/report', person('ann'))
  assert.equal(r.status, 500)
  assert.equal(await r.text(), '{"detail":"boom"}')
  assert.equal(inserts().length, 0)
})

// ---------- saved reports ----------
test("a person's own saved reports are listed newest first, 50 at most, nobody else's", async () => {
  const rows = [{ id: UUID, created_at: '2026-10-18T00:00:00Z', lat: 13.75, lon: 100.5, text: 'Single professional' }]
  db = () => ({ rows })
  assert.equal((await get('/api/reports')).status, 401)
  const r = await get('/api/reports', person('ann'))
  assert.deepEqual(await r.json(), rows)
  assert.deepEqual(queries[0].params, ['ann'])
  assert.match(queries[0].sql, /where user_id = \$1 order by created_at desc limit 50/)
  assert.match(queries[0].sql, /left\(state->>'text', 120\)/)  // a preview, not the whole report
})

test('a saved report opens by its id for any signed-in person, with the id added', async () => {
  db = () => ({ rows: [{ state: REPORT_STATE }] })
  const r = await get(`/api/reports/${UUID}`, person('someone-with-the-link'))
  assert.equal(r.status, 200)
  assert.deepEqual(await r.json(), { ...REPORT_STATE, id: UUID })
  assert.deepEqual(queries[0].params, [UUID])
  assert.equal((await get(`/api/reports/${UUID}`)).status, 401)
})

test('an unknown report is a plain 404', async () => {
  const r = await get(`/api/reports/${UUID}`, person('ann'))
  assert.equal(r.status, 404)
  assert.deepEqual(await r.json(), { message: 'No such report.' })
})

test('an id that is not a UUID never reaches the database', async () => {
  for (const id of ['abc', 'x'.repeat(36), `${UUID}; drop table report`, `${UUID}0`, '%2e%2e%2fetc', UUID.toUpperCase()]) {
    const r = await get(`/api/reports/${id}`, person('ann'))
    assert.equal(r.status, 404, id)
  }
  assert.equal(queries.length, 0)
})

// ---------- everything else goes to FastAPI ----------
test('other API calls pass straight through for signed-in people, body and answer both', async () => {
  reply = () => ({ body: '{"type": "answer", "text": "It is mostly March."}\n' })
  const r = await post('/api/chat', person('ann'), { question: 'Is it only March?', state: { lat: 1 } })
  assert.equal(await r.text(), '{"type": "answer", "text": "It is mostly March."}\n')
  assert.deepEqual([seen[0].method, seen[0].url, JSON.parse(seen[0].body)], ['POST', '/api/chat', { question: 'Is it only March?', state: { lat: 1 } }])
  assert.equal(inserts().length, 0)
})
