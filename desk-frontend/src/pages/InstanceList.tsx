import { useEffect, useState } from 'react'
import { listInstances, startInstance, stopInstance, type Instance } from '../api/client'

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

  if (loading) return <p>Loading instances…</p>
  if (error) return <p style={{ color: 'crimson' }}>{error}</p>

  return (
    <div>
      <h1>Workstations</h1>
      <table>
        <thead>
          <tr>
            <th>Instance name</th>
            <th>Status</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {instances.length === 0 ? (
            <tr>
              <td colSpan={3}>No workstations found.</td>
            </tr>
          ) : (
            instances.map((inst) => (
              <tr key={inst.instance_id}>
                <td>{inst.name}</td>
                <td>{inst.state}</td>
                <td>
                  {inst.state === 'stopped' && (
                    <button
                      type="button"
                      disabled={acting !== null}
                      onClick={() => onStart(inst.name || inst.instance_id)}
                    >
                      {acting === (inst.name || inst.instance_id) ? '…' : 'Start'}
                    </button>
                  )}
                  {(inst.state === 'running' || inst.state === 'pending') && (
                    <button
                      type="button"
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
  )
}
