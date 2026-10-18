// The header and the admin page: what each kind of visitor is shown. The server enforces access too (see server.test.ts);
// this only checks that the page offers the right thing. The Better Auth client is replaced by a stub whose session each test sets.
// Local dev tests: from web/, run `node --test "tests/*.test.ts"`.
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { after, beforeEach, describe, test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const define = (name: string, value: unknown) => Object.defineProperty(globalThis, name, { value, configurable: true, writable: true })
const html = { lang: '', dataset: {} as Record<string, string> }
define('document', { documentElement: html })
define('navigator', { language: 'en-US' })
define('localStorage', { getItem: () => null, setItem: () => {} })
define('location', { origin: 'https://orra.test', pathname: '/admin' })

type Session = { data: { user: Record<string, unknown> } | null, isPending: boolean }
let session: Session = { data: null, isPending: false }
define('__fakeAuth', { useSession: () => session, signOut: () => {}, admin: {} })

const vite = await createServer({
  configFile: false, root: fileURLToPath(new URL('..', import.meta.url)), logLevel: 'silent', appType: 'custom',
  server: { middlewareMode: true, watch: null, hmr: false }, optimizeDeps: { noDiscovery: true, include: [] },
  plugins: [{
    name: 'fake-auth-client', enforce: 'pre',
    resolveId: source => source === './auth-client.ts' ? '\0fake-auth-client' : null,
    load: id => id === '\0fake-auth-client' ? 'export const authClient = globalThis.__fakeAuth; export const signIn = () => {}' : null,
  }],
})
after(() => vite.close())

const I18N = await vite.ssrLoadModule('/src/i18n.ts') as typeof import('../src/i18n.ts')
const Header = (await vite.ssrLoadModule('/src/Header.tsx')).default as typeof import('../src/Header.tsx').default
const Admin = (await vite.ssrLoadModule('/src/Admin.tsx')).default as typeof import('../src/Admin.tsx').default

const plain = (markup: string) => markup.replace(/<[^>]*>/g, ' ').replace(/&#x27;/g, "'").replace(/\s+/g, ' ').trim()
const visitor = (user: Record<string, unknown> | null, isPending = false) => { session = { data: user && { user }, isPending } }
const ann = { id: 'u1', email: 'ann@gmail.com', role: 'user' }
const dev = { id: 'u2', email: 'dev@example.org', role: 'admin' }

beforeEach(() => { I18N.setLang('en'); delete html.dataset.theme; visitor(null) })

describe('Header', () => {
  const view = () => renderToStaticMarkup(createElement(Header))

  test('shows the brand, the tagline and both languages with the current one pressed', () => {
    const m = view()
    assert.match(m, /<img src="\/logo-white\.svg" alt="Orra"\/>/)
    assert.match(plain(m), /Know the ground before you sign/)
    assert.match(m, /<button type="button" lang="en" class="small" aria-pressed="true">English<\/button>/)
    assert.match(m, /<button type="button" lang="th" class="small" aria-pressed="false">ไทย<\/button>/)
    I18N.setLang('th')
    assert.match(view(), /lang="th" class="small" aria-pressed="true"/)
    assert.match(plain(view()), /รู้จักพื้นที่ก่อนเซ็นสัญญา/)
  })

  test('offers the other theme, with the matching logo', () => {
    assert.match(view(), /aria-label="Switch to light theme"/)
    html.dataset.theme = 'light'
    const m = view()
    assert.match(m, /aria-label="Switch to dark theme"/)
    assert.match(m, /<img src="\/logo\.svg" alt="Orra"\/>/)
  })

  test('a visitor who is not signed in sees no admin link and no sign-out', () => {
    const text = plain(view())
    assert.doesNotMatch(text, /Admin|Sign out/)
  })

  test('a signed-in person can sign out, and only an admin sees the admin link', () => {
    visitor(ann)
    assert.match(view(), /<button type="button" class="small" title="ann@gmail\.com">Sign out<\/button>/)
    assert.doesNotMatch(view(), /href="\/admin"/)
    visitor(dev)
    assert.match(view(), /<a href="\/admin">Admin<\/a>/)
  })
})

describe('Admin', () => {
  const view = () => renderToStaticMarkup(createElement(Admin))

  test('shows nothing while the session is still loading', () => {
    visitor(null, true)
    assert.doesNotMatch(plain(view()), /Sign in|Admins only|Users/)
  })

  test('asks a visitor who is not signed in to sign in', () => {
    const m = view()
    assert.match(plain(m), /Admin Sign in with your staff Google account\./)
    assert.match(m, /<button type="button" class="primary">Sign in with Google<\/button>/)
    assert.doesNotMatch(m, /Search by email/)
  })

  test('tells a signed-in person who is not an admin that the page is not for them, naming their account', () => {
    visitor(ann)
    const text = plain(view())
    assert.match(text, /Admins only/)
    assert.match(text, /You are signed in as ann@gmail\.com\./)
    assert.doesNotMatch(text, /Search by email|Platform/)
  })

  test('shows an admin the user search and the Users / Platform tabs, Users first', () => {
    visitor(dev)
    const m = view()
    assert.match(m, /<button type="button" class="small" aria-pressed="true">Users<\/button>/)
    assert.match(m, /<button type="button" class="small" aria-pressed="false">Platform<\/button>/)
    assert.match(m, /<input type="text" id="q"[^>]*placeholder="Search by email"/)
    assert.doesNotMatch(plain(m), /Admins only|Sign in with Google/)
  })
})
