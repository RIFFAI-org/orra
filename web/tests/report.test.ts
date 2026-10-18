// src/report.tsx: the agent-log summaries, the flood-claim verdict, streaming, translation and what the report screens show.
// report.tsx has JSX, which Node cannot run by itself, so it is loaded through Vite's own SSR loader (already a dependency) and
// the components are rendered to static HTML. A few private helpers are exported only inside this test's copy of the module.
// Local dev tests: from web/, run `node --test "tests/*.test.ts"`.
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { after, afterEach, beforeEach, describe, mock, test } from 'node:test'
import { type ReactElement, createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

process.env.TZ = 'UTC'  // day() prints in local time

// ---------- load the modules the way the browser sees them, in Node ----------
const define = (name: string, value: unknown) => Object.defineProperty(globalThis, name, { value, configurable: true, writable: true })
define('document', { documentElement: { lang: '' } })
define('navigator', { language: 'en-US' })
define('localStorage', { getItem: () => null, setItem: () => {} })
define('location', { origin: 'https://orra.test', pathname: '/' })

const INTERNALS = ['claimSays', 'refs', 'pct', 'signed', 'says', 'changes', 'nearest']
const vite = await createServer({
  configFile: false, root: fileURLToPath(new URL('..', import.meta.url)), logLevel: 'silent', appType: 'custom',
  server: { middlewareMode: true, watch: null, hmr: false }, optimizeDeps: { noDiscovery: true, include: [] },
  plugins: [{ name: 'expose-internals', enforce: 'pre', transform: (code, id) => id.endsWith('/src/report.tsx') ? `${code}\nexport { ${INTERNALS.join(', ')} }\n` : null }],
})
after(() => vite.close())

type Rec = Record<string, any>
type Pair = [Rec, string | number]
type Internals = {
  claimSays: (r: Rec) => [string, string], refs: (x: Rec) => Pair[], pct: (x?: number | null) => string, signed: (x: number) => string,
  says: (c: Rec) => string, changes: (c?: Record<string, number>) => string, nearest: (r: Rec) => string }
const I18N = await vite.ssrLoadModule('/src/i18n.ts') as typeof import('../src/i18n.ts')
const R = await vite.ssrLoadModule('/src/report.tsx') as typeof import('../src/report.tsx') & Internals
type State = import('../src/report.tsx').State

beforeEach(() => I18N.setLang('en'))
afterEach(() => mock.restoreAll())

// ---------- helpers ----------
const plain = (markup: string) => markup.replace(/<[^>]*>/g, ' ').replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&')
  .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/\s+/g, ' ').trim()
const all = (markup: string, re: RegExp) => [...markup.matchAll(re)].map(m => plain(m[1]))
const render = (el: ReactElement) => renderToStaticMarkup(el)
const noop = () => {}

const chapter = (key: string, level: string, over: Rec = {}) => ({
  key, level, headline: `${key} headline`, detail: `${key} detail`,
  evidence: [{ claim: `${key} claim.`, value: `${key} value`, source: `${key} source`, date: '2024' }], ...over })
const EVIDENCE: Rec[] = [
  { tool: 'water_history', args: {}, result: { passes: 120, flood_events: 2, hand_m: 4.2, worst: { time: '2024-09-15T12:00:00+00:00', share_flooded_1km: 0.31 } } },
  { tool: 'flood_day_images', args: { event_time: 'small' }, result: { share_flooded_1km: 0.1, pass_time: '2024-07-01T12:00:00+00:00', images: { before: 'SMALLB', after: 'SMALLA' }, visual_description: 'small flood' } },
  { tool: 'flood_day_images', args: { event_time: 'big' }, result: { share_flooded_1km: 0.31, pass_time: '2024-09-15T12:00:00+00:00', images: { before: 'BIGB', after: 'BIGA' }, visual_description: 'water east of the ring' } },
  { tool: 'flood_day_images', args: {}, result: { error: 'x', share_flooded_1km: 0.9, images: { before: 'ERRB', after: 'ERRA' } } },
  { tool: 'heat', args: {}, result: { delta_c: 2.1, scenes: 14, images: { heat: 'HEATPNG' } } },
  { tool: 'fire', args: { radius_km: 50 }, result: { radius_km: 50, dry_season_fire_days_radius: 400, dry_season_fire_days_5km: 40, by_month_last_5y_radius: { Jan: 9 } } },
  { tool: 'fire', args: { radius_km: 20 }, result: { radius_km: 20, dry_season_fire_days_radius: 180, dry_season_fire_days_5km: 40,
    by_month_last_5y_radius: { Jan: 1, Feb: 3, Mar: 61, Apr: 20, May: 0, Jun: 0, Jul: 0, Aug: 0, Sep: 0, Oct: 0, Nov: 0, Dec: 2 } } },
  { tool: 'air', args: {}, result: { days: 30, pm25_mean_ug_m3: 41.2, pm25_worst_day: { date: '2026-10-02T00:00:00Z', ug_m3: 88.1 }, days_over_thai_standard: 5, thai_standard_ug_m3: 37.5 } },
  { tool: 'change', args: {}, result: { change_points_1km: { built: 8, trees: -10 }, before_period: 'Nov 2017 - Apr 2018', after_period: 'Nov 2025 - Apr 2026',
    images: { before: 'CHB', after: 'CHA' }, visual_description: 'new roofs to the east' } },
  { tool: 'nearby', args: {}, result: { nearest_km: { shops: [{ name: 'Big C', km: 0.4 }], hospital: [] } } },
]
const profile = { who: 'A family with a baby', why: 'Flood matters most.', flood: 'high', heat: 'low', smoke: 'medium', change: 'low', nearby: 'low' }
const stateOf = (chapters: Rec[], over: Rec = {}) => ({
  lat: 20.3692, lon: 99.9957, text: 'I have a baby', profile: structuredClone(profile), evidence: structuredClone(EVIDENCE),
  report: { verdict: ['Avoid the ground floor', 'Water reached the pin in 2024.'], chapters, overall: 'High', checks: ['Ask about flood marks', 'Ask about the drainage'],
            questions: ['Is it only March?'], lender_note: 'Collateral note.' }, ...over }) as unknown as State
