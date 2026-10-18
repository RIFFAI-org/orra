import { type SubmitEvent, type ReactNode, useEffect, useId, useRef, useState } from 'react'
import { T, lang } from './i18n.ts'

export type Site = 'A' | 'B'
export type Ev = { type: string, [k: string]: any }
type R = Record<string, any>  // a tool result; its shape depends on the tool
type ChKey = keyof typeof T.ch
type Chapter = { key: ChKey, level: string, headline: string, detail: string,
                 evidence: { claim: string, value: string, source: string, date: string }[] }
export type State = { id?: string, lat: number, lon: number, text: string, lang?: string, profile: R, evidence: { tool: string, result: R }[],
                      report: { verdict: string[], chapters: Chapter[], overall: string, checks: string[], questions: string[],
                                lender_note: string } }
export type Compare = { pick: Site | 'Either', reason: string, tradeoffs: string[], lang?: string }
type Show = (state: State, label: string) => void

const ICONS = {  // Lucide (ISC)
  check: <path d="M20 6 9 17l-5-5" />,
  back: <><path d="m12 19-7-7 7-7" /><path d="M19 12H5" /></>,
  printer: <><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2" /><path d="M6 9V3a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v6" /><rect x="6" y="14" width="12" height="8" rx="1" /></>,
  download: <><path d="M12 15V3" /><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="m7 10 5 5 5-5" /></>,
  alert: <><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3" /><path d="M12 9v4" /><path d="M12 17h.01" /></>,
  stop: <><path d="M12 16h.01" /><path d="M12 8v4" /><path d="M15.312 2a2 2 0 0 1 1.414.586l4.688 4.688A2 2 0 0 1 22 8.688v6.624a2 2 0 0 1-.586 1.414l-4.688 4.688a2 2 0 0 1-1.414.586H8.688a2 2 0 0 1-1.414-.586l-4.688-4.688A2 2 0 0 1 2 15.312V8.688a2 2 0 0 1 .586-1.414l4.688-4.688A2 2 0 0 1 8.688 2z" /></>,
  help: <><circle cx="12" cy="12" r="10" /><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" /><path d="M12 17h.01" /></>,
  square: <rect width="18" height="18" x="3" y="3" />,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2" /><path d="M12 20v2" /><path d="m4.93 4.93 1.41 1.41" /><path d="m17.66 17.66 1.41 1.41" /><path d="M2 12h2" /><path d="M20 12h2" /><path d="m6.34 17.66-1.41 1.41" /><path d="m19.07 4.93-1.41 1.41" /></>,
  moon: <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" />,
  globe: <><circle cx="12" cy="12" r="10" /><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20" /><path d="M2 12h20" /></>,
  mic: <><path d="M12 19v3" /><path d="M19 10v2a7 7 0 0 1-14 0v-2" /><rect x="9" y="2" width="6" height="13" rx="3" /></>,
}
export const Icon = ({ n }: { n: keyof typeof ICONS }) =>
  <svg className="i" viewBox="0 0 24 24" aria-hidden="true" focusable="false">{ICONS[n]}</svg>

export const day = (iso: string | Date) => new Date(iso).toLocaleDateString(T.locale, { day: 'numeric', month: 'short', year: 'numeric' })
const pct = (x?: number | null) => x == null ? '–' : Math.round(x * 100) + '%'
const signed = (x: number) => (x > 0 ? '+' : '') + x
const cls = (l: string) => l.split(' ')[0]
export const clock = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
export const mobile = () => matchMedia('(max-width: 760px)').matches
const month = (i: number, month: 'narrow' | 'long') => new Date(2000, i, 15).toLocaleString(T.locale, { month })
export const TOOL_CH: Record<string, ChKey> = { water_history: 'water', flood_day_images: 'water', water_on_date: 'water', heat: 'heat',
                                                fire: 'smoke', air: 'smoke', change: 'change', nearby: 'nearby', find_places: 'nearby' }
