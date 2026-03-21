import { useCallback, useEffect, useRef, useState } from 'react'
import {
  createSavedCommand,
  deleteSavedCommand,
  getCommandStatus,
  listInstances,
  listSavedCommands,
  runCommand,
  type CommandStatus,
  type Instance,
  type SavedCommandItem,
  type SavedCommandParam,
} from '../api/client'

const TERMINAL_STATES = new Set(['Success', 'Failed', 'TimedOut', 'Cancelled', 'Cancelling'])
const POLL_MS = 1500
const HISTORY_KEY = 'desk-command-history'
const MAX_HISTORY = 50

interface HistoryEntry {
  id: string
  workstation: string
  script: string
  user: string
  submittedAt: string
  commandId: string
  instanceId: string
  status: string
  stdout: string
  stderr: string
  exitCode: number | null
}

function loadHistory(): HistoryEntry[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY)
    if (!raw) return []
    return JSON.parse(raw) as HistoryEntry[]
  } catch {
    return []
  }
}

function saveHistory(entries: HistoryEntry[]) {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(entries.slice(0, MAX_HISTORY)))
  } catch {
    // quota exceeded — silently drop
  }
}

function statusColor(status: string): string {
  switch (status) {
    case 'Success':
      return '#4ade80'
    case 'InProgress':
    case 'Pending':
      return '#facc15'
    case 'Failed':
    case 'TimedOut':
    case 'Cancelled':
    case 'Cancelling':
      return '#f87171'
    default:
      return '#94a3b8'
  }
}

function extractParamNames(script: string): string[] {
  const seen = new Set<string>()
  const result: string[] = []
  for (const m of script.matchAll(/\{\{(\w+)\}\}/g)) {
    if (!seen.has(m[1])) {
      seen.add(m[1])
      result.push(m[1])
    }
  }
  return result
}

function renderTemplate(template: string, params: Record<string, string>): string {
  let result = template
  for (const [key, value] of Object.entries(params)) {
    result = result.split(`{{${key}}}`).join(value)
  }
  return result
}

interface CommandPageProps {
  initialSection?: 'manage' | 'run'
}

