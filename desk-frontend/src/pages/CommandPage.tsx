import { useCallback, useEffect, useRef, useState } from 'react'
import {
  getCommandStatus,
  listInstances,
  runCommand,
  type CommandStatus,
  type Instance,
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

export function CommandPage() {
  const [instances, setInstances] = useState<Instance[]>([])
  const [loadingInstances, setLoadingInstances] = useState(true)
  const [selectedWorkstation, setSelectedWorkstation] = useState('')
  const [script, setScript] = useState('')
  const [user, setUser] = useState('ubuntu')
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [history, setHistory] = useState<HistoryEntry[]>(loadHistory)

  const pollingRef = useRef<Map<string, number>>(new Map())
  const outputEndRef = useRef<HTMLDivElement>(null)
  const historyRef = useRef(history)
  historyRef.current = history

  const runningInstances = instances.filter(
    (i) => i.state === 'running' || i.state === 'pending',
  )

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

  // Resume polling for in-progress entries on mount
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

  const handleSubmit = async () => {
    if (!selectedWorkstation || !script.trim()) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      const result = await runCommand(selectedWorkstation, script, user || undefined)
      const entry: HistoryEntry = {
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        workstation: selectedWorkstation,
        script,
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
      setScript('')
      setTimeout(() => outputEndRef.current?.scrollIntoView({ behavior: 'smooth' }), 100)
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault()
      handleSubmit()
    }
  }

  const clearHistory = () => {
    setHistory([])
    saveHistory([])
  }

  return (
    <div className="command-page">
      <div className="page-header">
        <h1 className="page-title">Command</h1>
        {history.length > 0 && (
          <div className="page-header-actions">
            <button type="button" className="btn btn-secondary" onClick={clearHistory}>
              Clear history
            </button>
          </div>
        )}
      </div>

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
        <button
          type="button"
          className="btn btn-run"
          disabled={submitting || !script.trim() || runningInstances.length === 0}
          onClick={handleSubmit}
          title="Run (Ctrl+Enter)"
        >
          {submitting ? 'Sending…' : 'Run'}
        </button>
      </div>
      {submitError && <p className="command-error" role="alert">{submitError}</p>}

      <div className="command-history">
        {history.map((entry) => (
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
        <div ref={outputEndRef} />
      </div>
    </div>
  )
}
