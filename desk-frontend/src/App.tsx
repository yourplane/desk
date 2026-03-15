import { useEffect, useState } from 'react'
import { ensureAuth, handleCallback, isAuthEnabled } from './auth'
import { InstanceList } from './pages/InstanceList'
import './App.css'

function App() {
  const [ready, setReady] = useState(false)
  const [isCallback, setIsCallback] = useState(false)

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get('code')) {
      setIsCallback(true)
      handleCallback().then((ok) => {
        if (ok) {
          window.history.replaceState({}, '', window.location.pathname || '/')
          setReady(true)
          setIsCallback(false)
        } else {
          setReady(true)
          setIsCallback(false)
        }
      })
      return
    }
    if (!isAuthEnabled()) {
      setReady(true)
      return
    }
    setReady(ensureAuth())
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
  return (
    <div className="app">
      <InstanceList />
    </div>
  )
}

export default App