const reportOf = (chapters: Rec[], patch: Rec = {}) => ({ ...stateOf(chapters).report, ...patch })
const FIVE = () => [chapter('water', 'High'), chapter('heat', 'Medium'), chapter('smoke', 'Low'), chapter('change', 'Not enough evidence'), chapter('nearby', 'Low')]

// ---------- small helpers ----------
test('clock shows minutes and zero-padded seconds', () => {
  assert.deepEqual([0, 9, 65, 600, 3725].map(s => R.clock(s)), ['0:00', '0:09', '1:05', '10:00', '62:05'])
})

test('day prints a Gregorian date in the page language', () => {
  assert.match(R.day('2024-09-15T12:00:00Z'), /^Sep 15, 2024$/)
  assert.match(R.day(new Date('2024-09-15T12:00:00Z')), /2024/)
  I18N.setLang('th')
  assert.match(R.day('2024-09-15T12:00:00Z'), /2024/)
  assert.doesNotMatch(R.day('2024-09-15T12:00:00Z'), /2567/)
})

test('pct, signed and the footer', () => {
  assert.deepEqual([0.314, 0, 1, 0.005, null, undefined].map(x => R.pct(x)), ['31%', '0%', '100%', '1%', '–', '–'])
  assert.deepEqual([2.1, 0, -1.5].map(x => R.signed(x)), ['+2.1', '0', '-1.5'])
  assert.match(R.footer(), /^Decision support from public satellite data\. Not a formal appraisal/)
  assert.match(R.footer(), /Copernicus Sentinel.*Landsat.*NASA FIRMS.*Dynamic World.*JRC.*MERIT Hydro.*ERA5-Land.*Google Maps.*air quality data from Google/s)
})

test('every tool belongs to a chapter and has a readable query name', () => {
  assert.deepEqual(R.TOOL_CH, { water_history: 'water', flood_day_images: 'water', water_on_date: 'water', heat: 'heat', fire: 'smoke', air: 'smoke',
                                change: 'change', nearby: 'nearby', find_places: 'nearby' })
  assert.deepEqual(Object.keys(R.NAMES).sort(), Object.keys(R.TOOL_CH).sort())
})

test('query names show the radius, date or place that was asked for', () => {
  const N = R.NAMES
  assert.deepEqual([{ radius_km: 20 }, { radius_km: 21 }, { radius_km: 50 }, {}].map(a => N.fire(a)), [
    'NASA FIRMS fire detections within 20 km', 'NASA FIRMS fire detections within 50 km', 'NASA FIRMS fire detections within 50 km',
    'NASA FIRMS fire detections within 20 km'])
  assert.equal(N.flood_day_images({ event_time: '2024-09-15T23:16+00:00' }), 'Sentinel-1 radar images, pass of Sep 15, 2024')
  assert.equal(N.flood_day_images({}), 'Sentinel-1 radar images, pass of ?')
  assert.equal(N.water_on_date({ date: '2024-09-12' }), 'Sentinel-1 radar, the pass nearest Sep 12, 2024')
  assert.equal(N.water_on_date({}), 'Sentinel-1 radar, the pass nearest ?')
  assert.equal(N.find_places({ query: 'Big C' }), 'Google Maps: "Big C" near the site')
  assert.equal(N.find_places({}), 'Google Maps: "" near the site')
  I18N.setLang('th')
  assert.match(N.fire({ radius_km: 50 }), /50 กม\./)
})

