import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import {
  createWorkstation,
  listInstances,
  setAutoStop,
  startInstance,
  stopInstance,
  restartInstance,
  killInstance,
  type Instance,
} from '../api/client'
import { DataFreshnessBar } from '../DataFreshnessBar'
import { useAdaptiveRefetchInterval } from '../hooks/useAdaptiveRefetchInterval'
import { queryKeys } from '../queryKeys'
import { logout } from '../auth'
import { instanceKey, stateColor } from './workstationUtils'

const POLL_INTERVAL_MS = 10_000
const BACKGROUND_POLL_INTERVAL_MS = 5 * 60 * 1000

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

function buildDurationFromTotalMinutes(totalMinutes: number): string {
  const clamped = Math.max(1, Math.floor(totalMinutes))
  const hours = Math.floor(clamped / 60)
  const minutes = clamped % 60
  if (hours > 0 && minutes > 0) return `${hours}h${minutes}m`
  if (hours > 0) return `${hours}h`
  return `${minutes}m`
}

function toDatetimeLocalValue(isoUtc: string | null): string {
  const d = isoUtc ? new Date(isoUtc) : null
  const base = d && !Number.isNaN(d.getTime()) ? d : new Date(Date.now() + 2 * 3600_000)
  const y = base.getFullYear()
  const mo = String(base.getMonth() + 1).padStart(2, '0')
  const day = String(base.getDate()).padStart(2, '0')
  const h = String(base.getHours()).padStart(2, '0')
  const mi = String(base.getMinutes()).padStart(2, '0')
  return `${y}-${mo}-${day}T${h}:${mi}`
}

