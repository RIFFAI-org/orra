import { useState } from 'react'
import { authClient } from './auth-client.ts'
import { T, lang, setLang } from './i18n.ts'
import { Icon } from './report.tsx'

const LANGS = [['en', 'English'], ['th', 'ไทย']] as const  // each named in its own language, so a reader spots theirs

function setTheme(t: 'light' | 'dark') {
  document.documentElement.dataset.theme = t  // index.html applies the saved one before first paint
  try { localStorage.setItem('theme', t) } catch { /* private window: the choice lasts this visit */ }
}

export default function Header() {
  const user = authClient.useSession().data?.user
  const [light, setLight] = useState(document.documentElement.dataset.theme === 'light')
  const flip = light ? T.toDark : T.toLight
  return (
    <header>
      <div className="brand"><a href="/"><img src={light ? '/logo.svg' : '/logo-white.svg'} alt="Orra" /></a><span className="tech tag">{T.tagline}</span></div>
      <div className="tech meta">
        {user?.role === 'admin' && <a href="/admin">{T.admin}</a>}
        <div className="langs" role="group" aria-label={T.language}><Icon n="globe" />
          {LANGS.map(([l, name]) =>
            <button type="button" key={l} lang={l} className="small" aria-pressed={lang === l} onClick={() => setLang(l)}>{name}</button>)}
        </div>
        <button type="button" className="small" aria-label={flip} title={flip}
          onClick={() => { setTheme(light ? 'dark' : 'light'); setLight(!light) }}><Icon n={light ? 'moon' : 'sun'} /></button>
        {user && <button type="button" className="small" title={user.email} onClick={() => authClient.signOut()}>{T.signOut}</button>}
        <span className="riffai">RIFFAI</span>
      </div>
    </header>
  )
}