// ---------- what the agent log says about each result ----------
describe('summary', () => {
  const D = '2024-09-15T12:00:00+00:00'
  test('flood history: events and the worst one, or none; height unknown shows a dash', () => {
    assert.equal(R.summary('water_history', { flood_events: 2, passes: 120, hand_m: 4.2, worst: { time: D, share_flooded_1km: 0.314 } }),
      '2 flood event(s) in 120 radar passes; worst Sep 15, 2024 (31% of nearby flood-prone ground); HAND 4.2 m')
    assert.equal(R.summary('water_history', { flood_events: 0, passes: 120, hand_m: 4.2 }), 'no flood events in 120 radar passes; HAND 4.2 m')
    assert.equal(R.summary('water_history', { flood_events: 0, passes: 0, hand_m: null }), 'no flood events in 0 radar passes; HAND – m')
  })

  test('a day check says whether water reached the site, or gives the reason there is no answer', () => {
    for (const tool of ['flood_day_images', 'water_on_date']) {
      assert.equal(R.summary(tool, { water_at_pin: true, share_flooded_300m: 0.5 }), 'water at the site: yes; 50% of ground within 300 m flooded')
      assert.equal(R.summary(tool, { water_at_pin: false, share_flooded_300m: 0 }), 'water at the site: no; 0% of ground within 300 m flooded')
      assert.equal(R.summary(tool, { water_at_pin: null, share_flooded_300m: null }), 'water at the site: unknown; – of ground within 300 m flooded')
      assert.equal(R.summary(tool, { note: 'No radar pass within 6 day(s) of 2024-09-12.', water_at_pin: true }), 'No radar pass within 6 day(s) of 2024-09-12.')
    }
  })

  test('heat is signed against the area average, or the note when there is no data', () => {
    assert.equal(R.summary('heat', { delta_c: 2.1, scenes: 14 }), '+2.1 °C vs the 2 km average (14 scenes)')
    assert.equal(R.summary('heat', { delta_c: -1.5, scenes: 3 }), '-1.5 °C vs the 2 km average (3 scenes)')
    assert.equal(R.summary('heat', { delta_c: 0, scenes: 3 }), '0 °C vs the 2 km average (3 scenes)')
    assert.equal(R.summary('heat', { note: 'No cloud-free Landsat pixels at this pin in the hot season.' }), 'No cloud-free Landsat pixels at this pin in the hot season.')
  })

  test('fire, change, nearby places, a named place and air', () => {
    assert.equal(R.summary('fire', { dry_season_fire_days_radius: 180, radius_km: 20, dry_season_fire_days_5km: 40 }), '180 dry-season fire-days within 20 km, 40 within 5 km')
    assert.equal(R.summary('change', { change_points_1km: { built: 8, trees: -10 } }), 'built +8 pts, trees -10 pts')
    assert.equal(R.summary('change', { change_points_1km: {} }), 'little land-cover change')
    assert.equal(R.summary('change', { change_points_1km: null }), 'little land-cover change')
    assert.equal(R.summary('change', { change_points_1km: { mystery: 1 } }), 'mystery +1 pts')
    assert.equal(R.summary('nearby', { nearest_km: { shops: [{ name: 'Big C', km: 0.4 }], hospital: [], school: [{ name: 'S', km: 1.2 }] } }), 'Shops 0.4 km, Hospital –, School 1.2 km')
    assert.equal(R.summary('nearby', {}), '')
    assert.equal(R.summary('find_places', { nearest: [{ name: 'Big C Supercenter', km: 1.2 }, { name: 'Far', km: 9 }] }), 'Big C Supercenter · 1.2 km')
    assert.equal(R.summary('find_places', { nearest: [] }), 'no match on Google Maps')
    assert.equal(R.summary('air', { days: 30, pm25_mean_ug_m3: 41.2, days_over_thai_standard: 5 }), 'PM2.5 averaged 41.2 µg/m³; 5 of 30 days over the Thai standard')
    assert.equal(R.summary('air', { note: 'No PM2.5 estimate for this location in the last 30 days.' }), 'No PM2.5 estimate for this location in the last 30 days.')
  })

  test('a failed tool shows its error whatever the tool, and an unknown tool shows nothing', () => {
    for (const tool of ['water_history', 'heat', 'fire', 'air', 'nearby', 'nothing']) assert.equal(R.summary(tool, { error: 'EEException: quota' }), 'failed: EEException: quota')
    assert.equal(R.summary('nothing', { a: 1 }), '')
  })

  test('the same results read in Thai when the page is Thai', () => {
    I18N.setLang('th')
    assert.equal(R.summary('fire', { dry_season_fire_days_radius: 180, radius_km: 20, dry_season_fire_days_5km: 40 }),
      'วันพบไฟช่วงหน้าแล้ง 180 วันในรัศมี 20 กม., 40 วันในรัศมี 5 กม.')
    assert.equal(R.summary('change', { change_points_1km: { built: 8 } }), 'สิ่งปลูกสร้าง +8 จุด')
    assert.equal(R.summary('find_places', { nearest: [] }), 'ไม่พบใน Google Maps')
  })
})

test('changes and nearest list what moved and how far each daily need is', () => {
  assert.equal(R.changes(undefined), '')
  assert.equal(R.changes({ flooded_vegetation: 2, bare: -1 }), 'flooded vegetation +2 pts, bare -1 pts')
  assert.equal(R.nearest({ nearest_km: { transit: [{ name: 'BTS', km: 0.2 }, { name: 'Bus', km: 0.5 }], school: [] } }), 'Transit 0.2 km, School –')
})

// ---------- what the server is sent back ----------
test('slim keeps what the server needs and drops every image without touching the original', () => {
  const s = stateOf(FIVE(), { id: 'abc', lang: 'th' })
  const out = R.slim(s) as Rec
  assert.deepEqual(Object.keys(out), ['lat', 'lon', 'profile', 'report', 'evidence'])
  assert.equal(out.evidence.length, EVIDENCE.length)
  assert.ok(out.evidence.every((e: Rec) => !('images' in e.result)))
  assert.deepEqual(out.evidence[4], { tool: 'heat', args: {}, result: { delta_c: 2.1, scenes: 14 } })
  assert.ok(s.evidence[4].result.images)
})

// ---------- streaming ----------
const enc = new TextEncoder()
function ndjson(lines: object[], cut = 7, status = 200) {
  const bytes = enc.encode(lines.map(l => JSON.stringify(l)).join('\n') + '\n')
  return new Response(new ReadableStream<Uint8Array>({ start(c) { for (let i = 0; i < bytes.length; i += cut) c.enqueue(bytes.slice(i, i + cut)); c.close() } }), { status })
}
function fakeFetch(answer: (url: string, body: Rec) => Response | Promise<Response>) {
  const calls: { url: string, method?: string, type: unknown, body: Rec }[] = []
  mock.method(globalThis, 'fetch', (async (url: string, init: RequestInit) => {
    const body = JSON.parse(String(init.body))
    calls.push({ url, method: init.method, type: (init.headers as Rec)['content-type'], body })
    return answer(url, body)
  }) as typeof fetch)
  return calls
}