export function InstanceList() {
  const queryClient = useQueryClient()
  const pollIntervalMs = useAdaptiveRefetchInterval(POLL_INTERVAL_MS, BACKGROUND_POLL_INTERVAL_MS)
  const [acting, setActing] = useState<string | null>(null)
  const [openAutoStopFor, setOpenAutoStopFor] = useState<string | null>(null)
  const [customTime, setCustomTime] = useState('')
  const [showCreateForm, setShowCreateForm] = useState(false)
  const [createName, setCreateName] = useState('')
  const [createInstanceType, setCreateInstanceType] = useState('t3.medium')
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const autoStopMenuRef = useRef<HTMLDivElement>(null)
  const actingRef = useRef<string | null>(null)
  actingRef.current = acting
  const [listInfra, setListInfra] = useState(false)

  const instancesQuery = useQuery({
    queryKey: queryKeys.workstations(listInfra),
    queryFn: () => listInstances({ infra: listInfra }),
    placeholderData: keepPreviousData,
    staleTime: 5_000,
    refetchInterval: () => (actingRef.current !== null ? false : pollIntervalMs),
  })

  const instances: Instance[] = instancesQuery.data ?? []
  const blockingError =
    instancesQuery.isError && instancesQuery.data === undefined
      ? instancesQuery.error instanceof Error
        ? instancesQuery.error.message
        : String(instancesQuery.error)
      : null
  const fallbackMsg = 'Unable to load workstations. Check the browser console or API logs.'
  const error = blockingError && !blockingError.trim() ? fallbackMsg : blockingError
  const refreshError =
    instancesQuery.isError && instancesQuery.data !== undefined
      ? 'Could not refresh. Will retry.'
      : null

  const [actionError, setActionError] = useState<string | null>(null)

  const refetchWorkstations = () => instancesQuery.refetch()

  const onStart = async (name: string) => {
    setActing(name)
    setActionError(null)
    try {
      await startInstance(name, { infra: listInfra })
      await queryClient.invalidateQueries({ queryKey: ['workstations'] })
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setActing(null)
    }
  }

  const onStop = async (name: string) => {
    setActing(name)
    setActionError(null)
    try {
      await stopInstance(name, { infra: listInfra })
      await queryClient.invalidateQueries({ queryKey: ['workstations'] })
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setActing(null)
    }
  }

  const onRestart = async (name: string) => {
    setActing(name)
    setActionError(null)
    try {
      await restartInstance(name, { infra: listInfra })
      await queryClient.invalidateQueries({ queryKey: ['workstations'] })
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setActing(null)
    }
  }

  const onSetAutoStop = async (name: string, duration: string) => {
    setActing(name)
    setActionError(null)
    setOpenAutoStopFor(null)
    try {
      await setAutoStop(name, { duration })
      await queryClient.invalidateQueries({ queryKey: ['workstations'] })
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setActing(null)
    }
  }

  const onKill = async (name: string) => {
    if (!window.confirm('Terminate this workstation? This cannot be undone.')) return
    setActing(name)
    setActionError(null)
    try {
      await killInstance(name, { infra: listInfra })
      await queryClient.invalidateQueries({ queryKey: ['workstations'] })
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setActing(null)
    }
  }

  const onClearAutoStop = async (name: string) => {
    setActing(name)
    setActionError(null)
    setOpenAutoStopFor(null)
    try {
      await setAutoStop(name, { clear: true })
      await queryClient.invalidateQueries({ queryKey: ['workstations'] })
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setActing(null)
    }
  }

  const onSetAutoStopAt = async (name: string, localDatetime: string) => {
    setActing(name)
    setActionError(null)
    setOpenAutoStopFor(null)
    try {
      const utcIso = new Date(localDatetime).toISOString()
      await setAutoStop(name, { shutdown_at: utcIso })
      await queryClient.invalidateQueries({ queryKey: ['workstations'] })
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setActing(null)
    }
  }

  const onPlus2h = async (name: string, shutdownAt: string | null) => {
    setActing(name)
    setActionError(null)
    setOpenAutoStopFor(null)
    try {
      let totalMinutes = 120
      if (shutdownAt) {
        const shutdownMs = new Date(shutdownAt).getTime()
        if (!Number.isNaN(shutdownMs)) {
          const remainingMinutes = Math.max(0, Math.ceil((shutdownMs - Date.now()) / 60000))
          totalMinutes = remainingMinutes + 120
        }
      }
      await setAutoStop(name, { duration: buildDurationFromTotalMinutes(totalMinutes) })
      await queryClient.invalidateQueries({ queryKey: ['workstations'] })
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setActing(null)
    }
  }

  const onCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmed = createName.trim()
    if (!trimmed) return
    setCreating(true)
    setCreateError(null)
    try {
      await createWorkstation(trimmed, createInstanceType || undefined)
      setShowCreateForm(false)
      setCreateName('')
      setCreateInstanceType('t3.medium')
      await queryClient.invalidateQueries({ queryKey: ['workstations'] })
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : String(err))
    } finally {
      setCreating(false)
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

  const createSection = (
    <div className="create-section">
      {showCreateForm ? (
        <form className="create-form" onSubmit={onCreate}>
          <div className="create-form-fields">
            <input
              className="create-input"
              type="text"
              placeholder="Workstation name"
              value={createName}
              onChange={(e) => setCreateName(e.target.value)}
              required
              autoFocus
              disabled={creating}
            />
            <input
              className="create-input create-input--narrow"
              type="text"
              placeholder="Instance type"
              value={createInstanceType}
              onChange={(e) => setCreateInstanceType(e.target.value)}
              disabled={creating}
            />
            <button type="submit" className="btn btn-start" disabled={creating || !createName.trim()}>
              {creating ? 'Creating…' : 'Launch'}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => { setShowCreateForm(false); setCreateError(null) }}
              disabled={creating}
            >
              Cancel
            </button>
          </div>
          {createError && <p className="create-error" role="alert">{createError}</p>}
        </form>
      ) : (
        <button
          type="button"
          className="btn btn-start"
          onClick={() => { setShowCreateForm(true); setCreateError(null) }}
        >
          Create
        </button>
      )}
    </div>
  )

  if (instancesQuery.isPending && instancesQuery.data === undefined) {
    return <p className="loading">Loading instances…</p>
  }

  if (error) {
    const isAuthError = /session expired|invalid|log in again/i.test(error)
    return (
      <>
        <p className="error-message" role="alert">{error}</p>
        {isAuthError && (
          <button type="button" className="btn btn-start" onClick={() => logout()}>
            Log in again
          </button>
        )}
        {createSection}
      </>
    )
  }

  return (
    <>
      <DataFreshnessBar
        resourceLabel="Workstation list"
        dataUpdatedAt={instancesQuery.dataUpdatedAt}
        isFetching={instancesQuery.isFetching}
        onRefresh={() => void refetchWorkstations()}
      />
      {refreshError && (
        <p className="refresh-error" role="status">{refreshError}</p>
      )}
      {actionError && (
        <p className="error-message" role="alert">{actionError}</p>
      )}
      <p className="instance-list-toolbar">
        <label className="instance-list-infra-toggle">
          <input
            type="checkbox"
            checked={listInfra}
            onChange={(e) => setListInfra(e.target.checked)}
          />
          {' '}
          List managed router (infra)
        </label>
      </p>
      <div
        className={`table-wrap${instancesQuery.isFetching && instances.length > 0 ? ' table-wrap--revalidating' : ''}`}
      >
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
                <td colSpan={4} className="empty">
                  {listInfra ? 'No router instances found.' : 'No workstations found.'}
                </td>
              </tr>
            ) : (
              instances.map((inst) => {
                const key = instanceKey(inst)
                return (
                <tr key={inst.instance_id}>
                  <td className="name">{key}</td>
                  <td>
                    <span className="state-label" style={{ color: stateColor(inst.state) }}>
                      {inst.state}
                    </span>
                  </td>
                  <td className="shutdown">
                    {listInfra ? (
                      '—'
                    ) : (() => {
                      const { absolute, relative } = formatShutdownLocal(inst.shutdown_at, inst.state)
                      const isRunningOrPending = inst.state === 'running' || inst.state === 'pending'
                      const menuOpen = openAutoStopFor === key
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
                            onClick={() => {
                              setOpenAutoStopFor((prev) => {
                                if (prev === key) return null
                                setCustomTime(toDatetimeLocalValue(inst.shutdown_at))
                                return key
                              })
                            }}
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
                            onClick={() => onPlus2h(key, inst.shutdown_at)}
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
                                  onClick={() => onSetAutoStop(key, value)}
                                >
                                  {label}
                                </button>
                              ))}
                              <div className="shutdown-menu-custom">
                                <input
                                  type="datetime-local"
                                  className="shutdown-menu-datetime"
                                  value={customTime}
                                  onChange={(e) => setCustomTime(e.target.value)}
                                />
                                <button
                                  type="button"
                                  className="btn btn-set-time"
                                  disabled={!customTime}
                                  onClick={() => onSetAutoStopAt(key, customTime)}
                                >
                                  Set
                                </button>
                              </div>
                              <button
                                type="button"
                                role="menuitem"
                                className="shutdown-menu-item shutdown-menu-item--clear"
                                onClick={() => onClearAutoStop(key)}
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
                        onClick={() => onStart(key)}
                      >
                        {acting === key ? '…' : 'Start'}
                      </button>
                    )}
                    {(inst.state === 'running' || inst.state === 'pending') && (
                      <>
                        <button
                          type="button"
                          className="btn btn-restart"
                          disabled={acting !== null}
                          onClick={() => onRestart(key)}
                        >
                          {acting === key ? '…' : 'Restart'}
                        </button>
                        <button
                          type="button"
                          className="btn btn-stop"
                          disabled={acting !== null}
                          onClick={() => onStop(key)}
                        >
                          {acting === key ? '…' : 'Stop'}
                        </button>
                      </>
                    )}
                    {inst.state !== 'terminated' && inst.state !== 'shutting-down' && (
                      <button
                        type="button"
                        className="btn btn-kill"
                        disabled={acting !== null}
                        onClick={() => onKill(key)}
                      >
                        {acting === key ? '…' : 'Kill'}
                      </button>
                    )}
                  </td>
                </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>
      {!listInfra && createSection}
    </>
  )
}