export const NAMES: Record<string, (a: R) => string> = {
  water_history: () => T.q.water,
  flood_day_images: a => T.q.flood(a.event_time ? day(a.event_time) : '?'),
  heat: () => T.q.heat,
  fire: a => T.q.fire(a.radius_km > 20 ? 50 : 20),
  change: () => T.q.change,
  water_on_date: a => T.q.date(a.date ? day(a.date) : '?'),
  nearby: () => T.q.nearby,
  air: () => T.q.air,
  find_places: a => T.q.find(a.query ?? ''),
}
const LV: Record<string, number> = { High: 3, Medium: 2, Low: 1 }
export const footer = () => `${T.disclaimer}
  Contains modified Copernicus Sentinel data. Landsat courtesy of USGS. NASA FIRMS. Dynamic World (Google, WRI).
  JRC Global Surface Water. MERIT Hydro. ERA5-Land (Copernicus Climate Change Service). Places: Google Maps. Includes air quality data from Google. Icons: Lucide.`

// the lender brief keeps its rating words; the homebuyer's screens show a Mark instead
const Level = ({ l, big = '' }: { l: string, big?: string }) =>
  <span className={`lvl ${cls(l)} ${big}`}><span className="meter" aria-hidden="true">
    {[1, 2, 3].map(i => <i key={i} className={i <= (LV[l] ?? 0) ? 'on' : ''} />)}</span>{T.level[l] ?? l}</span>

// severity as traffic-light colour plus icon shape, so it never rests on colour alone
const SIGN = { bad: 'stop', warn: 'alert', ok: 'check', unk: 'help' } as const
type Sev = keyof typeof SIGN
const SEV: Record<string, Sev> = { High: 'bad', Medium: 'warn', Low: 'ok' }
const sev = (l: string): Sev => SEV[l] ?? 'unk'
const Sign = ({ c, label }: { c: Sev, label: string }) => <span className={`mark ${c}`}><Icon n={SIGN[c]} /><span className="sr">{label}</span></span>
const Mark = ({ l }: { l: string }) => <Sign c={sev(l)} label={T.sev[sev(l)]} />

export async function stream(url: string, body: object, onEvent: (ev: Ev) => void) {
  const res = await fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ ...body, lang }) })
  if (res.status === 401) throw new Error(T.signedOut)
  if (res.status === 429) throw new Error(T.limit)
  if (!res.ok) throw new Error(T.server(res.status))
  const reader = res.body!.pipeThrough(new TextDecoderStream()).getReader()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += value
    let i
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i)
      buf = buf.slice(i + 1)
      if (line) onEvent(JSON.parse(line))
    }
  }
}

const changes = (c?: Record<string, number>) => Object.entries(c || {}).map(([k, v]) => T.pts(T.cover[k] ?? k, signed(v))).join(', ')
type Place = { name: string, km: number }
const kinds = (r: R) => Object.entries((r.nearest_km ?? {}) as Record<string, Place[]>)
const nearest = (r: R) => kinds(r).map(([k, ps]) => `${T.need[k] ?? k} ${ps[0] ? ps[0].km + ' km' : '–'}`).join(', ')

export function summary(name: string, r: R) {
  if (r.error) return T.failed(r.error)
  switch (name) {
    case 'water_history': return r.flood_events
      ? T.floods(r.flood_events, r.passes, day(r.worst.time), pct(r.worst.share_flooded_1km), r.hand_m ?? '–')
      : T.noFloods(r.passes, r.hand_m ?? '–')
    case 'flood_day_images': case 'water_on_date': return r.note || T.floodDay(r.water_at_pin, pct(r.share_flooded_300m))
    case 'heat': return r.note || T.heatDelta(signed(r.delta_c), r.scenes)
    case 'fire': return T.fireDays(r.dry_season_fire_days_radius, r.radius_km, r.dry_season_fire_days_5km)
    case 'change': return changes(r.change_points_1km) || T.littleChange
    case 'nearby': return nearest(r)
    case 'find_places': return r.nearest?.length ? `${r.nearest[0].name} · ${r.nearest[0].km} km` : T.noMatch
    case 'air': return r.days ? T.airDays(r.pm25_mean_ug_m3, r.days_over_thai_standard, r.days) : r.note
  }
  return ''
}