describe('stream', () => {
  test('reads one event per line however the network cuts the bytes, Thai text included', async () => {
    const events = [{ type: 'step', text: 'กำลังวิเคราะห์น้ำ' }, { type: 'report', state: { lat: 1 } }, { type: 'x', n: 3 }]
    for (const cut of [1, 2, 5, 64]) {
      fakeFetch(() => ndjson(events, cut))
      const got: unknown[] = []
      await R.stream('/api/report', {}, ev => got.push(ev))
      assert.deepEqual(got, events, `cut every ${cut} bytes`)
      mock.restoreAll()
    }
  })

  test('posts the body as JSON together with the page language', async () => {
    const calls = fakeFetch(() => ndjson([]))
    await R.stream('/api/chat', { question: 'q' }, noop)
    I18N.setLang('th')
    await R.stream('/api/chat', { question: 'q' }, noop)
    assert.deepEqual(calls, [{ url: '/api/chat', method: 'POST', type: 'application/json', body: { question: 'q', lang: 'en' } },
                             { url: '/api/chat', method: 'POST', type: 'application/json', body: { question: 'q', lang: 'th' } }])
  })

  test('blank lines are ignored', async () => {
    fakeFetch(() => new Response('{"type":"a"}\n\n{"type":"b"}\n'))
    const got: Rec[] = []
    await R.stream('/x', {}, ev => got.push(ev))
    assert.deepEqual(got.map(e => e.type), ['a', 'b'])
  })

  test('signed out, over the daily limit and server errors become messages people can act on', async () => {
    for (const [status, message] of [[401, 'You are signed out. Sign in again to continue.'], [429, "You have used today's checks. Try again tomorrow."],
                                     [500, 'The server answered 500.'], [413, 'The server answered 413.']] as const) {
      fakeFetch(() => new Response('{}', { status }))
      await assert.rejects(R.stream('/x', {}, noop), new Error(message))
      mock.restoreAll()
    }
    I18N.setLang('th')
    fakeFetch(() => new Response('{}', { status: 429 }))
    await assert.rejects(R.stream('/x', {}, noop), /ลองใหม่พรุ่งนี้/)
  })
})

// ---------- switching the report language ----------
describe('translate', () => {
  const texts = (x: State) => R.refs(x).map(([o, k]) => o[k])
  const thai = (xs: string[]) => xs.map(t => `ไทย:${t}`)

  test('only the sentences Gemini wrote are sent, in a fixed order, and levels, numbers and sources stay', () => {
    const s = stateOf([chapter('water', 'High'), chapter('heat', 'Low', { evidence: [] })])
    assert.deepEqual(texts(s), [
      'A family with a baby', 'Flood matters most.', 'Collateral note.',
      'Avoid the ground floor', 'Water reached the pin in 2024.', 'Ask about flood marks', 'Ask about the drainage', 'Is it only March?',
      'water headline', 'water detail', 'water claim.', 'water value', 'heat headline', 'heat detail',
      'small flood', 'water east of the ring', 'new roofs to the east'])  // image readings last
    assert.deepEqual(texts({ pick: 'A', reason: 'Higher ground.', tradeoffs: ['t1', 't2'] } as unknown as State), ['Higher ground.', 't1', 't2'])
  })

  test('the translated sentences are written back in place and the report is marked as Thai', async () => {
    const s = stateOf(FIVE())
    const before = texts(s)
    I18N.setLang('th')
    const calls = fakeFetch(() => ndjson([{ type: 'translated', texts: thai(before) }]))
    await R.translate(s)
    assert.deepEqual(calls.map(c => [c.url, c.body]), [['/api/translate', { texts: before, lang: 'th' }]])
    assert.deepEqual(texts(s), thai(before))
    assert.equal(s.lang, 'th')
    assert.equal(s.report.chapters[0].level, 'High')  // not a sentence: untouched
    assert.equal(s.report.chapters[0].evidence[0].source, 'water source')
  })

  test('switching back and forth restores each version exactly without asking again', async () => {
    const s = stateOf(FIVE())
    const english = texts(s)
    I18N.setLang('th')
    const calls = fakeFetch(() => ndjson([{ type: 'translated', texts: thai(english) }]))
    await R.translate(s)
    I18N.setLang('en')
    await R.translate(s)
    assert.deepEqual(texts(s), english)
    assert.equal(s.lang, 'en')
    I18N.setLang('th')
    await R.translate(s)
    assert.deepEqual(texts(s), thai(english))
    assert.equal(calls.length, 1)
  })

  test('a new sentence after the translation, such as a chat image reading, means translating afresh', async () => {
    const s = stateOf(FIVE())
    const english = texts(s)
    I18N.setLang('th')
    const calls = fakeFetch((_, body) => ndjson([{ type: 'translated', texts: body.texts.map((t: string) => `${body.lang}:${t}`) }]))
    await R.translate(s)
    s.evidence.push({ tool: 'change', result: { visual_description: 'a chat reading' } })
    I18N.setLang('en')
    await R.translate(s)
    assert.equal(calls.length, 2)
    assert.equal(calls[1].body.texts.length, english.length + 1)
    assert.equal(s.evidence.at(-1)!.result.visual_description, 'en:a chat reading')
  })

  test('a compare result translates the same way', async () => {
    const cmp = { pick: 'A', reason: 'Higher ground.', tradeoffs: ['Water: A is safer'] } as Rec
    I18N.setLang('th')
    fakeFetch(() => ndjson([{ type: 'translated', texts: ['พื้นที่สูงกว่า', 'น้ำ: A ปลอดภัยกว่า'] }]))
    await R.translate(cmp as never)
    assert.deepEqual(cmp, { pick: 'A', reason: 'พื้นที่สูงกว่า', tradeoffs: ['น้ำ: A ปลอดภัยกว่า'], lang: 'th' })
  })

  test('if the person switches again while it is being translated, nothing is written', async () => {
    const s = stateOf(FIVE())
    const english = texts(s)
    I18N.setLang('th')
    let release!: () => void
    const gate = new Promise<void>(ok => { release = ok })
    fakeFetch(async () => { await gate; return ndjson([{ type: 'translated', texts: thai(english) }]) })
    const pending = R.translate(s)
    await new Promise(ok => setTimeout(ok, 5))
    I18N.setLang('en')
    release()
    await pending
    assert.deepEqual(texts(s), english)
    assert.equal(s.lang, undefined)
  })

  test('a failed translation says why and leaves the report as it was', async () => {
    const s = stateOf(FIVE())
    const english = texts(s)
    I18N.setLang('th')
    fakeFetch(() => ndjson([{ type: 'error', text: 'ValueError: translation returned 1 of 9 strings' }]))
    await assert.rejects(R.translate(s), /translation returned 1 of 9 strings/)
    fakeFetch(() => ndjson([{ type: 'step' }]))
    await assert.rejects(R.translate(s), /No translation came back\./)
    fakeFetch(() => new Response('', { status: 500 }))
    await assert.rejects(R.translate(s), /เซิร์ฟเวอร์ตอบกลับ 500/)  // the page is Thai at this point
    assert.deepEqual(texts(s), english)
    assert.equal(s.lang, undefined)
  })
})