export function CommandPage({
  initialSection = 'manage',
}: CommandPageProps) {
  const [instances, setInstances] = useState<Instance[]>([])
  const [loadingInstances, setLoadingInstances] = useState(true)
  const [selectedWorkstation, setSelectedWorkstation] = useState('')
  const [script, setScript] = useState('')
  const [user, setUser] = useState('ubuntu')
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [history, setHistory] = useState<HistoryEntry[]>(loadHistory)

  // Saved commands state
  const [savedCommands, setSavedCommands] = useState<SavedCommandItem[]>([])
  const [selectedSavedId, setSelectedSavedId] = useState('')
  const [paramValues, setParamValues] = useState<Record<string, string>>({})
  const [showSaveForm, setShowSaveForm] = useState(false)
  const [saveName, setSaveName] = useState('')
  const [saveDescription, setSaveDescription] = useState('')
  const [saveParamDefaults, setSaveParamDefaults] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [activeSection, setActiveSection] = useState<'manage' | 'run'>(initialSection)
  const [runComposerOpen, setRunComposerOpen] = useState(false)
  const [runInputType, setRunInputType] = useState<'custom' | 'saved'>('custom')

  const pollingRef = useRef<Map<string, number>>(new Map())
  const historyRef = useRef(history)
  historyRef.current = history

  const runningInstances = instances.filter(
    (i) => i.state === 'running' || i.state === 'pending',
  )

  const selectedSaved = savedCommands.find((c) => c.id === selectedSavedId) ?? null
  const currentParams = selectedSaved?.parameters ?? []
  const renderedSavedScript = selectedSaved ? renderTemplate(selectedSaved.script, paramValues) : ''
  const runnableScript = runInputType === 'saved' ? renderedSavedScript : script
  const visibleHistory = selectedWorkstation
    ? history.filter((entry) => entry.workstation === selectedWorkstation)
    : history

  // Load instances and saved commands on mount
  useEffect(() => {
    let cancelled = false
    setLoadingInstances(true)
    listInstances()
      .then((list) => {
        if (cancelled) return
        setInstances(list)
        const running = list.filter((i) => i.state === 'running' || i.state === 'pending')
        if (running.length > 0 && !selectedWorkstation) {
          setSelectedWorkstation(running[0].name && running[0].name !== '-' ? running[0].name : running[0].instance_id)
        }
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoadingInstances(false) })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    setActiveSection(initialSection)
  }, [initialSection])

  const fetchSavedCommands = useCallback(() => {
    listSavedCommands()
      .then(setSavedCommands)
      .catch(() => {})
  }, [])

  useEffect(() => { fetchSavedCommands() }, [fetchSavedCommands])

  const updateHistoryEntry = useCallback((id: string, patch: Partial<HistoryEntry>) => {
    setHistory((prev) => {
      const next = prev.map((e) => (e.id === id ? { ...e, ...patch } : e))
      saveHistory(next)
      return next
    })
  }, [])

  const pollCommand = useCallback(
    (entryId: string, workstation: string, commandId: string) => {
      const poll = async () => {
        try {
          const result: CommandStatus = await getCommandStatus(workstation, commandId)
          updateHistoryEntry(entryId, {
            status: result.status,
            stdout: result.stdout,
            stderr: result.stderr,
            exitCode: result.exit_code,
          })
          if (TERMINAL_STATES.has(result.status)) {
            const tid = pollingRef.current.get(entryId)
            if (tid !== undefined) window.clearInterval(tid)
            pollingRef.current.delete(entryId)
          }
        } catch {
          // keep polling on transient errors
        }
      }
      poll()
      const tid = window.setInterval(poll, POLL_MS)
      pollingRef.current.set(entryId, tid)
    },
    [updateHistoryEntry],
  )

  useEffect(() => {
    for (const entry of historyRef.current) {
      if (!TERMINAL_STATES.has(entry.status)) {
        pollCommand(entry.id, entry.workstation, entry.commandId)
      }
    }
    return () => {
      pollingRef.current.forEach((tid) => window.clearInterval(tid))
      pollingRef.current.clear()
    }
  }, [pollCommand])

  const handleSelectSaved = (id: string) => {
    setSelectedSavedId(id)
    if (!id) return
    const cmd = savedCommands.find((c) => c.id === id)
    if (!cmd) return
    if (activeSection === 'manage') setScript(cmd.script)
    const defaults: Record<string, string> = {}
    for (const p of cmd.parameters) {
      defaults[p.name] = p.default ?? ''
    }
    setParamValues(defaults)
  }

  const handleSubmit = async () => {
    if (!selectedWorkstation || !runnableScript.trim()) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      const finalScript = runnableScript
      const result = await runCommand(selectedWorkstation, finalScript, user || undefined)
      const entry: HistoryEntry = {
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        workstation: selectedWorkstation,
        script: finalScript,
        user: user || 'root',
        submittedAt: new Date().toISOString(),
        commandId: result.command_id,
        instanceId: result.instance_id,
        status: 'Pending',
        stdout: '',
        stderr: '',
        exitCode: null,
      }
      setHistory((prev) => {
        const next = [entry, ...prev].slice(0, MAX_HISTORY)
        saveHistory(next)
        return next
      })
      pollCommand(entry.id, entry.workstation, entry.commandId)
      if (runInputType === 'custom') setScript('')
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (!runComposerOpen || activeSection !== 'run' || runInputType !== 'custom') return
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault()
      handleSubmit()
    }
  }

  const clearHistory = () => {
    setHistory([])
    saveHistory([])
  }

  const handleDeleteSaved = async () => {
    if (!selectedSavedId) return
    if (!window.confirm(`Delete saved command "${selectedSaved?.name}"?`)) return
    try {
      await deleteSavedCommand(selectedSavedId)
      setSelectedSavedId('')
      setScript('')
      setParamValues({})
      fetchSavedCommands()
    } catch {
      // ignore
    }
  }

  const handleOpenSaveForm = () => {
    setSaveName('')
    setSaveDescription('')
    setSaveError(null)
    const params = extractParamNames(script)
    const defaults: Record<string, string> = {}
    for (const p of params) defaults[p] = ''
    setSaveParamDefaults(defaults)
    setShowSaveForm(true)
  }

  const handleSaveCommand = async () => {
    if (!saveName.trim() || !script.trim()) return
    setSaving(true)
    setSaveError(null)
    try {
      const detectedParams = extractParamNames(script)
      const params: SavedCommandParam[] = detectedParams.map((name) => ({
        name,
        ...(saveParamDefaults[name] ? { default: saveParamDefaults[name] } : {}),
      }))
      const created = await createSavedCommand({
        name: saveName.trim(),
        script,
        description: saveDescription.trim(),
        parameters: params,
      })
      setShowSaveForm(false)
      fetchSavedCommands()
      setSelectedSavedId(created.id)
      const defaults: Record<string, string> = {}
      for (const p of created.parameters) defaults[p.name] = p.default ?? ''
      setParamValues(defaults)
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  const detectedParamsForSave = extractParamNames(script)

  return (
    <div className="command-page">
      <div className="page-header">
        <h1 className="page-title">Command</h1>
        {visibleHistory.length > 0 && (
          <div className="page-header-actions">
            <button type="button" className="btn btn-secondary" onClick={clearHistory}>
              Clear history
            </button>
          </div>
        )}
      </div>

      <div className="command-subnav">
        <button
          type="button"
          className={`command-subnav-tab${activeSection === 'manage' ? ' command-subnav-tab--active' : ''}`}
          onClick={() => setActiveSection('manage')}
        >
          Saved Commands
        </button>
        <button
          type="button"
          className={`command-subnav-tab${activeSection === 'run' ? ' command-subnav-tab--active' : ''}`}
          onClick={() => setActiveSection('run')}
        >
          Run on Workstation
        </button>
      </div>

      {activeSection === 'manage' && (
        <section className="command-section command-section-edit">
          <div className="command-section-header">
            <h2 className="command-section-title">Manage Saved Commands</h2>
            <p className="command-section-description">
              Build and maintain reusable command templates.
            </p>
          </div>
          <div className="terminal-input-wrap">
            <div className="terminal-prompt">$</div>
            <textarea
              className="terminal-input"
              value={script}
              onChange={(e) => setScript(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Enter command or script… Use {{param}} for parameters"
              rows={3}
              disabled={submitting || runningInstances.length === 0}
              spellCheck={false}
              autoComplete="off"
              autoCorrect="off"
              autoCapitalize="off"
            />
          </div>
          <div className="manage-hint">
            Use <code>{'{{param}}'}</code> placeholders, then save this template.
          </div>
          <div className="saved-commands-section">
            <div className="saved-commands-row">
              <div className="command-control-group">
                <label className="command-label" htmlFor="cmd-saved">Saved Command</label>
                <select
                  id="cmd-saved"
                  className="command-select"
                  value={selectedSavedId}
                  onChange={(e) => handleSelectSaved(e.target.value)}
                  disabled={submitting}
                >
                  <option value="">— None —</option>
                  {savedCommands.map((c) => (
                    <option key={c.id} value={c.id}>{c.name}</option>
                  ))}
                </select>
              </div>
              <div className="saved-commands-actions">
                {selectedSavedId && (
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={handleDeleteSaved}
                    disabled={submitting}
                  >
                    Delete
                  </button>
                )}
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={handleOpenSaveForm}
                  disabled={submitting || !script.trim()}
                  title="Save current script as a reusable command"
                >
                  Save as…
                </button>
              </div>
            </div>
            {selectedSaved && currentParams.length > 0 && (
              <div className="saved-params">
                <span className="command-label">Parameters</span>
                <div className="saved-params-grid">
                  {currentParams.map((p) => (
                    <div key={p.name} className="saved-param-field">
                      <label className="saved-param-label" htmlFor={`param-${p.name}`}>
                        {p.name}
                      </label>
                      <input
                        id={`param-${p.name}`}
                        className="saved-param-input"
                        type="text"
                        value={paramValues[p.name] ?? ''}
                        onChange={(e) =>
                          setParamValues((prev) => ({ ...prev, [p.name]: e.target.value }))
                        }
                        placeholder={p.default ?? ''}
                        disabled={submitting}
                      />
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
          {showSaveForm && (
            <div className="save-form">
              <div className="save-form-header">
                <span className="command-label">Save Command</span>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => setShowSaveForm(false)}
                >
                  Cancel
                </button>
              </div>
              <div className="save-form-fields">
                <input
                  className="save-form-input"
                  type="text"
                  value={saveName}
                  onChange={(e) => setSaveName(e.target.value)}
                  placeholder="Command name"
                  disabled={saving}
                />
                <input
                  className="save-form-input"
                  type="text"
                  value={saveDescription}
                  onChange={(e) => setSaveDescription(e.target.value)}
                  placeholder="Description (optional)"
                  disabled={saving}
                />
              </div>
              {detectedParamsForSave.length > 0 && (
                <div className="save-form-params">
                  <span className="save-form-params-label">
                    Detected parameters ({`{{name}}`} syntax):
                  </span>
                  <div className="saved-params-grid">
                    {detectedParamsForSave.map((name) => (
                      <div key={name} className="saved-param-field">
                        <label className="saved-param-label">{name}</label>
                        <input
                          className="saved-param-input"
                          type="text"
                          value={saveParamDefaults[name] ?? ''}
                          onChange={(e) =>
                            setSaveParamDefaults((prev) => ({ ...prev, [name]: e.target.value }))
                          }
                          placeholder="Default value (optional)"
                          disabled={saving}
                        />
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {saveError && <p className="command-error" role="alert">{saveError}</p>}
              <button
                type="button"
                className="btn btn-start btn-sm"
                onClick={handleSaveCommand}
                disabled={saving || !saveName.trim() || !script.trim()}
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
            </div>
          )}
        </section>
      )}

      {activeSection === 'run' && (
        <>
          <div className="command-controls">
            <div className="command-control-group">
              <label className="command-label" htmlFor="cmd-workstation">Workstation</label>
              {loadingInstances ? (
                <span className="command-hint">Loading…</span>
              ) : runningInstances.length === 0 ? (
                <span className="command-hint">No running workstations</span>
              ) : (
                <select
                  id="cmd-workstation"
                  className="command-select"
                  value={selectedWorkstation}
                  onChange={(e) => setSelectedWorkstation(e.target.value)}
                  disabled={submitting}
                >
                  {runningInstances.map((inst) => {
                    const label = inst.name && inst.name !== '-' ? inst.name : inst.instance_id
                    return (
                      <option key={inst.instance_id} value={label}>
                        {label}
                      </option>
                    )
                  })}
                </select>
              )}
            </div>
            <div className="command-control-group">
              <label className="command-label" htmlFor="cmd-user">User</label>
              <input
                id="cmd-user"
                className="command-user-input"
                type="text"
                value={user}
                onChange={(e) => setUser(e.target.value)}
                placeholder="root"
                disabled={submitting}
              />
            </div>
          </div>
          <section className="command-section command-section-run">
            <div className="command-section-header">
              <h2 className="command-section-title">Run Command</h2>
              <p className="command-section-description">
                Execute the current command on the selected workstation.
              </p>
            </div>
            {!runComposerOpen ? (
              <button
                type="button"
                className="btn btn-start"
                onClick={() => setRunComposerOpen(true)}
                disabled={runningInstances.length === 0}
              >
                Run command
              </button>
            ) : (
              <>
                <div className="run-mode-controls">
                  <label className="command-label" htmlFor="run-input-type">Command type</label>
                  <select
                    id="run-input-type"
                    className="command-select"
                    value={runInputType}
                    onChange={(e) => setRunInputType(e.target.value as 'custom' | 'saved')}
                    disabled={submitting}
                  >
                    <option value="custom">Custom command</option>
                    <option value="saved">Saved command</option>
                  </select>
                </div>
                {runInputType === 'custom' ? (
                  <div className="terminal-input-wrap">
                    <div className="terminal-prompt">$</div>
                    <textarea
                      className="terminal-input"
                      value={script}
                      onChange={(e) => setScript(e.target.value)}
                      onKeyDown={handleKeyDown}
                      placeholder="Enter command or script…"
                      rows={3}
                      disabled={submitting || runningInstances.length === 0}
                      spellCheck={false}
                      autoComplete="off"
                      autoCorrect="off"
                      autoCapitalize="off"
                    />
                  </div>
                ) : (
                  <div className="saved-commands-section">
                    <div className="command-control-group">
                      <label className="command-label" htmlFor="run-saved">Saved Command</label>
                      <select
                        id="run-saved"
                        className="command-select"
                        value={selectedSavedId}
                        onChange={(e) => handleSelectSaved(e.target.value)}
                        disabled={submitting}
                      >
                        <option value="">— Select saved command —</option>
                        {savedCommands.map((c) => (
                          <option key={c.id} value={c.id}>{c.name}</option>
                        ))}
                      </select>
                    </div>
                    {selectedSaved && currentParams.length > 0 && (
                      <div className="saved-params">
                        <span className="command-label">Parameters</span>
                        <div className="saved-params-grid">
                          {currentParams.map((p) => (
                            <div key={p.name} className="saved-param-field">
                              <label className="saved-param-label" htmlFor={`run-param-${p.name}`}>
                                {p.name}
                              </label>
                              <input
                                id={`run-param-${p.name}`}
                                className="saved-param-input"
                                type="text"
                                value={paramValues[p.name] ?? ''}
                                onChange={(e) =>
                                  setParamValues((prev) => ({ ...prev, [p.name]: e.target.value }))
                                }
                                placeholder={p.default ?? ''}
                                disabled={submitting}
                              />
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                    <div className="readonly-command-wrap">
                      <span className="command-label">Command preview</span>
                      <pre className="readonly-command-content">{selectedSaved ? runnableScript : ''}</pre>
                    </div>
                  </div>
                )}
                <div className="run-command-actions">
                  <button
                    type="button"
                    className="btn btn-run"
                    disabled={
                      submitting ||
                      !selectedWorkstation ||
                      !runnableScript.trim() ||
                      runningInstances.length === 0 ||
                      (runInputType === 'saved' && !selectedSavedId)
                    }
                    onClick={handleSubmit}
                    title="Run (Ctrl+Enter)"
                  >
                    {submitting ? 'Sending…' : 'Run'}
                  </button>
                </div>
              </>
            )}
            {submitError && <p className="command-error" role="alert">{submitError}</p>}
          </section>
          <div className="command-history">
            {visibleHistory.map((entry) => (
              <div key={entry.id} className="terminal-output-block">
                <div className="terminal-output-header">
                  <span className="terminal-output-ws">{entry.workstation}</span>
                  <span className="terminal-output-user">{entry.user}@</span>
                  <span
                    className="terminal-output-status"
                    style={{ color: statusColor(entry.status) }}
                  >
                    {entry.status}
                    {entry.exitCode !== null && ` (exit ${entry.exitCode})`}
                  </span>
                  <span className="terminal-output-time">
                    {new Date(entry.submittedAt).toLocaleTimeString()}
                  </span>
                </div>
                <div className="terminal-output-script">$ {entry.script}</div>
                <pre className="terminal-output-content">
                  {entry.stdout}
                  {entry.stderr && (
                    <span className="terminal-stderr">{entry.stderr}</span>
                  )}
                  {!TERMINAL_STATES.has(entry.status) && (
                    <span className="terminal-cursor">▌</span>
                  )}
                </pre>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
