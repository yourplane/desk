import { useEffect, useState } from 'react'
import { ensureAuth, getToken, goToLogin, handleCallback, isAuthEnabled } from './auth'
import { InstanceList } from './pages/InstanceList'
import { CostTracker } from './pages/CostTracker'
import './App.css'

type Page = 'workstations' | 'costs'

function buildInfo(): string | null {
  const deployedAt = (import.meta.env.VITE_BUILD_AT as string | undefined)?.trim()
  const sha = (import.meta.env.VITE_BUILD_SHA as string | undefined)?.trim()
  if (!deployedAt && !sha) return null
  if (deployedAt && sha) return `Built ${deployedAt} (${sha})`
  return deployedAt ? `Built ${deployedAt}` : `Built (${sha})`
}

function App() {
  const [ready, setReady] = useState(false)
  const [isCallback, setIsCallback] = useState(false)
  const [callbackFailed, setCallbackFailed] = useState(false)
  const [page, setPage] = useState<Page>('workstations')
  const info = buildInfo()

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get('code')) {
      setIsCallback(true)
      setCallbackFailed(false)
      handleCallback().then((ok) => {
        if (ok) {
          window.history.replaceState({}, '', window.location.pathname || '/')
          setReady(true)
          setCallbackFailed(false)
        } else {
          setReady(true)
          setCallbackFailed(true)
        }
        setIsCallback(false)
      })
      return
    }
    if (!isAuthEnabled()) {
      setReady(true)
      return
    }
    ensureAuth().then((ok) => setReady(ok))
  }, [])

  if (isCallback) {
    return (
      <div className="app">
        <p>Signing in…</p>
      </div>
    )
  }
  if (!ready) {
    return (
      <div className="app">
        <p>Redirecting to login…</p>
      </div>
    )
  }
  if (callbackFailed) {
    return (
      <div className="app">
        <h1 className="page-title">Sign-in failed</h1>
        <p>We couldn't complete sign-in. This can happen if the link was used twice or expired.</p>
        <button type="button" className="btn btn-start" onClick={() => goToLogin()}>
          Try again
        </button>
      </div>
    )
  }
  if (isAuthEnabled() && !getToken()) {
    return (
      <div className="app">
        <h1 className="page-title">No session</h1>
        <p>Please log in to view workstations.</p>
        <button type="button" className="btn btn-start" onClick={() => goToLogin()}>
          Log in
        </button>
      </div>
    )
  }
  return (
    <div className="app">
      <nav className="app-nav">
        <button
          type="button"
          className={`nav-tab${page === 'workstations' ? ' nav-tab--active' : ''}`}
          onClick={() => setPage('workstations')}
        >
          Workstations
        </button>
        <button
          type="button"
          className={`nav-tab${page === 'costs' ? ' nav-tab--active' : ''}`}
          onClick={() => setPage('costs')}
        >
          Costs
        </button>
      </nav>
      {page === 'workstations' && <InstanceList />}
      {page === 'costs' && <CostTracker />}
      {info && <p className="build-info">{info}</p>}
    </div>
  )
}

export default App
