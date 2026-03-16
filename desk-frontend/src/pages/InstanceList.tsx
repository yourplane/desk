import { useEffect, useRef, useState } from 'react'
import { listInstances, setAutoStop, startInstance, stopInstance, type Instance } from '../api/client'
import { logout } from '../auth'

const AUTO_STOP_PRESETS = [
  { label: '30m', value: '30m' },
  { label: '2h', value: '2h' },
  { label: '4h', value: '4h' },
  { label: '8h', value: '8h' },
] as const

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
  const [acting, setActing] = useState<string | null>(null)
  const [openAutoStopFor, setOpenAutoStopFor] = useState<string | null>(null)
  const autoStopMenuRef = useRef<HTMLDivElement>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const list = await listInstances()
      setInstances(list)
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      if (!msg.trim()) {
        setError('Unable to load workstations. Check the browser console or API logs.')
      } else {
        setError(msg)
      }
      console.error('InstanceList load failed:', e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
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

  const onSetAutoStop = async (name: string, duration: string) => {
    setActing(name)
    setError(null)
    setOpenAutoStopFor(null)
    try {
      await setAutoStop(name, { duration })
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setActing(null)
    }
  }

  const onClearAutoStop = async (name: string) => {
    setActing(name)
    setError(null)
    setOpenAutoStopFor(null)
    try {
      await setAutoStop(name, { clear: true })
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setActing(null)
    }
  }

  const onPlus2h = async (name: string) => {
    setActing(name)
    setError(null)
    try {
      await setAutoStop(name, { duration: '2h' })
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setActing(null)
    }
  }

  useEffect(() => {
    if (openAutoStopFor === null) return
    const handleClickOutside = (e: MouseEvent) => {
      if (autoStopMenuRef.current && !autoStopMenuRef.current.contains(e.target as Node)) {
        setOpenAutoStopFor(null)
      }
    }
    document.addEventListener('click', handleClickOutside)
    return () => document.removeEventListener('click', handleClickOutside)
  }, [openAutoStopFor])

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
                      const isRunningOrPending = inst.state === 'running' || inst.state === 'pending'
                      const nameOrId = inst.name || inst.instance_id
                      const menuOpen = openAutoStopFor === nameOrId
                      const busy = acting !== null
                      if (!isRunningOrPending) {
                        return relative ? (
                          <span className="shutdown-cell">
                            <span className="shutdown-absolute">{absolute}</span>
                            <span className="shutdown-relative">{relative}</span>
                          </span>
                        ) : (
                          absolute
                        )
                      }
                      return (
                        <div className="shutdown-cell shutdown-cell--editable" ref={menuOpen ? autoStopMenuRef : undefined}>
                          <button
                            type="button"
                            className="shutdown-clickable"
                            disabled={busy}
                            onClick={() => setOpenAutoStopFor((prev) => (prev === nameOrId ? null : nameOrId))}
                            title="Set auto-stop time"
                          >
                            {relative ? (
                              <>
                                <span className="shutdown-absolute">{absolute}</span>
                                <span className="shutdown-relative">{relative}</span>
                              </>
                            ) : (
                              absolute
                            )}
                          </button>
                          <button
                            type="button"
                            className="btn btn-plus2h"
                            disabled={busy}
                            onClick={() => onPlus2h(nameOrId)}
                            title="Set auto-stop to 2 hours from now"
                          >
                            +2h
                          </button>
                          {menuOpen && (
                            <div className="shutdown-menu" role="menu">
                              <div className="shutdown-menu-title">Set auto-stop</div>
                              {AUTO_STOP_PRESETS.map(({ label, value }) => (
                                <button
                                  key={value}
                                  type="button"
                                  role="menuitem"
                                  className="shutdown-menu-item"
                                  onClick={() => onSetAutoStop(nameOrId, value)}
                                >
                                  {label}
                                </button>
                              ))}
                              <button
                                type="button"
                                role="menuitem"
                                className="shutdown-menu-item shutdown-menu-item--clear"
                                onClick={() => onClearAutoStop(nameOrId)}
                              >
                                Clear auto-stop
                              </button>
                            </div>
                          )}
                        </div>
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