// ---------- the flood-claim verdict ----------
describe('claimSays', () => {
  const T = () => I18N.T
  const when = '2024-09-12T11:00:00+00:00'
  const D = 'Sep 12, 2024'
  const base = { pass_time: when, days_from_request: 0, water_at_pin: null, share_flooded_300m: null, pin_built_up: false }

  test('no pass in the window can neither confirm nor rule out a flood', () => {
    assert.deepEqual(R.claimSays({ note: 'No radar pass within 6 day(s) of 2024-09-12.' }), ['unk', T().claimNoPass])
  })

  test('water at the pin is consistent with a flood, even on built-up ground or a day away', () => {
    assert.deepEqual(R.claimSays({ ...base, water_at_pin: true, days_from_request: 2 }), ['ok', T().claimWet(D, 2)])
    assert.deepEqual(R.claimSays({ ...base, water_at_pin: true, pin_built_up: true }), ['ok', T().claimWet(D, 0)])
  })

  test('water within 300 m but not at the pin is a warning from 5 % up', () => {
    assert.deepEqual(R.claimSays({ ...base, water_at_pin: false, share_flooded_300m: 0.05 }), ['warn', T().claimNear('5%', D, 0)])
    assert.deepEqual(R.claimSays({ ...base, water_at_pin: false, share_flooded_300m: 0.31, pin_built_up: true, days_from_request: 3 }),
      ['warn', T().claimNear('31%', D, 3)])  // checked before built-up and late
    assert.notEqual(R.claimSays({ ...base, water_at_pin: false, share_flooded_300m: 0.049 })[0], 'warn')
  })

  test('built-up ground cannot show water between buildings, so a dry reading proves nothing', () => {
    assert.deepEqual(R.claimSays({ ...base, water_at_pin: false, pin_built_up: true }), ['unk', T().claimBuilt(D, 0)])
    assert.deepEqual(R.claimSays({ ...base, water_at_pin: false, pin_built_up: true, days_from_request: 4 }), ['unk', T().claimBuilt(D, 4)])
  })

  test('a pass more than a day from the claimed date cannot rule a flood out either', () => {
    assert.deepEqual(R.claimSays({ ...base, water_at_pin: false, days_from_request: 1.1 }), ['unk', T().claimLate(D, 1.1)])
    assert.deepEqual(R.claimSays({ ...base, water_at_pin: false, days_from_request: -2 }), ['unk', T().claimLate(D, -2)])
  })

  test('only open ground, a pass within a day and a dry pin says to review the claim', () => {
    for (const days of [0, 0.5, 1, -1]) assert.deepEqual(R.claimSays({ ...base, water_at_pin: false, days_from_request: days }), ['bad', T().claimDry(D, days)], `${days} days`)
    assert.ok(T().claimDry(D, 0).endsWith('Review the claim.'))
  })

  test('radar that could not judge the exact spot says so', () => {
    assert.deepEqual(R.claimSays(base), ['unk', T().claimMasked(D)])
    assert.deepEqual(R.claimSays({ ...base, share_flooded_300m: 0.01 }), ['unk', T().claimMasked(D)])
  })

  test('the verdict reads in Thai when the page is Thai', () => {
    I18N.setLang('th')
    const [level, text] = R.claimSays({ ...base, water_at_pin: false })
    assert.equal(level, 'bad')
    assert.match(text, /ไม่พบน้ำที่ตำแหน่งนี้/)
  })
})

