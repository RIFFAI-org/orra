import L from 'leaflet'
import { type ReactNode, type SubmitEvent, useEffect, useEffectEvent, useReducer, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import Header from './Header.tsx'
import { authClient, signIn } from './auth-client.ts'
import { T, lang } from './i18n.ts'
import {
  Chat, type Compare, CompareView, type Ev, Icon, Lender, Mic, NAMES, Pdf, Report, type Site, type State,
  TOOL_CH, clock, footer, mobile, slim, stream, summary, translate,
} from './report.tsx'

type Stage = 'pick' | 'who' | 'run' | 'done' | 'pickB'
type Status = 'todo' | 'active' | 'done' | 'err'
type Line = { t: string, tag: string, body: ReactNode, kind: string }
type Prog = { title: string, t0: number, calls: number, seen: number, dropped: number, you: Status, report: Status,
              ch: Record<string, { open: number, ok: number, err: number }>, log: Line[], end?: string }
type Out = { kind: 'skel' } | { kind: 'error', msg: string } | { kind: 'report', state: State }
         | { kind: 'compare', cmp: Compare, A: State, B: State } | null
type Found = { name: string, address: string, lat: number, lon: number }

const CONTOUR = 'M4 30 32 4 60 30V61H4ZM11.3 32.6 32 13.4 52.7 32.6V55.5H11.3ZM18.6 35.2 32 22.7 45.4 35.2V50.1H18.6Z'  // brand mark 09
const elapsed = (p: Prog) => Math.round((Date.now() - p.t0) / 1000)
const SKEL = <div className="mod skel" aria-hidden="true"><div className="sk t" /><div className="sk" /><div className="sk s" /></div>
const SAVED = location.pathname.match(/^\/r\/([0-9a-f-]{36})$/)?.[1]  // a saved report's link

const Step = ({ state, name, sub = T.status[state] }: { state: Status, name: string, sub?: string }) =>
  <li className={state}><span className="box">{state === 'done' ? <Icon n="check" /> : state === 'err' ? '—' : null}</span>
    <span className="nm">{name}</span><small>{sub}</small></li>

function Picker({ inputId, onPick }: { inputId: string, onPick: (lat: number, lon: number) => void }) {
  const [err, setErr] = useState('')
  const [found, setFound] = useState<Found[] | null>(null)
  const [busy, setBusy] = useState(false)
  async function submit(e: SubmitEvent<HTMLFormElement>) {
    e.preventDefault()
    const q = String(new FormData(e.currentTarget).get('q')).trim()
    // coordinates need decimals, so "Soi 23, 101" is searched, while "20.43, 99.88" or a Maps link's "@13.75,100.50" is a pin
    const m = q.match(/(-?\d+\.\d+)\s*[,\s]\s*(-?\d+\.\d+)/)
    setErr(''); setFound(null)
    if (m) return Math.abs(+m[1]) > 90 || Math.abs(+m[2]) > 180 ? setErr(T.coordErr) : onPick(+m[1], +m[2])
    if (q.length < 2) return
    setBusy(true)
    try {
      await stream('/api/places', { q }, ev => {
        if (ev.type === 'places') setFound(ev.places)
        if (ev.type === 'error') setErr(T.searchFailed)
      })
    } catch (x) { setErr((x as Error).message) }
    setBusy(false)
  }
  return <>
    <form className="row coordform" noValidate onSubmit={submit}>
      <label htmlFor={inputId} className="sr">{T.searchLabel}</label>
      <input type="text" id={inputId} name="q" placeholder={T.searchPh} autoComplete="off" />
      <button type="submit" disabled={busy}>{T.search}</button>
    </form>
    <p className="err" role="alert">{err}</p>
    {found && <div className="found" aria-live="polite">{found.length ? <>
      {found.map((f, i) => <button type="button" key={i} onClick={() => { setFound(null); onPick(f.lat, f.lon) }}>
        <b>{f.name}</b><span className="dim">{f.address}</span></button>)}
      <p className="src">Google Maps</p></> : <p className="hint">{T.noPlaces}</p>}</div>}
  </>
}

type Mine = { id: string, created_at: string, lat: number, lon: number, text: string | null }

function MyReports() {  // a link reloads into the saved-report path, so there is one way a report opens
  const [list, setList] = useState<Mine[]>([])
  useEffect(() => { fetch('/api/reports').then(r => r.ok ? r.json() : []).then(setList, () => {}) }, [])
  if (!list.length) return null
  return <nav className="found mine" aria-label={T.myReports}>
    <p className="count">{T.myReports}</p>
    {list.map(r => <a key={r.id} href={`/r/${r.id}`}>
      <b>{new Date(r.created_at).toLocaleString(T.locale, { dateStyle: 'medium', timeStyle: 'short' })} · {r.lat.toFixed(4)}, {r.lon.toFixed(4)}</b>
      <span className="dim">{r.text}</span></a>)}
  </nav>
}

export default function App() {
  const { data: session, isPending } = authClient.useSession()
  const mapEl = useRef<HTMLDivElement>(null)
  const readout = useRef<HTMLDivElement>(null)
  const dbody = useRef<HTMLDivElement>(null)
  const tlist = useRef<HTMLOListElement>(null)
  const whoEl = useRef<HTMLTextAreaElement>(null)
  const map = useRef<L.Map>(null)
  // the map and the streaming run are imperative, so their state lives in refs and rerender() redraws
  const pins = useRef<Partial<Record<Site, { lat: number, lon: number, marker: L.Marker }>>>({})
  const prog = useRef<Prog>(null)
  const flying = useRef(false)
  const drawerRef = useRef(false)
  const lastRun = useRef<() => Promise<void>>(null)
  const [, rerender] = useReducer((n: number) => n + 1, 0)
  const [stage, setStageState] = useState<Stage>('pick')
  const [who, setWho] = useState('')
  const [states, setStates] = useState<Partial<Record<Site, State>>>({})
  const [running, setRunning] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [traceOpen, setTraceOpen] = useState(true)
  const [out, setOut] = useState<Out>(null)
  const [lender, setLender] = useState<State | null>(null)
  const [pdf, setPdf] = useState<{ state: State, label: string } | null>(null)
  const [translating, setTranslating] = useState<'' | 'busy' | 'failed'>('')

  // ---------- map ----------
  const onMapClick = useEffectEvent((lat: number, lon: number) => choose(lat, lon))
  useEffect(() => {
    const sat = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      { maxZoom: 19, attribution: 'Imagery © Esri, Maxar, Earthstar Geographics' })
    const m = L.map(mapEl.current!, { layers: [sat], zoomControl: false }).setView([15.5, 101], 6)
    L.control.zoom({ position: 'topright' }).addTo(m)
    m.on('mousemove', e => { readout.current!.textContent = `Lat ${e.latlng.lat.toFixed(5)}  Lon ${e.latlng.lng.toFixed(5)}` })
    m.on('click', e => onMapClick(e.latlng.lat, e.latlng.lng))
    map.current = m
    return () => { m.remove() }
  }, [])

  // ---------- flow: pick -> who -> run -> done (-> pickB -> run) ----------
  function fly(lat: number, lon: number) {
    flying.current = true
    map.current!.flyTo([lat, lon], 15)
    map.current!.once('moveend', () => { flying.current = false })
  }

  function setStage(s: Stage) {
    setStageState(s)
    document.body.dataset.stage = s
    if (s === 'run') setDrawer(true)
    if (s === 'pickB' || s === 'pick' || s === 'who') setDrawer(false)
  }

  function setDrawer(open: boolean) {
    if (open === drawerRef.current) return
    drawerRef.current = open
    setDrawerOpen(open)
    document.body.classList.toggle('drawer-open', open)
    const A = pins.current.A, m = map.current!
    if (A) {  // centre site A in the part of the map the drawer leaves visible; one setView overrides any running fly-to
      const z = flying.current ? 15 : m.getZoom()
      const off: L.PointTuple = !open ? [0, 0] : mobile() ? [0, innerHeight * .36] : [260, 0]
      m.setView(m.unproject(m.project([A.lat, A.lon], z).add(off), z), z)
    }
  }

  function marker(key: Site, lat: number, lon: number) {
    pins.current[key]?.marker.remove()
    const icon = L.divIcon({ className: '', iconSize: [44, 44], iconAnchor: [22, 29], html:  // anchor = the dot
      `<div class="pin ${key}"><svg viewBox="0 0 64 64" aria-hidden="true"><path class="under" d="${CONTOUR}"/><path d="${CONTOUR}"/><circle cx="32" cy="42" r="4.5"/></svg><b>${key}</b></div>` })
    pins.current[key] = { lat, lon, marker: L.marker([lat, lon], { icon, keyboard: false, title: T.site(key) }).addTo(map.current!) }
    rerender()
  }

  function choose(lat: number, lon: number) {
    if (running || !session) return
    lat = +lat.toFixed(4); lon = +lon.toFixed(4)  // ~11 m, the same snap the agent caches by
    if (stage === 'pickB') {
      marker('B', lat, lon)
      runCompare()  // opens the drawer, so fit both sites into the map area it leaves visible
      const A = pins.current.A!
      map.current!.fitBounds([[A.lat, A.lon], [lat, lon]], { maxZoom: 15, paddingTopLeft: [60, 60],
        paddingBottomRight: mobile() ? [60, innerHeight * .76 + 40] : [520 + 60, 60] })
      return
    }
    if (pins.current.B) { pins.current.B.marker.remove(); delete pins.current.B }
    history.replaceState(null, '', '/')  // a new site: the address no longer names the saved report
    marker('A', lat, lon)
    setStage('who')
  }

  function pick(lat: number, lon: number) {
    if (stage !== 'pickB') fly(lat, lon)
    choose(lat, lon)
  }

  function newSite() {
    history.replaceState(null, '', '/')
    Object.values(pins.current).forEach(p => p.marker.remove())
    pins.current = {}
    setStates({})
    setStage('pick')
  }

  const siteA = pins.current.A && `${pins.current.A.lat.toFixed(4)}, ${pins.current.A.lon.toFixed(4)}`
  useEffect(() => {
    if (stage === 'who' && !matchMedia('(pointer: coarse)').matches) whoEl.current?.focus()  // no surprise keyboard on phones
  }, [stage, siteA])

  // ---------- progress: stepper, timer, agent log ----------
  useEffect(() => {
    if (!running) return
    const t = setInterval(rerender, 1000)
    return () => clearInterval(t)
  }, [running])

  const logLength = prog.current?.log.length
  useEffect(() => {
    if (traceOpen) tlist.current?.lastElementChild?.scrollIntoView({ block: 'nearest' })
  }, [logLength])

  function newRun(title: string) {
    prog.current = { title, t0: Date.now(), calls: 0, seen: 0, dropped: 0, you: 'todo', report: 'todo', ch: {}, log: [] }
    setStage('run')
    setTraceOpen(true)
    setOut({ kind: 'skel' })
    dbody.current!.scrollTop = 0
    startSite(title)
  }

  function startSite(title: string) {
    const p = prog.current!
    p.ch = Object.fromEntries(Object.keys(T.ch).map(k => [k, { open: 0, ok: 0, err: 0 }]))
    p.you = p.report = 'todo'
    p.title = title
    rerender()
  }

  function endRun(ok: boolean) {
    const p = prog.current!
    p.title = ok ? T.complete(clock(elapsed(p))) : T.interrupted
    p.end = T.logEnd(p.calls, p.seen, p.dropped)
    setTraceOpen(!ok)
  }

  function trace(ev: Ev) {
    const p = prog.current!
    const add = (tag: string, body: ReactNode, kind = '') => p.log.push({ t: `T+${clock(elapsed(p))}`, tag, body, kind })
    const ch = p.ch[TOOL_CH[ev.name]]
    switch (ev.type) {
      case 'site': add(T.tag.site, ev.text, 'sitel'); break
      case 'step':
        add(T.tag.step, T.phase[ev.phase] ?? ev.text)
        if (ev.phase === 'intake') p.you = 'active'
        if (ev.phase === 'judge') p.report = 'active'
        break
      case 'profile': {
        const f = ev.profile, w = (k: string) => <b>{T.weight[f[k]] ?? f[k]}</b>, h = T.hazard
        p.you = 'done'
        add(T.tag.profile, <>{f.who}. {T.weights}: {h.flood} {w('flood')}, {h.heat} {w('heat')}, {h.smoke} {w('smoke')}, {h.change} {w('change')}, {h.nearby} {w('nearby')}.<br /><span className="dim">{f.why}</span></>)
        break
      }
      case 'thought': add(T.tag.reason, ev.text.length > 280 ? ev.text.slice(0, 280) + '…' : ev.text, 'reason'); break
      case 'say': add(T.tag.plan, ev.text); break
      case 'call':
        p.calls++
        if (ch) ch.open++
        add(T.tag.query, NAMES[ev.name]?.(ev.args) ?? ev.name)
        break
      case 'result':
        if (ch) { ch.open = Math.max(0, ch.open - 1); if (ev.result.error) ch.err++; else ch.ok++ }
        add(ev.result.error ? T.tag.failed : T.tag.result, <><b>{T.short[ev.name] ?? ev.name}:</b> {summary(ev.name, ev.result)}</>, ev.result.error ? 'bad' : '')
        if (ev.result.visual_description) { p.seen++; add(T.tag.image, <><b>Gemini:</b> {ev.result.visual_description}</>, 'img') }
        break
      case 'guardrail':
        p.dropped += ev.dropped.length
        add(T.tag.guardrail, T.removed(ev.dropped.length), 'guard')
        break
      case 'error': add(T.tag.error, ev.text, 'bad'); break
    }
    rerender()
  }

  async function busy(fn: () => Promise<void>) {
    setRunning(true)
    lastRun.current = fn
    let ok = true
    try { await fn() } catch (e) {
      ok = false
      trace({ type: 'error', text: (e as Error).message })
      setOut({ kind: 'error', msg: (e as Error).message })
    }
    setRunning(false)
    endRun(ok)
    setStage('done')
    setDrawer(true)
  }

  async function runReport(key: Site, text: string) {
    const p = pins.current[key]!
    let state: State | undefined
    startSite(T.analysingSite(key))
    trace({ type: 'site', text: `${T.site(key)} · ${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}` })
    await stream('/api/report', { lat: p.lat, lon: p.lon, text: text || T.defaultWho }, ev => {
      if (ev.type !== 'report') return trace(ev)
      state = { ...ev.state, id: ev.id }  // the server saved it under this id
      prog.current!.report = 'done'
      rerender()
    })
    if (!state) throw new Error(T.noReport)
    return state
  }

  const analyse = () => busy(async () => {
    newRun(T.analysingSite('A'))
    const A = await runReport('A', who.trim())
    setStates({ A })
    setOut({ kind: 'report', state: A })
    if (A.id) history.replaceState(null, '', `/r/${A.id}`)  // a refresh or a shared link reopens it
    dbody.current!.scrollTop = 0
  })

  function runCompare() {
    busy(async () => {
      newRun(T.analysingSite('A'))
      const text = who.trim(), a = pins.current.A!
      const A = states.A && states.A.lat === a.lat && states.A.lon === a.lon && states.A.text === text && states.A.lang === lang
        ? states.A : await runReport('A', text)
      const B = await runReport('B', text)
      setStates({ A, B })
      let cmp: Compare | undefined
      prog.current!.title = T.comparing
      rerender()
      await stream('/api/compare', { profile: A.profile, a: slim(A), b: slim(B) },
        ev => { if (ev.type === 'compare') cmp = ev.compare; else trace(ev) })
      if (!cmp) throw new Error(T.noCompare)
      setOut({ kind: 'compare', cmp, A, B })
      dbody.current!.scrollTop = 0
    })
  }

  // ---------- language switch: Gemini's text follows; a run in flight is caught up once it ends ----------
  useEffect(() => {
    if (running) return
    const to = lang
    const stale = [...Object.values(states), out?.kind === 'compare' && out.cmp].filter(x => x && x.lang !== to) as (State | Compare)[]
    if (!stale.length) return setTranslating('')
    setTranslating('busy')
    Promise.all(stale.map(translate)).then(() => '' as const, () => 'failed' as const)
      .then(s => { if (to === lang) { setTranslating(s); rerender() } })
  }, [lang, running])

  // ---------- a saved report's link opens it, once signed in ----------
  const userId = session?.user.id
  useEffect(() => {
    if (!userId || !SAVED) return
    fetch(`/api/reports/${SAVED}`).then(r => r.ok ? r.json() : Promise.reject(new Error(T.noSaved))).then((A: State) => {
      fly(A.lat, A.lon)
      marker('A', A.lat, A.lon)
      setWho(A.text)
      setStates({ A })
      setOut({ kind: 'report', state: A })
    }, (e: Error) => setOut({ kind: 'error', msg: e.message })).finally(() => { setStage('done'); setDrawer(true) })
  }, [userId])

  // ---------- lender view and PDF ----------
  function openLender(state: State) {
    setLender(state)
    document.body.classList.add('lender')
    scrollTo(0, 0)
  }

  function closeLender() {
    document.body.classList.remove('lender')
    setLender(null)
    map.current!.invalidateSize()
    document.querySelector<HTMLElement>('[data-lender]')?.focus()
  }

  async function downloadPdf(state: State, label: string) {
    flushSync(() => setPdf({ state, label }))
    await Promise.all([...document.querySelectorAll<HTMLImageElement>('#pdf img')].map(i => i.decode().catch(() => {})))  // print only once the images are drawn
    const title = document.title
    document.title = `Orra ${state.lat}, ${state.lon}`  // the browser's default PDF file name
    document.body.classList.add('pdf')
    addEventListener('afterprint', () => { document.body.classList.remove('pdf'); document.title = title }, { once: true })
    print()
  }

  // ---------- view ----------
  const p = prog.current
  const chState = (c: Prog['ch'][string]): Status => c.open ? 'active' : c.ok ? 'done' : c.err ? 'err' : 'todo'
  const checked = p ? Object.values(p.ch).filter(c => !c.open && (c.ok || c.err)).length : 0

  return <>
    <Header />

    <div className="mapwrap">
      <div id="map" ref={mapEl} aria-label={T.mapLabel} />
      <div className="scan" aria-hidden="true" hidden={!running} />
      <div className="readout tech" ref={readout} aria-hidden="true">Lat — Lon —</div>
    </div>

    <section className="prompt" aria-label="Set up the analysis" hidden={isPending || !['pick', 'who', 'pickB'].includes(stage)}>
      {!session ? <div>
        <div className="count">{T.welcome}</div>
        <h2>{SAVED ? T.signInReport : T.signInTitle}</h2>
        <p className="hint">{T.signInHint}</p>
        <button type="button" className="primary wide" onClick={signIn}>{T.signIn}</button>
      </div> : stage === 'pick' ? <div>
        <div className="count">{T.step(1)}</div>
        <h2>{T.selectSite}</h2>
        <p className="hint">{T.selectHint}</p>
        <Picker inputId="coordA" onPick={pick} />
        <p className="tech detects">{T.detects}</p>
        <MyReports />
      </div> : stage === 'who' ? <div>
        <div className="count">{T.step(2)}</div>
        <h2><label htmlFor="who">{T.whoTitle}</label></h2>
        <div className="site"><span className="sitetag">A</span><span className="coord">{siteA}</span>
          <button type="button" className="small" onClick={() => setStage('pick')}>{T.change}</button></div>
        <p className="hint" id="whohint">{T.whoHint}</p>
        <textarea id="who" ref={whoEl} maxLength={2000} aria-describedby="whohint" value={who} onChange={e => setWho(e.target.value)}
          placeholder={T.whoPlaceholder} />
        <Mic onText={t => setWho(w => `${w.trim()} ${t}`.trim().slice(0, 2000))} />
        <button type="button" className="primary wide" disabled={running || !pins.current.A} onClick={analyse}>{running ? T.analysing : T.analyse}</button>
      </div> : <div>
        <div className="count">{T.compare}</div>
        <h2>{T.selectB}</h2>
        <p className="hint">{T.selectBHint}</p>
        <Picker inputId="coordB" onPick={pick} />
        <button type="button" className="wide" onClick={() => { setStage('done'); setDrawer(true) }}>{T.cancel}</button>
      </div>}
    </section>

    <aside className="drawer" aria-label={T.drawer} inert={!drawerOpen}
      onClick={e => e.target === e.currentTarget && setDrawer(false)}>{/* a click on the dimmed map closes the report */}
      <div className="dhead">
        <span className="tech"><span className="stepno">{T.step(3)} · </span>{T.analysis}</span>
        <div className="row">
          <span className="row" hidden={running || stage !== 'done'}>
            <button type="button" className="small" hidden={!states.A} onClick={() => setStage('pickB')}>{T.compare}</button>
            <button type="button" className="small" onClick={newSite}>{T.newSite}</button>
          </span>
          <button type="button" className="small" onClick={() => setDrawer(false)}>{stage === 'done' ? T.viewMap : `${T.hide} ${mobile() ? '▾' : '▸'}`}</button>
        </div>
      </div>
      <div className="dbody" ref={dbody} aria-busy={running}>
        <div className="mod" id="progress" hidden={!p}>
          <div className="row between">
            <span className="tech">{p?.title ?? T.analysing}</span>
            <span className="tech timer" aria-hidden="true">{running && p ? 'T+' + clock(elapsed(p)) : ''}</span>
          </div>
          <p className="sr" role="status">{p && T.checked(p.title, checked, Object.keys(T.ch).length, p.report === 'active')}</p>
          <ol className="stepper" id="stepper" aria-hidden="true">{p && <>
            <Step state={p.you} name={T.profile} />
            {Object.entries(T.ch).map(([k, c]) => <Step key={k} state={chState(p.ch[k])} name={c.short} />)}
            <Step state={p.report} name={T.report} sub={p.report === 'active' ? T.writing : undefined} />
          </>}</ol>
          <details className="log" open={traceOpen} onToggle={e => setTraceOpen(e.currentTarget.open)}>
            <summary className="tech">{p?.end ?? (p ? T.logLive : T.log)}</summary>
            <ol className="tlist" ref={tlist}>{p?.log.map((l, i) =>
              <li key={i} className={l.kind}><time>{l.t}</time><span className="tag">{l.tag}</span><span className="lmsg">{l.body}</span></li>)}</ol>
          </details>
        </div>
        {translating && <p className="hint" role="status">{translating === 'busy' ? T.translating : T.translateFailed}</p>}
        <div id="out">
          {out?.kind === 'skel' && <>{SKEL}{SKEL}{SKEL}</>}
          {out?.kind === 'error' && <div className="mod" role="alert">
            <div className="label"><span className="num">!</span><span className="disp">{T.interrupted}</span></div>
            <p className="hint">{out.msg}</p>
            {lastRun.current && <button type="button" className="primary" onClick={() => !running && busy(lastRun.current!)}>{T.retry}</button>}
          </div>}
          {out?.kind === 'report' && <><Report state={out.state} onPdf={downloadPdf} onLender={openLender} /><Chat state={out.state} /></>}
          {out?.kind === 'compare' && <CompareView {...out} onPdf={downloadPdf} onLender={openLender} />}
        </div>
        <footer>{footer()}</footer>
      </div>
    </aside>
    <button type="button" className="tab" hidden={drawerOpen || !(running || states.A)} onClick={() => setDrawer(true)}>{`${mobile() ? '▲' : '◂'} ${T.reportTab}`}</button>
    <div id="lender">{lender && <Lender state={lender} onBack={closeLender} />}</div>
    <div id="pdf">{pdf && <Pdf {...pdf} />}</div>
  </>
}
