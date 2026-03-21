import { useEffect, useMemo, useState } from 'react'
import {
  cancelWorkflowRun,
  getWorkflowRunStatus,
  listInstances,
  listSavedCommands,
  listWorkflowMethods,
  startWorkflowRun,
  type Instance,
  type SavedCommandItem,
  type WorkflowRunStatus,
  type WorkflowStepInput,
} from '../api/client'

const POLL_MS = 2000

type StepMode = 'custom' | 'saved'

interface StepDraft {
  id: string
  workstation: string
  user: string
  timeout: number
  poll_interval_seconds: number
  mode: StepMode
  script: string
  saved_command_id: string
  params: Record<string, string>
}

function uid() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function renderTemplate(template: string, params: Record<string, string>): string {
  let result = template
  for (const [key, value] of Object.entries(params)) {
    result = result.split(`{{${key}}}`).join(value)
  }
  return result
}

export function WorkflowPage() {
  const [instances, setInstances] = useState<Instance[]>([])
  const [savedCommands, setSavedCommands] = useState<SavedCommandItem[]>([])
  const [methodsCount, setMethodsCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [runError, setRunError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [run, setRun] = useState<WorkflowRunStatus | null>(null)
  const [steps, setSteps] = useState<StepDraft[]>([])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    Promise.all([listInstances(), listSavedCommands(), listWorkflowMethods()])
      .then(([insts, saved, methods]) => {
        if (cancelled) return
        setInstances(insts)
        setSavedCommands(saved)
        setMethodsCount(methods.length)
        const firstWorkstation = insts[0]?.name && insts[0].name !== '-' ? insts[0].name : (insts[0]?.instance_id ?? '')
        setSteps([{
          id: uid(),
          workstation: firstWorkstation,
          user: 'ubuntu',
          timeout: 3600,
          poll_interval_seconds: 2,
          mode: 'custom',
          script: '',
          saved_command_id: '',
          params: {},
        }])
      })
      .catch((e) => {
        if (cancelled) return
        setError(e instanceof Error ? e.message : String(e))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!run || run.is_terminal) return
    const timer = window.setInterval(async () => {
      try {
        const next = await getWorkflowRunStatus(run.execution_arn)
        setRun(next)
      } catch {
        // keep polling on transient errors
      }
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [run])

  const runningOptions = useMemo(
    () =>
      instances
        .filter((i) => i.state === 'running' || i.state === 'pending')
        .map((i) => ({
          value: i.name && i.name !== '-' ? i.name : i.instance_id,
          label: i.name && i.name !== '-' ? `${i.name} (${i.instance_id})` : i.instance_id,
        })),
    [instances],
  )

  const updateStep = (id: string, patch: Partial<StepDraft>) => {
    setSteps((prev) => prev.map((s) => (s.id === id ? { ...s, ...patch } : s)))
  }

  const addStep = () => {
    setSteps((prev) => [
      ...prev,
      {
        id: uid(),
        workstation: runningOptions[0]?.value ?? '',
        user: 'ubuntu',
        timeout: 3600,
        poll_interval_seconds: 2,
        mode: 'custom',
        script: '',
        saved_command_id: '',
        params: {},
      },
    ])
  }

  const removeStep = (id: string) => {
    setSteps((prev) => prev.filter((s) => s.id !== id))
  }

  const buildStepInput = (step: StepDraft): WorkflowStepInput => {
    let script = step.script
    if (step.mode === 'saved') {
      const saved = savedCommands.find((c) => c.id === step.saved_command_id)
      if (!saved) throw new Error('Choose a saved command for every saved step.')
      script = renderTemplate(saved.script, step.params)
    }
    if (!step.workstation.trim()) throw new Error('Workstation is required for each step.')
    if (!script.trim()) throw new Error('Script is required for each step.')
    return {
      method_id: 'workstations.run_command',
      workstation: step.workstation.trim(),
      script: script.trim(),
      user: step.user.trim() || undefined,
      timeout: step.timeout,
      poll_interval_seconds: step.poll_interval_seconds,
    }
  }

  const onStart = async () => {
    setRunError(null)
    if (steps.length === 0) {
      setRunError('Add at least one step.')
      return
    }
    setStarting(true)
    try {
      const payload = steps.map(buildStepInput)
      const started = await startWorkflowRun(payload, 'command-workflow')
      const status = await getWorkflowRunStatus(started.execution_arn)
      setRun(status)
    } catch (e) {
      setRunError(e instanceof Error ? e.message : String(e))
    } finally {
      setStarting(false)
    }
  }

  const onCancel = async () => {
    if (!run || run.is_terminal) return
    try {
      await cancelWorkflowRun(run.execution_arn)
      const status = await getWorkflowRunStatus(run.execution_arn)
      setRun(status)
    } catch (e) {
      setRunError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="command-page">
      <div className="page-header">
        <h1 className="page-title">Workflow</h1>
      </div>
      <p className="command-section-description">
        Build an arbitrary sequence of command steps. Steps run sequentially in Step Functions.
      </p>
      <p className="command-hint">Available workflow methods: {methodsCount}</p>
      {loading && <p className="loading">Loading workflow data…</p>}
      {error && <p className="error-message">{error}</p>}
      {!loading && !error && (
        <>
          <div className="command-history">
            {steps.map((step, index) => {
              const selectedSaved = savedCommands.find((c) => c.id === step.saved_command_id)
              const savedParams = selectedSaved?.parameters ?? []
              return (
                <section key={step.id} className="command-section command-section-run">
                  <div className="page-header">
                    <h2 className="command-section-title">Step {index + 1}</h2>
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() => removeStep(step.id)}
                      disabled={steps.length <= 1 || starting}
                    >
                      Remove
                    </button>
                  </div>
                  <div className="command-controls">
                    <div className="command-control-group">
                      <label className="command-label">Workstation</label>
                      <select
                        className="command-select"
                        value={step.workstation}
                        onChange={(e) => updateStep(step.id, { workstation: e.target.value })}
                        disabled={starting}
                      >
                        <option value="">— Select —</option>
                        {runningOptions.map((opt) => (
                          <option key={opt.value} value={opt.value}>{opt.label}</option>
                        ))}
                      </select>
                    </div>
                    <div className="command-control-group">
                      <label className="command-label">User</label>
                      <input
                        className="command-user-input"
                        value={step.user}
                        onChange={(e) => updateStep(step.id, { user: e.target.value })}
                        disabled={starting}
                      />
                    </div>
                  </div>
                  <div className="run-mode-controls">
                    <label className="command-label">Command type</label>
                    <select
                      className="command-select"
                      value={step.mode}
                      onChange={(e) =>
                        updateStep(step.id, {
                          mode: e.target.value as StepMode,
                          saved_command_id: '',
                          params: {},
                        })
                      }
                    >
                      <option value="custom">Custom command</option>
                      <option value="saved">Saved command</option>
                    </select>
                  </div>
                  {step.mode === 'custom' ? (
                    <div className="terminal-input-wrap">
                      <div className="terminal-prompt">$</div>
                      <textarea
                        className="terminal-input"
                        value={step.script}
                        onChange={(e) => updateStep(step.id, { script: e.target.value })}
                        rows={3}
                        disabled={starting}
                      />
                    </div>
                  ) : (
                    <div className="saved-commands-section">
                      <div className="command-control-group">
                        <label className="command-label">Saved Command</label>
                        <select
                          className="command-select"
                          value={step.saved_command_id}
                          onChange={(e) => {
                            const id = e.target.value
                            const cmd = savedCommands.find((c) => c.id === id)
                            const params: Record<string, string> = {}
                            for (const p of cmd?.parameters ?? []) params[p.name] = p.default ?? ''
                            updateStep(step.id, { saved_command_id: id, params })
                          }}
                        >
                          <option value="">— Select —</option>
                          {savedCommands.map((cmd) => (
                            <option key={cmd.id} value={cmd.id}>{cmd.name}</option>
                          ))}
                        </select>
                      </div>
                      {savedParams.length > 0 && (
                        <div className="saved-params-grid">
                          {savedParams.map((p) => (
                            <input
                              key={p.name}
                              className="saved-param-input"
                              value={step.params[p.name] ?? ''}
                              onChange={(e) =>
                                updateStep(step.id, {
                                  params: { ...step.params, [p.name]: e.target.value },
                                })
                              }
                              placeholder={p.default ?? p.name}
                              disabled={starting}
                            />
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </section>
              )
            })}
          </div>
          <div className="page-header-actions">
            <button type="button" className="btn btn-secondary" onClick={addStep} disabled={starting}>
              Add step
            </button>
            <button type="button" className="btn btn-start" onClick={onStart} disabled={starting}>
              {starting ? 'Starting…' : 'Start workflow'}
            </button>
            <button
              type="button"
              className="btn btn-stop"
              onClick={onCancel}
              disabled={!run || run.is_terminal}
            >
              Cancel run
            </button>
          </div>
          {runError && <p className="command-error">{runError}</p>}
          {run && (
            <div className="terminal-output-block">
              <div className="terminal-output-header">
                <span className="terminal-output-ws">Run</span>
                <span className="terminal-output-status">{run.status}</span>
              </div>
              <pre className="terminal-output-content">{JSON.stringify(run.output ?? run.input, null, 2)}</pre>
            </div>
          )}
        </>
      )}
    </div>
  )
}
