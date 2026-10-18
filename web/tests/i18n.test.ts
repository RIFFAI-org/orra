// The UI dictionaries: English and Thai stay in step, every sentence carries what it is given, the language is chosen
// as intended, and the labels cover every name the Python side can send.
// Local dev tests: from web/, run `node --test "tests/*.test.ts"`.
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { test } from 'node:test'

type I18n = typeof import('../src/i18n.ts')

const store = new Map<string, string>()
let fresh = 0

// i18n.ts reads the browser when it is imported, so each load gets its own stubs and its own module instance.
async function load(opts: { saved?: string, browser?: string, blocked?: boolean } = {}) {
  store.clear()
  if (opts.saved) store.set('lang', opts.saved)
  const doc = { documentElement: { lang: '' } }
  const define = (name: string, value: unknown) => Object.defineProperty(globalThis, name, { value, configurable: true, writable: true })
  define('document', doc)
  define('navigator', { language: opts.browser ?? 'en-US' })
  define('localStorage', opts.blocked
    ? { getItem() { throw new Error('blocked') }, setItem() { throw new Error('blocked') } }
    : { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v) } })
  const m = await import(`../src/i18n.ts?fresh=${++fresh}`) as I18n
  return { m, html: doc.documentElement }
}

async function both() {
  const { m } = await load()
  m.setLang('en')
  const en = m.T
  m.setLang('th')
  return { en, th: m.T }
}

const shape = (v: unknown): unknown =>
  typeof v === 'function' ? `function/${v.length}` : Array.isArray(v) ? v.map(shape)
  : v && typeof v === 'object' ? Object.fromEntries(Object.entries(v).map(([k, x]) => [k, shape(x)])) : typeof v
const leaves = (v: unknown, path = ''): [string, string][] =>
  typeof v === 'string' ? [[path, v]] : Array.isArray(v) ? v.flatMap((x, i) => leaves(x, `${path}[${i}]`))
  : v && typeof v === 'object' ? Object.entries(v).flatMap(([k, x]) => leaves(x, path ? `${path}.${k}` : k)) : []
const at = (v: unknown, path: string) => path.split('.').reduce((o, k) => (o as Record<string, unknown>)[k], v) as (...a: unknown[]) => string

// ---------- which language the page starts in, and switching ----------
test('the starting language is the saved choice, else the browser language, else English', async () => {
  for (const [opts, expected] of [
    [{}, 'en'], [{ browser: 'th-TH' }, 'th'], [{ browser: 'th' }, 'th'], [{ browser: 'fr-FR' }, 'en'],
    [{ saved: 'th', browser: 'en-US' }, 'th'], [{ saved: 'en', browser: 'th-TH' }, 'en'],
    [{ saved: 'fr', browser: 'th-TH' }, 'th'],  // not one of ours: ignored
    [{ blocked: true, browser: 'th-TH' }, 'th'], [{ blocked: true }, 'en'],
  ] as const) {
    const { m, html } = await load(opts)
    assert.equal(m.lang, expected, JSON.stringify(opts))
    assert.equal(m.T.locale, expected === 'th' ? 'th-TH-u-ca-gregory' : 'en', JSON.stringify(opts))
    assert.equal(html.lang, expected, 'the page language follows')
  }
})

test('switching language updates the live values, the page language and the saved choice', async () => {
  const { m, html } = await load()
  m.setLang('th')
  assert.deepEqual([m.lang, m.T.signOut, html.lang, store.get('lang')], ['th', 'ออกจากระบบ', 'th', 'th'])
  m.setLang('en')
  assert.deepEqual([m.lang, m.T.signOut, html.lang, store.get('lang')], ['en', 'Sign out', 'en', 'en'])
})

test('blocked storage does not stop a switch', async () => {
  const { m, html } = await load({ blocked: true })
  m.setLang('th')
  assert.deepEqual([m.lang, html.lang], ['th', 'th'])
})

test('Thai dates use Gregorian years, to match the dates the evidence cites', async () => {
  const { th } = await both()
  const shown = new Date('2024-09-15T12:00:00Z').toLocaleDateString(th.locale, { day: 'numeric', month: 'short', year: 'numeric' })
  assert.match(shown, /2024/)
  assert.doesNotMatch(shown, /2567/)
})

// ---------- the two dictionaries match ----------
test('English and Thai have the same keys, nesting, list lengths and function arguments', async () => {
  const { en, th } = await both()
  assert.deepEqual(shape(th), shape(en))
})