// ---------- the screens ----------
describe('Report (the homebuyer screen)', () => {
  const view = (chapters = FIVE(), over: Rec = {}) => render(createElement(R.Report, { state: stateOf(chapters, over), onPdf: noop, onLender: noop }))

  test('leads with the call for this person', () => {
    const m = view()
    assert.deepEqual(all(m, /<h2 class="lead">(.*?)<\/h2>/g), ['Avoid the ground floor'])
    assert.match(m, /<p class="who">For: A family with a baby<\/p>/)
    assert.deepEqual(all(view(FIVE(), { report: reportOf(FIVE(), { verdict: [] }) }), /<h2 class="lead">(.*?)<\/h2>/g), [''])
  })

  test('never prints a rating word: colour, icon and plain words only', () => {
    for (const lang of ['en', 'th'] as const) {
      I18N.setLang(lang)
      const text = plain(view())
      assert.doesNotMatch(text, /\b(High|Medium|Low)\b/, lang)
      assert.doesNotMatch(text, /(สูง|ปานกลาง|ต่ำ)/, lang)
    }
  })

  test('shows concerns first, then what to check, then what is fine, then what could not be checked', () => {
    assert.deepEqual(all(view(), /<h3 class="tech">(.*?)<\/h3>/g), ['What to watch (2)', 'Before you sign', 'No concern found', "Couldn't check"])
    assert.deepEqual(all(view([chapter('heat', 'Low')]), /<h3 class="tech">(.*?)<\/h3>/g), ['Before you sign', 'No concern found'])
    assert.deepEqual(all(view([chapter('heat', 'Low')], { report: reportOf([chapter('heat', 'Low')], { checks: [] }) }), /<h3 class="tech">(.*?)<\/h3>/g), ['No concern found'])
  })

  test('orders concerns by seriousness, then by what this person cares about', () => {
    // water: Medium and flood weight high; heat: Medium and heat weight low; smoke: High
    const m = view([chapter('heat', 'Medium'), chapter('water', 'Medium'), chapter('smoke', 'High')])
    assert.deepEqual(all(m, /<b class="fname">(.*?)<\/b>/g), ['Smoke and fire', 'Water', 'Heat'])
  })

  test('each concern shows its mark as a colour class, an icon and words together', () => {
    const m = view(FIVE())
    for (const [cls, words] of [['bad', 'Serious concern'], ['warn', 'Worth checking'], ['ok', 'No concern found'], ['unk', 'Not enough evidence']]) {
      assert.match(m, new RegExp(`<span class="mark ${cls}"><svg[^>]*>.*?</svg><span class="sr">${words}</span></span>`), cls)
    }
    assert.match(m, /<details class="finding bad big">/)
    assert.match(m, /<details class="finding ok">/)
  })

  test('a chapter the guardrail emptied says there is not enough evidence instead of its old headline', () => {
    const m = view([chapter('change', 'Not enough evidence', { headline: 'Old confident headline', evidence: [] })])
    assert.match(plain(m), /Not enough satellite evidence to say\./)
    assert.doesNotMatch(m, /Old confident headline/)
  })

  test('the evidence is one click away with its source and date, claims without the trailing full stop', () => {
    const m = view([chapter('water', 'High')])
    assert.match(m, /<li>water claim: <b>water value<\/b><span class="src">water source · 2024<\/span><\/li>/)
    assert.match(plain(m), /water value · water claim/)  // the first fact beside the concern
  })

  test('shows the images, the bars and the tables the evidence supports', () => {
    const m = view([chapter('water', 'High'), chapter('heat', 'High'), chapter('smoke', 'High'), chapter('change', 'High'), chapter('nearby', 'High')])
    // water: the strongest flood pass, never a failed result
    assert.match(m, /src="data:image\/png;base64,BIGB"/)
    assert.match(m, /src="data:image\/png;base64,BIGA"/)
    assert.doesNotMatch(m, /SMALLA|ERRA/)
    assert.match(plain(m), /T1 · Sep 15, 2024/)
    assert.match(plain(m), /Image interpretation · Gemini water east of the ring/)
    // heat, change
    assert.match(m, /base64,HEATPNG/)
    assert.match(plain(m), /T0 · Nov 2017 - Apr 2018/)
    assert.match(m, /base64,CHA/)
    assert.match(plain(m), /new roofs to the east/)
    // smoke: the nearest search ring, the peak month named, air with its attribution
    assert.match(m, /aria-label="Fire-days by month within 20 km over 5 years; highest in March with 61\."/)
    assert.equal([...m.matchAll(/<div class="peak"/g)].length, 1)
    assert.equal([...m.matchAll(/<div [^>]*title="\d+ fire-days"/g)].length, 12)
    assert.match(plain(m), /Fire-days by month within 20 km, last 5 years/)
    assert.match(plain(m), /PM2\.5, 30-day average 41\.2 µg\/m³ Worst day Oct 2, 2026 · 88\.1 µg\/m³ Over the Thai standard \(37\.5 µg\/m³\) 5 of 30 days/)
    assert.match(plain(m), /Includes air quality data from Google/)
    // nearby: the nearest of each need, with the Google attribution
    assert.match(plain(m), /Shops Big C · 0\.4 km Hospital None within 10 km/)
    assert.match(plain(m), /Google Maps/)
  })

  test('shows no figure for a tool that did not run', () => {
    const m = view([chapter('water', 'High'), chapter('smoke', 'High'), chapter('nearby', 'High')], { evidence: [] })
    assert.doesNotMatch(m, /<img|class="bars"|class="near"/)
  })

  test('the same screen in Thai', () => {
    I18N.setLang('th')
    const text = plain(view())
    assert.match(text, /เรื่องที่ควรระวัง \(2\)/)
    assert.match(text, /ก่อนเซ็นสัญญา/)
    assert.match(text, /สำหรับ: A family with a baby/)
  })
})