const noImages = (r: R) => { const { images, ...rest } = r; return rest }
export const slim = (s: State) => ({ lat: s.lat, lon: s.lon, profile: s.profile, report: s.report,
                                     evidence: s.evidence.map(e => ({ ...e, result: noImages(e.result) })) })

type Ref = [any, string | number]  // an object and the key of one string Gemini wrote into it
const each = (xs: string[]) => xs.map((_, i): Ref => [xs, i])
const refs = (x: State | Compare): Ref[] => 'report' in x ? [
  [x.profile, 'who'], [x.profile, 'why'], [x.report, 'lender_note'],
  ...each(x.report.verdict), ...each(x.report.checks), ...each(x.report.questions),
  ...x.report.chapters.flatMap((c): Ref[] => [[c, 'headline'], [c, 'detail'], ...c.evidence.flatMap((e): Ref[] => [[e, 'claim'], [e, 'value']])]),
  ...x.evidence.filter(e => e.result.visual_description).map((e): Ref => [e.result, 'visual_description']),
] : [[x, 'reason'], ...each(x.tradeoffs)]

const versions = new WeakMap<object, Record<string, string[]>>()  // every language a report was shown in: switching back is exact and free

// a language switch translates what Gemini already wrote, in place: levels, numbers and sources stay as they were
export async function translate(x: State | Compare) {
  const to = lang, rs = refs(x), seen = versions.get(x) ?? {}
  versions.set(x, { ...seen, [x.lang ?? 'en']: rs.map(([o, k]) => o[k]) })
  let texts: string[] | null = seen[to]?.length === rs.length ? seen[to] : null  // a chat image reading since adds a string: translate afresh
  if (!texts) await stream('/api/translate', { texts: rs.map(([o, k]) => o[k]) }, ev => {
    if (ev.type === 'error') throw new Error(ev.text)
    if (ev.type === 'translated') texts = ev.texts
  })
  if (!texts) throw new Error('No translation came back.')
  if (to !== lang) return  // switched again meanwhile: that switch starts from the untouched text
  rs.forEach(([o, k], i) => { o[k] = texts![i] })
  x.lang = to
}

// ---------- report ----------
const results = (state: State, tool: string) => state.evidence.filter(e => e.tool === tool && !e.result.error).map(e => e.result)
const Fig = ({ b64, tag, cap }: { b64: string, tag: string, cap: string }) =>
  <figure><div className="frame"><img alt={cap} src={`data:image/png;base64,${b64}`} /><span className="t tech">{tag}</span></div><figcaption>{cap}</figcaption></figure>
const floodShot = (state: State) => results(state, 'flood_day_images').filter(r => r.images)
  .sort((a, b) => (b.share_flooded_1km ?? 0) - (a.share_flooded_1km ?? 0))[0]
const fireNear = (state: State) => results(state, 'fire').sort((a, b) => a.radius_km - b.radius_km)[0]
const Interp = ({ text }: { text?: string }) =>
  text ? <div className="interp"><div className="tech">{T.interp}</div><p>{text}</p></div> : null