test('no Thai string is left in English, and none is empty', async () => {
  const { en, th } = await both()
  const thai = new Map(leaves(th))
  const empty = [...leaves(en), ...thai].filter(([, s]) => !s.trim()).map(([p]) => p)
  assert.deepEqual(empty, [])
  const untranslated = leaves(en).filter(([p, s]) => thai.get(p) === s).map(([p]) => p)
  assert.deepEqual(untranslated, ['ch.water.n', 'ch.heat.n', 'ch.smoke.n', 'ch.change.n', 'ch.nearby.n'], 'only the chapter numbers are the same')
})

// ---------- every sentence carries what it is given ----------
const CASES: Record<string, { args: unknown[], has: string[] }> = {
  step: { args: [2], has: ['2'] },
  site: { args: ['B'], has: ['B'] },
  analysingSite: { args: ['B'], has: ['B'] },
  complete: { args: ['1:05'], has: ['1:05'] },
  checked: { args: ['Site A', 3, 5, true], has: ['Site A', '3', '5'] },
  logEnd: { args: [4, 2, 1], has: ['4', '2', '1'] },
  removed: { args: [3], has: ['3'] },
  server: { args: [502], has: ['502'] },
  failed: { args: ['boom'], has: ['boom'] },
  'q.flood': { args: ['15 Sep 2024'], has: ['15 Sep 2024'] },
  'q.fire': { args: [50], has: ['50'] },
  'q.date': { args: ['12 Sep 2024'], has: ['12 Sep 2024'] },
  'q.find': { args: ['Big C'], has: ['Big C'] },
  airDays: { args: [41.2, 5, 30], has: ['41.2', '5', '30'] },
  airOver: { args: [37.5], has: ['37.5'] },
  airOf: { args: [5, 30], has: ['5', '30'] },
  floods: { args: [3, 120, '15 Sep 2024', '31%', '4.2'], has: ['3', '120', '15 Sep 2024', '31%', '4.2'] },
  noFloods: { args: [120, '4.2'], has: ['120', '4.2'] },
  floodDay: { args: [true, '12%'], has: ['12%'] },
  heatDelta: { args: ['+2.1', 14], has: ['+2.1', '14'] },
  fireDays: { args: [180, 20, 40], has: ['180', '20', '40'] },
  pts: { args: ['built', '+4'], has: ['built', '+4'] },
  fireAria: { args: [20, 'March', 61], has: ['20', 'March', '61'] },
  fireTitle: { args: [12], has: ['12'] },
  fireCap: { args: [20], has: ['20'] },
  watch: { args: [2], has: ['2'] },
  siteReportOf: { args: ['A'], has: ['A'] },
  lWater: { args: [3, '; worst X', '4.2'], has: ['3', '; worst X', '4.2'] },
  lWorst: { args: ['15 Sep 2024', '31%'], has: ['15 Sep 2024', '31%'] },
  lHeat: { args: ['+2.1'], has: ['+2.1'] },
  lSmoke: { args: [40, 180, 20], has: ['40', '180', '20'] },
  verify: { args: ['https://orra.test/r/1'], has: ['https://orra.test/r/1'] },
  claimWet: { args: ['12 Sep 2024', 2], has: ['12 Sep 2024', '2'] },
  claimNear: { args: ['12%', '12 Sep 2024', -1], has: ['12%', '12 Sep 2024', '1'] },
  claimDry: { args: ['12 Sep 2024', 1], has: ['12 Sep 2024', '1'] },
  claimMasked: { args: ['12 Sep 2024'], has: ['12 Sep 2024'] },
  claimBuilt: { args: ['12 Sep 2024', 2], has: ['12 Sep 2024', '2'] },
  claimLate: { args: ['12 Sep 2024', -3], has: ['12 Sep 2024', '3'] },
}

test('every sentence that takes values shows all of them, in both languages', async () => {
  const { en, th } = await both()
  for (const [name, { args, has }] of Object.entries(CASES)) {
    for (const [lang, dict] of [['en', en], ['th', th]] as const) {
      const out = at(dict, name)(...args)
      assert.equal(typeof out, 'string', `${lang} ${name}`)
      for (const piece of has) assert.ok(out.includes(piece), `${lang} ${name}(${args.join(', ')}) leaves out "${piece}": ${out}`)
    }
  }
})

test('counts say 1 day, 2 days and the claimed day itself', async () => {
  const { en, th } = await both()
  const claim = (k: number) => en.claimWet('X', k)
  assert.match(claim(0), /on X \(the claimed day\)\.$/)
  assert.match(claim(0.4), /\(the claimed day\)/)  // under half a day is the same day
  assert.match(claim(1), /\(1 day after the claimed date\)/)
  assert.match(claim(2), /\(2 days after the claimed date\)/)
  assert.match(claim(-1), /\(1 day before the claimed date\)/)
  assert.match(claim(-2.4), /\(2 days before the claimed date\)/)
  assert.match(th.claimWet('X', 0), /\(วันเดียวกับที่แจ้ง\)/)
  assert.match(th.claimWet('X', 2), /\(2 วันหลังวันที่แจ้ง\)/)
  assert.match(th.claimWet('X', -1), /\(1 วันก่อนวันที่แจ้ง\)/)
})

