import { useState } from 'react'
import { reapWorkstations, type ReapResult } from '../api/client'

export function ReaperPage() {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<ReapResult | null>(null)

  const onReap = async () => {
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const data = await reapWorkstations()
      setResult(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="instance-list">
      <div className="page-header">
        <h1 className="page-title">Reaper</h1>
      </div>
      <p className="reaper-description">
        Stop all workstations that are past their auto-stop time.
      </p>
      <button
        type="button"
        className="btn btn-reap"
        disabled={loading}
        onClick={onReap}
      >
        {loading ? 'Reaping…' : 'Reap now'}
      </button>

      {error && (
        <p className="error-message" role="alert">{error}</p>
      )}

      {result && (
        <div className="reaper-results">
          {result.stopped.length === 0 ? (
            <p className="reaper-empty">No overdue workstations found.</p>
          ) : (
            <>
              <p className="reaper-summary">
                Stopped {result.stopped.length} workstation{result.stopped.length !== 1 ? 's' : ''}:
              </p>
              <div className="table-wrap">
                <table className="instances-table">
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Instance ID</th>
                      <th>Was due at</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.stopped.map((w) => (
                      <tr key={w.instance_id}>
                        <td className="name">{w.name && w.name !== '-' ? w.name : w.instance_id}</td>
                        <td className="instance-id-cell">{w.instance_id}</td>
                        <td className="shutdown">{w.shutdown_at ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