describe('Pdf (the full report)', () => {
  const view = (over: Rec = {}) => render(createElement(R.Pdf, { state: stateOf(FIVE(), over), label: 'Site A' }))

  test('names the place, the person, what they wrote and every chapter in order', () => {
    const text = plain(view())
    assert.match(text, /Site A · full report/)
    assert.match(text, /Location 20\.3692, 99\.9957/)
    assert.match(text, /For: A family with a baby You wrote: “I have a baby”/)
    assert.match(text, /Avoid the ground floor Water reached the pin in 2024\./)
    assert.match(text, /Before you sign Ask about flood marks Ask about the drainage/)
    assert.deepEqual(all(view(), /<span class="num">(\d\d)<\/span>/g), ['01', '02', '03', '04', '05'])
    assert.deepEqual(all(view(), /<h3 class="disp">(.*?)<\/h3>/g), ['Water', 'Heat', 'Smoke and fire', 'Change around you', 'Daily needs nearby'])
    assert.match(text, /For banks and insurers Collateral note\./)
    assert.match(text, /Decision support from public satellite data\./)
  })

  test('also keeps rating words off the page', () => {
    assert.doesNotMatch(plain(view()), /\b(High|Medium|Low)\b/)
  })

  test('only a saved report carries the link to check it against the original', () => {
    assert.match(plain(view({ id: 'a1b2' })), /Check against the original: https:\/\/orra\.test\/r\/a1b2 \(Google sign-in\)/)
    assert.doesNotMatch(plain(view()), /Check against the original/)
  })

  test('the second verdict line is only printed when there is one', () => {
    assert.doesNotMatch(plain(view({ report: reportOf([], { verdict: ['Only the call'] }) })), /Water reached/)
  })
})