function Extras({ k, state }: { k: ChKey, state: State }) {
  if (k === 'water') {
    const f = floodShot(state)
    return f ? <><div className="imgs"><Fig b64={f.images.before} tag={T.usualTag} cap={T.usualCap} />
      <Fig b64={f.images.after} tag={`T1 · ${day(f.pass_time)}`} cap={T.passCap} /></div><Interp text={f.visual_description} /></> : null
  }
  if (k === 'heat') {
    const h = results(state, 'heat')[0]
    return h?.images ? <div className="imgs one"><Fig b64={h.images.heat} tag={T.lstTag} cap={T.lstCap} /></div> : null
  }
  if (k === 'smoke') {
    const f = fireNear(state), a = results(state, 'air')[0], air = a?.days ? <Air a={a} /> : null
    if (!f) return air
    const m: Record<string, number> = f.by_month_last_5y_radius, keys = Object.keys(m), vals = Object.values(m)
    const max = Math.max(1, ...vals), peak = vals.indexOf(Math.max(...vals))
    return <>
      <div className="bars" role="img" aria-label={T.fireAria(f.radius_km, month(peak, 'long'), vals[peak])}>
        {vals.map((v, i) => <div key={i} className={i === peak && v ? 'peak' : ''} style={{ height: `${100 * v / max}%` }} title={T.fireTitle(v)}>{v ? <span>{v}</span> : null}</div>)}
      </div>
      <div className="bars-l" aria-hidden="true">{keys.map((k, i) => <span key={k}>{month(i, 'narrow')}</span>)}</div>
      <p className="hint">{T.fireCap(f.radius_km)}</p>
      {air}
    </>
  }
  if (k === 'change') {
    const c = results(state, 'change')[0]
    return c?.images ? <><div className="imgs"><Fig b64={c.images.before} tag={`T0 · ${c.before_period}`} cap={T.before} />
      <Fig b64={c.images.after} tag={`T1 · ${c.after_period}`} cap={T.after} /></div><Interp text={c.visual_description} /></> : null
  }
  if (k === 'nearby') {
    const n = results(state, 'nearby')[0]
    return n ? <><table className="near"><tbody>{kinds(n).map(([k, ps]) => <tr key={k}><th scope="row">{T.need[k] ?? k}</th>
      <td>{ps[0] ? <>{ps[0].name} · <b>{ps[0].km} km</b></> : T.noneNear}</td></tr>)}</tbody></table>
      <p className="src">Google Maps</p></> : null  // Places terms: attribute when shown without a Google map
  }
  return null
}

const Air = ({ a }: { a: R }) => <><table className="near"><tbody>
  <tr><th scope="row">{T.airMean}</th><td><b>{a.pm25_mean_ug_m3} {T.ug}</b></td></tr>
  <tr><th scope="row">{T.airWorst}</th><td>{day(a.pm25_worst_day.date)} · <b>{a.pm25_worst_day.ug_m3} {T.ug}</b></td></tr>
  <tr><th scope="row">{T.airOver(a.thai_standard_ug_m3)}</th><td>{T.airOf(a.days_over_thai_standard, a.days)}</td></tr>
</tbody></table><p className="src">{T.airCap} Source: Includes air quality data from Google</p></>  // Air Quality terms: this text, next to the data

// record, then Gemini transcribes on the server: works in every browser, unlike the browser's own speech recognition
const b64 = (b: Blob) => new Promise<string>(ok => { const f = new FileReader(); f.onload = () => ok(String(f.result).split(',')[1]); f.readAsDataURL(b) })

export function Mic({ onText }: { onText: (t: string) => void }) {
  const [on, setOn] = useState<'' | 'rec' | 'busy'>('')
  const [err, setErr] = useState('')
  const rec = useRef<MediaRecorder>(null)
  useEffect(() => () => { const r = rec.current; if (r) { r.onstop = null; r.stream.getTracks().forEach(t => t.stop()) } }, [])  // leaving the step drops the recording
  async function start() {
    setErr('')
    try {
      const mic = await navigator.mediaDevices.getUserMedia({ audio: true })
      const r = rec.current = new MediaRecorder(mic, { audioBitsPerSecond: 32000 }), parts: Blob[] = []
      r.ondataavailable = e => parts.push(e.data)
      r.onstop = () => { mic.getTracks().forEach(t => t.stop()); send(new Blob(parts, { type: r.mimeType })) }
      r.start()
      setOn('rec')
      setTimeout(() => r.state === 'recording' && r.stop(), 60_000)  // a minute at 32 kbps stays far under the 1 MB body cap
    } catch (x) { setErr((x as Error).name === 'NotAllowedError' ? T.micDenied : T.micFailed) }
  }
  async function send(blob: Blob) {
    setOn('busy')
    try {
      await stream('/api/transcribe', { audio: await b64(blob), mime: blob.type.split(';')[0] }, ev => {
        if (ev.type === 'transcript') ev.text ? onText(ev.text) : setErr(T.micFailed)
        if (ev.type === 'error') setErr(T.micFailed)
      })
    } catch (x) { setErr((x as Error).message) }
    setOn('')
  }
  if (!navigator.mediaDevices) return null  // only on https or localhost
  return <>
    <div className="mic">
      <button type="button" className={on === 'rec' ? 'on' : ''} disabled={on === 'busy'} onClick={() => on === 'rec' ? rec.current!.stop() : start()}>
        <Icon n={on === 'rec' ? 'square' : 'mic'} />{on === 'rec' ? T.micStop : on === 'busy' ? T.hearing : T.mic}</button>
      <span className="hint" aria-live="polite">{on === 'rec' ? T.listening : ''}</span>
    </div>
    <p className="err" role="alert">{err}</p>
  </>
}

