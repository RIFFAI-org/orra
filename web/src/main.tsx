import 'leaflet/dist/leaflet.css'
import './app.css'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import Admin from './Admin.tsx'
import App from './App.tsx'
import { useLang } from './i18n.ts'

// shortcut: two pages, so a pathname check instead of a router; add react-router when a third page arrives
function Root() {
  useLang()  // a language switch re-renders the whole tree, which reads the current T
  return location.pathname.startsWith('/admin') ? <Admin /> : <App />
}

createRoot(document.getElementById('root')!).render(<StrictMode><Root /></StrictMode>)
