import { useEffect, useState } from 'react'
import { ensureAuth, getToken, goToLogin, handleCallback, isAuthEnabled } from './auth'
import { InstanceList } from './pages/InstanceList'
import './App.css'

function App() {
  const [ready, setReady] = useState(false)
  const [isCallback, setIsCallback] = useState(false)
  const [callbackFailed, setCallbackFailed] = useState(false)

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
        <p>We couldn’t complete sign-in. This can happen if the link was used twice or expired.</p>
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
      <InstanceList />
    </div>
  )
}

export default App