describe('Lender (the bank and insurer brief)', () => {
  const view = (over: Rec = {}, chapters = FIVE()) => render(createElement(R.Lender, { state: stateOf(chapters, over), onBack: noop }))
  const rows = (m: string) => [...m.matchAll(/<tr><td>([^<]*)<\/td><td>(.*?)<\/td><td>(.*?)<\/td><td>(.*?)<\/td><\/tr>/g)].map(r => r.slice(1).map(plain))

  test('keeps its rating words, unlike the homebuyer screens', () => {
    const m = view()
    assert.match(plain(m), /\bHigh\b/)
    assert.deepEqual(rows(m).map(r => r[1]), ['High', 'Medium', 'Low', 'Not enough evidence', 'Low'])
    assert.match(m, /<span class="lvl High big">/)
  })

  test('one row per chapter with its key evidence and the first source cited', () => {
    assert.deepEqual(rows(view()), [
      ['Water', 'High', '2 radar flood event(s) since 2018; worst Sep 15, 2024, 31% of nearby flood-prone ground; HAND 4.2 m', 'water source'],
      ['Heat', 'Medium', '+2.1 °C vs 2 km average (hot season)', 'heat source'],
      ['Smoke and fire', 'Low', '40 fire-days within 5 km, 180 within 20 km (Jan–Apr, 5 years)', 'smoke source'],
      ['Change around you', 'Not enough evidence', 'built +8 pts, trees -10 pts', 'change source'],
      ['Daily needs nearby', 'Low', 'Shops 0.4 km, Hospital –', 'nearby source']])
  })

  test('a hazard that was not assessed says so, and a chapter with no evidence has no source', () => {
    const bare = chapter('water', 'Not enough evidence', { evidence: [] })
    assert.deepEqual(rows(view({ evidence: [] }, [bare, chapter('heat', 'Low')])).map(r => [r[2], r[3]]), [['Not assessed', '–'], ['Not assessed', 'heat source']])
  })

  test('flood history without a worst event still reads, and a little change reads as little', () => {
    const evidence = [{ tool: 'water_history', args: {}, result: { passes: 80, flood_events: 0, hand_m: null } },
                      { tool: 'change', args: {}, result: { change_points_1km: {} } }]
    const [water, , , change] = rows(view({ evidence }, FIVE()))
    assert.equal(water[2], '0 radar flood event(s) since 2018; HAND – m')
    assert.equal(change[2], 'Little change')
  })

  test('shows the radar and optical pictures when there are some, with the location and a link for a saved report', () => {
    const m = view({ id: 'a1b2' })
    assert.match(m, /base64,BIGA/)
    assert.match(plain(m), /SAR · Sep 15, 2024/)
    assert.match(m, /base64,CHA/)
    assert.match(plain(m), /Optical · Nov 2025 - Apr 2026/)
    assert.match(plain(m), /Check against the original: https:\/\/orra\.test\/r\/a1b2/)
    assert.doesNotMatch(view({ evidence: [] }), /<img alt="(Radar|Location today)/)
  })

  test('has the overall level, the lender note and a claim check limited to the radar record', () => {
    const m = view()
    assert.match(m, /<span class="lvl High big">/)
    assert.match(plain(m), /Collateral location risk summary/)
    assert.match(plain(m), /Collateral note\./)
    const date = /<input type="date"[^>]*>/.exec(m)![0]  // the radar record starts 12 Jan 2018 and ends today
    assert.match(date, /name="date"/)
    assert.match(date, /required/)
    assert.match(date, /min="2018-01-12"/)
    assert.ok(date.includes(`max="${new Date().toISOString().slice(0, 10)}"`), date)
    assert.match(plain(m), /Claimed flood date Check/)
    assert.match(plain(m), /Not a formal appraisal, valuation or engineering survey\./)
  })
})

describe('CompareView', () => {
  const a = () => [chapter('water', 'High'), chapter('heat', 'Low')]
  const b = () => [chapter('water', 'Low')]
  const sites = () => ({ A: stateOf(a(), { report: reportOf(a(), { overall: 'High' }) }), B: stateOf(b(), { report: reportOf(b(), { overall: 'Low' }) }) })
  const view = (pick: string) => render(createElement(R.CompareView, { cmp: { pick, reason: 'Higher ground.', tradeoffs: ['Water: B is safer'] } as never, ...sites(), onPdf: noop, onLender: noop }))

  test('names the better site and gives the reason and trade-offs', () => {
    const m = view('B')
    assert.match(m, /<h2 class="disp big"><span class="sitetag B">B<\/span>Site B<\/h2>/)
    assert.match(plain(m), /Higher ground\./)
    assert.deepEqual(all(m, /<ul class="dash"><li>(.*?)<\/li>/g), ['Water: B is safer'])
    assert.match(view('A'), /<h2 class="disp big"><span class="sitetag A">A<\/span>Site A<\/h2>/)
  })

  test('says either site when there is no clear difference', () => {
    assert.deepEqual(all(view('Either'), /<h2 class="disp big">(.*?)<\/h2>/g), ['Either site'])
  })

  test('compares the five chapters and the overall rating as marks, a missing chapter as not enough evidence', () => {
    const m = view('A')
    const body = m.slice(m.indexOf('<tbody>'), m.indexOf('</tbody>'))
    const rowsOf = [...body.matchAll(/<tr><td>(.*?)<\/td><td>(.*?)<\/td><td>(.*?)<\/td><\/tr>/g)].map(r => [plain(r[1]), ...[r[2], r[3]].map(c => /class="mark (\w+)"/.exec(c)![1])])
    assert.deepEqual(rowsOf, [['Water', 'bad', 'ok'], ['Heat', 'ok', 'unk'], ['Smoke and fire', 'unk', 'unk'], ['Change around you', 'unk', 'unk'],
                              ['Daily needs nearby', 'unk', 'unk'], ['Overall', 'bad', 'ok']])
  })

  test('offers each site report but opens none until asked', () => {
    const m = view('A')
    assert.deepEqual(all(m, /<button type="button" aria-pressed="false">(.*?)<\/button>/g), ['Site A report', 'Site B report'])
    assert.doesNotMatch(m, /class="mod verdict"><div class="label" style/)
  })
})

describe('Chat', () => {
  const view = (over: Rec = {}) => render(createElement(R.Chat, { state: stateOf(FIVE(), over) }))

  test('offers the questions Gemini suggested', () => {
    assert.deepEqual(all(view(), /<div class="chips">(.*?)<\/div>/g), ['Is it only March?'])
  })

  test('falls back to the built-in questions when the report has none', () => {
    const m = view({ report: reportOf(FIVE(), { questions: [] }) })
    assert.deepEqual(all(m, /<button type="button">(.*?)<\/button>/g), ['What if we raise the ground floor by one metre?', 'Is the smoke only in March?', 'When did water last reach this area?'])
    I18N.setLang('th')
    assert.match(plain(view({ report: reportOf(FIVE(), { questions: [] }) })), /ควันมีแค่เดือนมีนาคมหรือเปล่า/)
  })

  test('has a question box limited to 1000 characters and no microphone where the browser has none', () => {
    const m = view()
    assert.match(m, /<input type="text" id="[^"]+" maxLength="1000"|<input type="text" id="[^"]+" maxlength="1000"/i)
    assert.doesNotMatch(m, /class="mic"/)
  })

  test('offers the microphone where the browser has one, labelled in the page language', () => {
    define('navigator', { language: 'en-US', mediaDevices: {} })
    try {
      assert.match(plain(view()), /Speak/)
      assert.match(view(), /<span class="hint" aria-live="polite"><\/span>/)
      I18N.setLang('th')
      assert.match(plain(view()), /พูด/)
    } finally {
      define('navigator', { language: 'en-US' })
    }
  })
})

test('mobile asks the browser whether the screen is under 761 px wide', () => {
  const asked: string[] = []
  define('matchMedia', (query: string) => { asked.push(query); return { matches: true } })
  assert.equal(R.mobile(), true)
  define('matchMedia', () => ({ matches: false }))
  assert.equal(R.mobile(), false)
  assert.deepEqual(asked, ['(max-width: 760px)'])
})

test('Icon draws an accessible-hidden svg for each name', () => {
  for (const n of ['check', 'back', 'printer', 'download', 'alert', 'stop', 'help', 'square', 'sun', 'moon', 'globe', 'mic'] as const) {
    assert.match(render(createElement(R.Icon, { n })), /^<svg class="i" viewBox="0 0 24 24" aria-hidden="true" focusable="false">.+<\/svg>$/, n)
  }
})