const says = (c: Chapter) => c.level === 'Not enough evidence' ? T.noEvidence : c.headline  // the guardrail can empty a chapter after its headline was written
const Sources = ({ c }: { c: Chapter }) => <ul className="evidence">{c.evidence.map((e, i) =>
  <li key={i}>{e.claim.replace(/\.$/, '')}: <b>{e.value}</b><span className="src">{e.source} · {e.date}</span></li>)}</ul>
const Checks = ({ xs }: { xs: string[] }) => <ul className="checks">{xs.map((x, i) => <li key={i}><Icon n="square" />{x}</li>)}</ul>

// one chapter: what it means for this person up front, the proof one click away
function Finding({ c, state, big = false }: { c: Chapter, state: State, big?: boolean }) {
  const e = c.evidence[0]
  return <details className={`finding ${sev(c.level)}${big ? ' big' : ''}`}>
    <summary><Mark l={c.level} /><span>
      <b className="fname">{T.ch[c.key].name}</b>{big ? ' ' : ' · '}<span className="fhead">{says(c)}</span>
      {big && e && <span className="fact"><b>{e.value}</b> · {e.claim.replace(/\.$/, '')}</span>}
      {big && <span className="more tech">{T.evidence}</span>}
    </span></summary>
    <div className="fmore"><p>{c.detail}</p><Extras k={c.key} state={state} /><Sources c={c} /></div>
  </details>
}

const W: Record<string, number> = { high: 3, medium: 2, low: 1 }
const weight = (s: State, c: Chapter) => W[s.profile[c.key === 'water' ? 'flood' : c.key]] ?? 0  // the profile calls water "flood"

// on screen: the call, then only what needs attention; no rating words, the full detail is in the PDF
export function Report({ state, label = T.site('A'), onPdf, onLender }: { state: State, label?: string, onPdf: Show, onLender: Show }) {
  const r = state.report
  const ranked = [...r.chapters].sort((a, b) => (LV[b.level] ?? 0) - (LV[a.level] ?? 0) || weight(state, b) - weight(state, a))
  const watch = ranked.filter(c => (LV[c.level] ?? 0) > 1), fine = ranked.filter(c => LV[c.level] === 1), unknown = ranked.filter(c => !LV[c.level])
  return (
    <article className="mod verdict">
      <div className="label" style={{ margin: 0 }}><span className="num">{label}</span><span className="disp">{T.yourAnswer}</span></div>
      <p className="who">{T.for} {state.profile.who}</p>
      <h2 className="lead">{r.verdict[0] ?? ''}</h2>
      {watch.length > 0 && <section className="group"><h3 className="tech">{T.watch(watch.length)}</h3>
        {watch.map(c => <Finding key={c.key} c={c} state={state} big />)}</section>}
      {!!r.checks?.length && <section className="group"><h3 className="tech">{T.beforeSign}</h3><Checks xs={r.checks} /></section>}
      {fine.length > 0 && <section className="group"><h3 className="tech">{T.fine}</h3>
        {fine.map(c => <Finding key={c.key} c={c} state={state} />)}</section>}
      {unknown.length > 0 && <section className="group"><h3 className="tech">{T.unknown}</h3>
        {unknown.map(c => <Finding key={c.key} c={c} state={state} />)}</section>}
      <div className="row" style={{ marginTop: 18 }}>
        <button type="button" className="primary" onClick={() => onPdf(state, label)}><Icon n="download" /> {T.fullPdf}</button>
        <button type="button" data-lender onClick={() => onLender(state, label)}>{T.lenderView}</button>
      </div>
    </article>
  )
}

