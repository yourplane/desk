import { useEffect, useRef, useState } from 'react'
import { listInstances, startInstance, stopInstance, type Instance } from '../api/client'
import { logout } from '../auth'

const POLL_INTERVAL_MS = 30_000
const BACKGROUND_POLL_INTERVAL_MS = 5 * 60 * 1000

function formatShutdownLocal(isoUtc: string | null, state: string): { absolute: string; relative: string } {
  if (!isoUtc || state === 'stopped' || state === 'stopping' || state === 'terminated' || state === 'shutting-down') {
    return { absolute: '—', relative: '' }
  }
  try {
    const d = new Date(isoUtc)
    if (Number.isNaN(d.getTime())) return { absolute: isoUtc, relative: '' }
    const absolute = d.toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
    const now = Date.now()
    const diffMs = d.getTime() - now
    let relative = ''
    if (diffMs > 0) {
      const totalM = Math.floor(diffMs / 60000)
      const h = Math.floor(totalM / 60)
      const m = totalM % 60
      relative = h > 0 ? `in ${h}h ${m}m` : `in ${m}m`
    } else {
      const totalM = Math.floor(-diffMs / 60000)
      const h = Math.floor(totalM / 60)
      const m = totalM % 60
      relative = h > 0 ? `${h}h ${m}m ago` : `${m}m ago`
    }
    return { absolute, relative }
  } catch {
    return { absolute: isoUtc, relative: '' }
  }
}

function stateColor(state: string): string {
  switch (state) {
    case 'running': return 'var(--state-running)'
    case 'pending': return 'var(--state-pending)'
    case 'stopped': return 'var(--state-stopped)'
    case 'stopping': return 'var(--state-pending)'
    default: return 'var(--state-default)'
  }
}

export function InstanceList() {
  const [instances, setInstances] = useState<Instance[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshError, setRefreshError] = useState<string | null>(null)
  const [acting, setActing] = useState<string | null>(null)
  const loadInFlightRef = useRef(false)
  const actingRef = useRef<string | null>(null)
  actingRef.current = acting

  const load = async (opts?: { isBackgroundRefresh?: boolean }) => {
    const isBackground = opts?.isBackgroundRefresh === true
    if (loadInFlightRef.current) return
    loadInFlightRef.current = true
    if (!isBackground) {
      setLoading(true)
      setError(null)
    }
    try {
      const list = await listInstances()
      setInstances(list)
      setRefreshError(null)
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      const fallback = 'Unable to load workstations. Check the browser console or API logs.'
      if (isBackground) {
        setRefreshError('Could not refresh. Will retry.')
        console.error('InstanceList poll failed:', e)
      } else {
        setError(!msg.trim() ? fallback : msg)
        console.error('InstanceList load failed:', e)
      }
    } finally {
      if (!isBackground) setLoading(false)
      loadInFlightRef.current = false
    }
  }

  useEffect(() => {
    load()

    let intervalId: ReturnType<typeof setInterval> | null = null
    let intervalMs = POLL_INTERVAL_MS

    const schedule = () => {
      if (intervalId) clearInterval(intervalId)
      intervalId = window.setInterval(() => {
        if (loadInFlightRef.current || actingRef.current !== null) return
        load({ isBackgroundRefresh: true })
      }, intervalMs)
    }
    schedule()

    const onVisibility = () => {
      intervalMs = document.visibilityState === 'hidden' ? BACKGROUND_POLL_INTERVAL_MS : POLL_INTERVAL_MS
      schedule()
    }
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      document.removeEventListener('visibilitychange', onVisibility)
      if (intervalId) clearInterval(intervalId)
    }
  }, [])

  const onStart = async (name: string) => {
    setActing(name)
    setError(null)
    try {
      await startInstance(name)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setActing(null)
    }
  }

  const onStop = async (name: string) => {
    setActing(name)
    setError(null)
    try {
      await stopInstance(name)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setActing(null)
    }
  }

  if (loading) {
    return (
      <div className="instance-list">
        <h1 className="page-title">Workstations</h1>
        <p className="loading">Loading instances…</p>
      </div>
    )
  }

  if (error) {
    const isAuthError = /session expired|invalid|log in again/i.test(error)
    return (
      <div className="instance-list">
        <h1 className="page-title">Workstations</h1>
        <p className="error-message" role="alert">{error}</p>
        {isAuthError && (
          <button type="button" className="btn btn-start" onClick={() => logout()}>
            Log in again
          </button>
        )}
      </div>
    )
  }

  return (
    <div className="instance-list">
      <h1 className="page-title">Workstations</h1>
      {refreshError && (
        <p className="refresh-error" role="status">{refreshError}</p>
      )}
      <div className="table-wrap">
        <table className="instances-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Status</th>
              <th>Auto-stop</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {instances.length === 0 ? (
              <tr>
                <td colSpan={4} className="empty">No workstations found.</td>
              </tr>
            ) : (
              instances.map((inst) => (
                <tr key={inst.instance_id}>
                  <td className="name">{inst.name || inst.instance_id}</td>
                  <td>
                    <span className="state-label" style={{ color: stateColor(inst.state) }}>
                      {inst.state}
                    </span>
                  </td>
                  <td className="shutdown">
                    {(() => {
                      const { absolute, relative } = formatShutdownLocal(inst.shutdown_at, inst.state)
                      return relative ? (
                        <span className="shutdown-cell">
                          <span className="shutdown-absolute">{absolute}</span>
                          <span className="shutdown-relative">{relative}</span>
                        </span>
                      ) : (
                        absolute
                      )
                    })()}
                  </td>
                  <td className="actions">
                    {inst.state === 'stopped' && (
                      <button
                        type="button"
                        className="btn btn-start"
                        disabled={acting !== null}
                        onClick={() => onStart(inst.name || inst.instance_id)}
                      >
                        {acting === (inst.name || inst.instance_id) ? '…' : 'Start'}
                      </button>
                    )}
                    {(inst.state === 'running' || inst.state === 'pending') && (
                      <button
                        type="button"
                        className="btn btn-stop"
                        disabled={acting !== null}
                        onClick={() => onStop(inst.name || inst.instance_id)}
                      >
                        {acting === (inst.name || inst.instance_id) ? '…' : 'Stop'}
                      </button>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
