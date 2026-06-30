import { useLayoutEffect, useState } from 'react'
import {
  clearAuthTokens,
  ensureAuth,
  getToken,
  goToLogin,
  handleCallback,
  isAuthEnabled,
  readOAuthAuthorizeError,
} from './auth'
import { WorkstationsPage } from './pages/WorkstationsPage'
import { CostTracker } from './pages/CostTracker'
import { ReaperPage } from './pages/ReaperPage'
import { CommandPage } from './pages/CommandPage'
import { AmiBuildsPage } from './pages/AmiBuildsPage'
import './App.css'

type Page = 'workstations' | 'costs' | 'reaper' | 'command' | 'ami-builds'
type CommandSection = 'manage' | 'run'

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
  const [authorizeError, setAuthorizeError] = useState<{ error: string; description: string } | null>(
    null,
  )
  const [page, setPage] = useState<Page>('workstations')
  const [commandSection, setCommandSection] = useState<CommandSection>('manage')
  const info = buildInfo()

  useLayoutEffect(() => {
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
    if (isAuthEnabled()) {
      const oauthErr = readOAuthAuthorizeError()
      if (oauthErr) {
        setAuthorizeError({ error: oauthErr.error, description: oauthErr.errorDescription })
        window.history.replaceState({}, '', window.location.pathname || '/')
        setReady(true)
        return
      }
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
  if (authorizeError) {
    return (
      <div className="app">
        <h1 className="page-title">Sign-in error</h1>
        <p role="alert">
          {authorizeError.description.trim() || authorizeError.error}
        </p>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem', marginTop: '0.75rem' }}>
          If you see <code>invalid_scope</code>, the Cognito app client must allow the same scopes
          the app requests (<code>openid</code>, <code>email</code>, <code>profile</code>), then try
          again.
        </p>
        <button
          type="button"
          className="btn btn-start"
          style={{ marginTop: '1rem' }}
          onClick={() => {
            clearAuthTokens()
            setAuthorizeError(null)
            void goToLogin()
          }}
        >
          Try again
        </button>
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
          className={`app-nav-tab${page === 'workstations' ? ' app-nav-tab--active' : ''}`}
          onClick={() => setPage('workstations')}
        >
          Workstations
        </button>
        <button
          type="button"
          className={`app-nav-tab${page === 'costs' ? ' app-nav-tab--active' : ''}`}
          onClick={() => setPage('costs')}
        >
          Costs
        </button>
        <button
          type="button"
          className={`app-nav-tab${page === 'reaper' ? ' app-nav-tab--active' : ''}`}
          onClick={() => setPage('reaper')}
        >
          Reaper
        </button>
        <button
          type="button"
          className={`app-nav-tab${page === 'command' ? ' app-nav-tab--active' : ''}`}
          onClick={() => {
            setPage('command')
            setCommandSection('manage')
          }}
        >
          Command
        </button>
        <button
          type="button"
          className={`app-nav-tab${page === 'ami-builds' ? ' app-nav-tab--active' : ''}`}
          onClick={() => setPage('ami-builds')}
        >
          AMI Builds
        </button>
      </nav>
      {page === 'workstations' && <WorkstationsPage />}
      {page === 'costs' && <CostTracker />}
      {page === 'reaper' && <ReaperPage />}
      {page === 'ami-builds' && <AmiBuildsPage />}
      {page === 'command' && (
        <CommandPage
          initialSection={commandSection}
        />
      )}
      {info && <p className="build-info">{info}</p>}
    </div>
  )
}

export default App