// a saved report can be checked against our copy, so an edited printout shows up
const Where = ({ state }: { state: State }) => <p className="tech">{T.location} {state.lat}, {state.lon}
  {state.id && <><br />{T.verify(`${location.origin}/r/${state.id}`)}</>}</p>

export function Pdf({ state, label }: { state: State, label: string }) {
  const r = state.report
  return (
    <div className="paper">
      <div className="tag-l tech"><img src="/logo.svg" alt="Orra" /><span>{T.siteReport} · {new Date().toLocaleString(T.locale)}</span></div>
      <h1 className="disp">{label} · {T.fullReport}</h1>
      <Where state={state} />
      <p><b>{T.for}</b> {state.profile.who}<br /><b>{T.youWrote}</b> “{state.text}”</p>
      <h2>{r.verdict[0] ?? ''}</h2>
      {r.verdict[1] && <p>{r.verdict[1]}</p>}
      {!!r.checks?.length && <><h3>{T.beforeSign}</h3><Checks xs={r.checks} /></>}
      {r.chapters.map(c => <section className="chapter" key={c.key}>
        <div className="chhead"><span className="num">{T.ch[c.key].n}</span><h3 className="disp">{T.ch[c.key].name}</h3><Mark l={c.level} /></div>
        <p><b>{says(c)}</b></p><p>{c.detail}</p><Extras k={c.key} state={state} /><Sources c={c} />
      </section>)}
      <h3>{T.forBanks}</h3><p>{r.lender_note}</p>
      <p className="fine">{footer()}</p>
    </div>
  )
}

export function Chat({ state }: { state: State }) {
  const id = useId()
  const [lines, setLines] = useState<{ q: string, a: ReactNode }[]>([])
  const [busy, setBusy] = useState(false)
  const [q, setQ] = useState('')
  const history = useRef<{ role: 'user' | 'model', text: string }[]>([])

  async function ask(question: string) {
    question = question.trim()
    if (!question || busy) return
    setBusy(true)
    setQ('')
    const i = lines.length
    const answer = (a: ReactNode) => setLines(l => l.map((x, j) => j === i ? { ...x, a } : x))
    setLines(l => [...l, { q: question, a: <span className="dim">{T.analysing}</span> }])
    const steps: string[] = []
    try {
      await stream('/api/chat', { state: slim(state), messages: history.current, question }, ev => {
        if (ev.type === 'call') {
          steps.push(NAMES[ev.name]?.(ev.args) ?? ev.name)
          answer(<span className="dim">{T.querying}: {steps.join('; ')}…</span>)
        } else if (ev.type === 'answer') {
          answer(<>{ev.text}{steps.length > 0 && <><br /><span className="dim">{T.newEvidence}: {steps.join('; ')}</span></>}</>)
          history.current.push({ role: 'user', text: question }, { role: 'model', text: ev.text })
          state.evidence.push(...ev.evidence)
        } else if (ev.type === 'error') {
          answer(`${T.queryFailed}: ${ev.text}`)
        }
      })
    } catch (err) {
      answer(`${T.queryFailed}: ${(err as Error).message}`)
    }
    setBusy(false)
  }
  const submit = (e: SubmitEvent) => { e.preventDefault(); ask(q) }

  return (
    <div className="mod chat">
      <div className="label"><span className="num">—</span><label htmlFor={id}>{T.followUp}</label></div>
      <div className="chips">{(state.report.questions?.length ? state.report.questions : T.suggest).map(s =>
        <button type="button" key={s} onClick={() => ask(s)}>{s}</button>)}</div>
      <div className="msgs" aria-live="polite">{lines.map((l, i) => <div key={i}>
        <div className="msg user"><span className="tech">{T.qa[0]}</span><div className="body">{l.q}</div></div>
        <div className="msg"><span className="tech">{T.qa[1]}</span><div className="body">{l.a}</div></div>
      </div>)}</div>
      <form className="row" onSubmit={submit}>
        <input type="text" id={id} value={q} onChange={e => setQ(e.target.value)} maxLength={1000} placeholder={T.askPlaceholder} autoComplete="off" />
        <button className="primary" disabled={busy}>{T.ask}</button>
      </form>
      <Mic onText={t => setQ(x => `${x.trim()} ${t}`.trim().slice(0, 1000))} />
    </div>
  )
}

