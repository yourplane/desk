import { useEffect, useMemo, useState } from 'react'
import {
  cancelWorkflowRun,
  cloneWorkflowVersion,
  createWorkflow,
  listWorkflowRuns,
  listWorkflows,
  startWorkflowRun,
  type WorkflowRunItem,
  type WorkflowStep,
} from '../api/client'

const POLL_MS = 3000
const TERMINAL = new Set(['SUCCEEDED', 'FAILED', 'CANCELED'])

export function WorkflowPage() {
  const [workflows, setWorkflows] = useState<any[]>([])
  const [runs, setRuns] = useState<WorkflowRunItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [stepsText, setStepsText] = useState('start_workstation main')
  const [creating, setCreating] = useState(false)

  const workflowById = useMemo(() => new Map(workflows.map((w) => [w.id, w])), [workflows])
  const hasActiveRuns = runs.some((run) => !TERMINAL.has(run.status))

  const refresh = async () => {
    const [workflowList, runList] = await Promise.all([listWorkflows(), listWorkflowRuns()])
    setWorkflows(workflowList)
    setRuns(runList)
  }

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    refresh()
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!hasActiveRuns) return
    const timer = window.setInterval(() => {
      refresh().catch(() => {})
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [hasActiveRuns])

  const parseSteps = (): WorkflowStep[] => {
    const lines = stepsText
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
    if (lines.length === 0) {
      throw new Error('At least one step is required.')
    }
    return lines.map((line, idx) => {
      const [action, target, ...rest] = line.split(' ')
      if (!action || !target) {
        throw new Error(`Step ${idx + 1} must be "<action> <target> [script...]"`)
      }
      if (action === 'run_command') {
        const script = rest.join(' ').trim()
        if (!script) throw new Error(`Step ${idx + 1} run_command requires a script`)
        return { action, target, script }
      }
      return { action, target }
    })
  }

  const onCreate = async () => {
    if (!name.trim()) return
    setCreating(true)
    setError(null)
    try {
      const steps = parseSteps()
      await createWorkflow({ name: name.trim(), description: description.trim(), steps })
      setName('')
      setDescription('')
      setStepsText('start_workstation main')
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setCreating(false)
    }
  }

  const onStartRun = async (workflowId: string) => {
    setError(null)
    try {
      await startWorkflowRun(workflowId)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const onClone = async (workflowId: string, version: number) => {
    setError(null)
    try {
      await cloneWorkflowVersion(workflowId, version)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const onCancel = async (runId: string) => {
    setError(null)
    try {
      await cancelWorkflowRun(runId)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="command-page">
      <h1 className="page-title">Workflow</h1>
      {error && <p className="command-error">{error}</p>}
      <div className="save-form">
        <span className="command-label">Create Workflow</span>
        <div className="save-form-fields">
          <input
            className="save-form-input"
            placeholder="Workflow name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <input
            className="save-form-input"
            placeholder="Description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        <textarea
          className="terminal-input"
          rows={4}
          value={stepsText}
          onChange={(e) => setStepsText(e.target.value)}
        />
        <p className="command-hint">One step per line: action target [script].</p>
        <button className="btn btn-start btn-sm" type="button" onClick={onCreate} disabled={creating}>
          {creating ? 'Creating…' : 'Create Workflow'}
        </button>
      </div>

      {loading ? (
        <p className="loading">Loading workflows…</p>
      ) : (
        <>
          <div className="table-wrap">
            <table className="instances-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Status</th>
                  <th>Latest Version</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {workflows.map((workflow) => {
                  const latestVersion = Math.max(...workflow.versions.map((v: any) => v.version))
                  return (
                    <tr key={workflow.id}>
                      <td>{workflow.name}</td>
                      <td>{workflow.status}</td>
                      <td>v{latestVersion}</td>
                      <td className="actions">
                        <button className="btn btn-start btn-sm" type="button" onClick={() => onStartRun(workflow.id)}>
                          Run now
                        </button>
                        <button
                          className="btn btn-secondary btn-sm"
                          type="button"
                          onClick={() => onClone(workflow.id, latestVersion)}
                        >
                          Clone version
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          <div className="command-history" style={{ marginTop: '1rem' }}>
            {runs.map((run) => {
              const wf = workflowById.get(run.workflow_id)
              return (
                <div key={run.id} className="terminal-output-block">
                  <div className="terminal-output-header">
                    <span className="terminal-output-ws">{wf?.name || run.workflow_id}</span>
                    <span className="terminal-output-user">v{run.workflow_version}</span>
                    <span className="terminal-output-status">{run.status}</span>
                    {!TERMINAL.has(run.status) && (
                      <button className="btn btn-stop btn-sm" type="button" onClick={() => onCancel(run.id)}>
                        Cancel
                      </button>
                    )}
                  </div>
                  <pre className="terminal-output-content">
                    {run.step_results.length === 0 ? 'No steps started yet.' : ''}
                    {run.step_results.map((step) => (
                      <div key={`${run.id}-${step.index}`}>
                        #{step.index} {step.action} {step.target} — {step.status}
                        {step.error ? ` (${step.error})` : ''}
                      </div>
                    ))}
                    {run.error && <div>Error: {run.error}</div>}
                  </pre>
                </div>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}