test('the agent log counts agree in number: 1 query, 2 queries', async () => {
  const { en } = await both()
  assert.match(en.logEnd(1, 1, 1), /1 satellite query · 1 image reading · 1 unsupported claim removed$/)
  assert.match(en.logEnd(0, 2, 3), /0 satellite queries · 2 image readings · 3 unsupported claims removed$/)
})

test('a flood check says yes, no or unknown for water at the site', async () => {
  const { en, th } = await both()
  assert.match(en.floodDay(true, '5%'), /water at the site: yes;/)
  assert.match(en.floodDay(false, '5%'), /water at the site: no;/)
  assert.match(en.floodDay(null, '–'), /water at the site: unknown;/)
  assert.match(th.floodDay(true, '5%'), /น้ำถึงจุดที่ตั้ง: ถึง;/)
  assert.match(th.floodDay(false, '5%'), /น้ำถึงจุดที่ตั้ง: ไม่ถึง;/)
  assert.match(th.floodDay(null, '–'), /น้ำถึงจุดที่ตั้ง: ไม่ทราบ;/)
})

test('steps are numbered 01 to 03 and the chapter list is the five chapters in order', async () => {
  const { en, th } = await both()
  assert.equal(en.step(1), 'Step 01 / 03')
  assert.equal(th.step(3), 'ขั้นที่ 03 / 03')
  for (const dict of [en, th]) {
    assert.deepEqual(Object.entries(dict.ch).map(([k, c]) => [k, c.n]), [['water', '01'], ['heat', '02'], ['smoke', '03'], ['change', '04'], ['nearby', '05']])
  }
})

// ---------- the labels cover every name the Python side can send ----------
const py = (file: string) => { const u = new URL(`../../${file}`, import.meta.url); return existsSync(u) ? readFileSync(u, 'utf8') : '' }
const quoted = (s = '') => [...s.matchAll(/"([^"]+)"/g)].map(m => m[1])
const SKIP = existsSync(new URL('../../agent.py', import.meta.url)) ? false : 'agent.py is not next to web/'
const sorted = (xs: string[]) => [...xs].sort()

test('every tool the agent can call has a progress label', { skip: SKIP }, async () => {
  const { en, th } = await both()
  const tools = quoted(/ToolName = Literal\[(.*?)\]/.exec(py('agent.py'))?.[1])
  assert.equal(tools.length, 9)
  for (const dict of [en, th]) assert.deepEqual(sorted(Object.keys(dict.short)), sorted(tools))
})

test('every chapter, level and weight the report can contain has a label', { skip: SKIP }, async () => {
  const { en, th } = await both()
  const agent = py('agent.py')
  const chapters = quoted(/class Chapter[\s\S]*?key: Literal\[(.*?)\]/.exec(agent)?.[1])
  const levels = quoted(/^Level = Literal\[(.*?)\]/m.exec(agent)?.[1])
  const weights = quoted(/^Weight = Literal\[(.*?)\]/m.exec(agent)?.[1])
  const hazards = [...(/class Profile[\s\S]*?who: str\n([\s\S]*?)why: str/.exec(agent)?.[1] ?? '').matchAll(/^\s+(\w+): Weight/gm)].map(m => m[1])
  assert.deepEqual([chapters.length, levels.length, weights.length, hazards.length], [5, 4, 3, 5])
  for (const dict of [en, th]) {
    assert.deepEqual(Object.keys(dict.ch), chapters)  // in the order the report lists them
    assert.deepEqual(sorted(Object.keys(dict.level)), sorted(levels))
    assert.deepEqual(sorted(Object.keys(dict.weight)), sorted(weights))
    assert.deepEqual(sorted(Object.keys(dict.hazard)), sorted(hazards))
  }
})

test('every daily need and land-cover class has a label', { skip: SKIP || !py('places.py') || !py('eo.py') }, async () => {
  const { en, th } = await both()
  const needs = [...(/KINDS = \{[\s\S]*?\n\}/.exec(py('places.py'))?.[0] ?? '').matchAll(/^\s+"(\w+)": \[/gm)].map(m => m[1])
  const classes = quoted(/CLASSES = \[([\s\S]*?)\]/.exec(py('eo.py'))?.[1])
  assert.deepEqual([needs.length, classes.length], [4, 9])
  for (const dict of [en, th]) {
    assert.deepEqual(sorted(Object.keys(dict.need)), sorted(needs))
    assert.deepEqual(sorted(Object.keys(dict.cover)), sorted(classes))
  }
})