export function CompareView({ cmp, A, B, onPdf, onLender }: { cmp: Compare, A: State, B: State, onPdf: Show, onLender: Show }) {
  const [shown, setShown] = useState<Site | null>(null)
  const lv = (s: State, k: string) => s.report.chapters.find(x => x.key === k)?.level ?? 'Not enough evidence'
  const S = { A, B }
  return <>
    <article className="mod winner verdict">
      <div className="label"><span className="num">—</span><span className="disp">{T.recommendation}</span></div>
      <p className="who">{T.for} {A.profile.who}</p>
      <h2 className="disp big">{cmp.pick === 'Either' ? T.either : <><span className={`sitetag ${cmp.pick}`}>{cmp.pick}</span>{T.site(cmp.pick)}</>}</h2>
      <p>{cmp.reason}</p>
      <table>
        <thead><tr><th scope="col">{T.chapter}</th><th scope="col"><span className="sitetag">A</span></th><th scope="col"><span className="sitetag B">B</span></th></tr></thead>
        <tbody>
          {Object.entries(T.ch).map(([k, ch]) => <tr key={k}><td>{ch.name}</td><td><Mark l={lv(A, k)} /></td><td><Mark l={lv(B, k)} /></td></tr>)}
          <tr><td><b>{T.overall}</b></td><td><Mark l={A.report.overall} /></td><td><Mark l={B.report.overall} /></td></tr>
        </tbody>
      </table>
      <ul className="dash">{cmp.tradeoffs.map((t, i) => <li key={i}>{t}</li>)}</ul>
      <div className="row">{(['A', 'B'] as const).map(k =>
        <button type="button" key={k} aria-pressed={shown === k} onClick={() => setShown(k)}>{T.siteReportOf(k)}</button>)}</div>
    </article>
    {shown && <><Report state={S[shown]} label={T.site(shown)} onPdf={onPdf} onLender={onLender} /><Chat key={shown} state={S[shown]} /></>}
  </>
}

export function Lender({ state, onBack }: { state: State, onBack: () => void }) {
  const back = useRef<HTMLButtonElement>(null)
  useEffect(() => back.current?.focus(), [])
  const r = state.report, w = results(state, 'water_history')[0] || {}, h = results(state, 'heat')[0] || {}
  const f = fireNear(state) || {}, c = results(state, 'change')[0] || {}, fl = floodShot(state), nb = results(state, 'nearby')[0]
  const metric: Record<ChKey, string> = {
    water: w.passes != null ? T.lWater(w.flood_events, w.worst ? T.lWorst(day(w.worst.time), pct(w.worst.share_flooded_1km)) : '', w.hand_m ?? '–') : T.notAssessed,
    heat: h.delta_c != null ? T.lHeat(signed(h.delta_c)) : T.notAssessed,
    smoke: f.radius_km ? T.lSmoke(f.dry_season_fire_days_5km, f.dry_season_fire_days_radius, f.radius_km) : T.notAssessed,
    change: c.change_points_1km ? changes(c.change_points_1km) || T.lLittle : T.notAssessed,
    nearby: nb ? nearest(nb) : T.notAssessed,
  }
  return (
    <div className="paper">
      <div className="row between noprint">
        <button type="button" ref={back} onClick={onBack}><Icon n="back" /> {T.back}</button>
        <button type="button" className="primary" onClick={() => print()}><Icon n="printer" /> {T.print}</button>
      </div>
      <div className="tag-l tech"><img src="/logo.svg" alt="Orra" /><span>{T.brief} · {new Date().toLocaleString(T.locale)}</span></div>
      <h1 className="disp">{T.briefTitle}</h1>
      <Where state={state} />
      <div className="row" style={{ margin: '14px 0 6px' }}><Level l={r.overall} big="big" /></div>
      <p>{r.lender_note}</p>
      <h3>{T.hazards}</h3>
      <table>
        <thead><tr><th scope="col">{T.hazardCol}</th><th scope="col">{T.levelCol}</th><th scope="col">{T.keyEvidence}</th><th scope="col">{T.source}</th></tr></thead>
        <tbody>{r.chapters.map(ch => <tr key={ch.key}><td>{T.ch[ch.key].name}</td><td><Level l={ch.level} /></td>
          <td>{metric[ch.key]}</td><td>{ch.evidence[0]?.source ?? '–'}</td></tr>)}</tbody>
      </table>
      <div className="imgs">
        {fl && <Fig b64={fl.images.after} tag={`SAR · ${day(fl.pass_time)}`} cap={T.sarCap} />}
        {c.images && <Fig b64={c.images.after} tag={`${T.optical} · ${c.after_period}`} cap={T.today} />}
      </div>
      <Claim state={state} />
      <p className="fine">{T.lenderFine}</p>
    </div>
  )
}

