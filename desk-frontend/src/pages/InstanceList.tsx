import { useEffect, useState } from 'react'
import { listInstances, startInstance, stopInstance, type Instance } from '../api/client'

function formatShutdownLocal(isoUtc: string | null, state: string): string {
  if (!isoUtc || state === 'stopped' || state === 'stopping' || state === 'terminated' || state === 'shutting-down') return '—'
  try {
    const d = new Date(isoUtc)
    if (Number.isNaN(d.getTime())) return isoUtc
    return d.toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
  } catch {
    return isoUtc
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

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const list = await listInstances()
      setInstances(list)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
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

  if (loading) {
    return (
      <div className="instance-list">
        <h1 className="page-title">Workstations</h1>
        <p className="loading">Loading instances…</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="instance-list">
        <h1 className="page-title">Workstations</h1>
        <p className="error-message">{error}</p>
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
              <th>Auto-stop (local)</th>
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
                    <span className="state-badge" style={{ backgroundColor: stateColor(inst.state) }}>
                      {inst.state}
                    </span>
                  </td>
                  <td className="shutdown">{formatShutdownLocal(inst.shutdown_at, inst.state)}</td>
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