// an insurer's claimed flood date against the nearest radar pass; the verdict is a fixed template over the
// measured facts, not model text, so the same facts always read the same
const claimSays = (r: R): [Sev, string] =>
  r.note ? ['unk', T.claimNoPass]
  : r.water_at_pin ? ['ok', T.claimWet(day(r.pass_time), r.days_from_request)]
  : (r.share_flooded_300m ?? 0) >= 0.05 ? ['warn', T.claimNear(pct(r.share_flooded_300m), day(r.pass_time), r.days_from_request)]
  : r.pin_built_up ? ['unk', T.claimBuilt(day(r.pass_time), r.days_from_request)]
  : Math.abs(r.days_from_request) > 1 ? ['unk', T.claimLate(day(r.pass_time), r.days_from_request)]  // water can come and go between passes
  : r.water_at_pin === false ? ['bad', T.claimDry(day(r.pass_time), r.days_from_request)]
  : ['unk', T.claimMasked(day(r.pass_time))]

function Claim({ state }: { state: State }) {
  const id = useId()
  const [res, setRes] = useState<R | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  async function check(e: SubmitEvent<HTMLFormElement>) {
    e.preventDefault()
    const date = String(new FormData(e.currentTarget).get('date'))
    setBusy(true); setErr(''); setRes(null)
    try {
      await stream('/api/claim', { lat: state.lat, lon: state.lon, date }, ev => {
        if (ev.type === 'claim') setRes(ev.result)
        if (ev.type === 'error') setErr(ev.text)
      })
    } catch (x) { setErr((x as Error).message) }
    setBusy(false)
  }
  const [c, says]: [Sev, string] = res ? claimSays(res) : ['unk', '']
  return <section className="claim">
    <h3>{T.claimTitle}</h3>
    <form className="row noprint" onSubmit={check}>
      <label htmlFor={id}>{T.claimDate}</label>
      <input type="date" id={id} name="date" required min="2018-01-12" max={new Date().toISOString().slice(0, 10)} />
      <button className="primary" disabled={busy}>{busy ? T.checking : T.check}</button>
    </form>
    <p className="err" role="alert">{err}</p>
    {res && <div aria-live="polite">
      <p className={`flag ${c}`}><Sign c={c} label={T.sev[c]} /><span>{says}</span></p>
      {res.images && <div className="imgs"><Fig b64={res.images.before} tag={T.usualTag} cap={T.usualCap} />
        <Fig b64={res.images.after} tag={`T1 · ${day(res.pass_time)}`} cap={T.passCap} /></div>}
      <Interp text={res.visual_description} />
      <p className="fine">{T.claimFine}</p>
    </div>}
  </section>
}
