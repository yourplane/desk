import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  listInstances,
  setAutoStop,
  startInstance,
  stopInstance,
  killInstance,
  type FutureRouterAmiInfo,
  type Instance,
} from '../api/client'
import { CreateWorkstationForm } from '../components/CreateWorkstationForm'
import { DataFreshnessBar } from '../DataFreshnessBar'
import { useAdaptiveRefetchInterval } from '../hooks/useAdaptiveRefetchInterval'
import { queryKeys } from '../queryKeys'
import { logout } from '../auth'
import { instanceKey, stateColor, formatAmiLine, instanceAmiSubline, futureRouterAmiSummaryClass } from './workstationUtils'

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

function FutureRouterAmiSummary({ info }: { info: FutureRouterAmiInfo }) {
  const className = futureRouterAmiSummaryClass(info)

  if (info.status === 'unavailable') {
    return (
      <div className={className} role="status">
        <div className="future-router-ami-summary__title">Future router AMI</div>
        <p className="future-router-ami-summary__warning">
          {info.warnings[0] ?? 'Router AMI info unavailable.'}
        </p>
      </div>
    )
  }

  if (info.status === 'consolidated' && info.ami) {
    return (
      <div className={className} role="status">
        <div className="future-router-ami-summary__title">Future router AMI</div>
        <p className="future-router-ami-summary__line">{formatAmiLine(info.ami)}</p>
      </div>
    )
  }

  if (info.status === 'partial' && info.ami) {
    return (
      <div className={className} role="status">
        <div className="future-router-ami-summary__title">Future router AMI</div>
        <p className="future-router-ami-summary__line">{formatAmiLine(info.ami)}</p>
        {info.warnings.map((w) => (
          <p key={w} className="future-router-ami-summary__warning">{w}</p>
        ))}
      </div>
    )
  }

  if (info.status === 'mismatch' && info.latest && info.deploy) {
    return (
      <div className={className} role="status">
        <div className="future-router-ami-summary__title">Future router AMI</div>
        <p className="future-router-ami-summary__line">
          <span className="future-router-ami-summary__label">Latest router-ami-*:</span>{' '}
          {formatAmiLine(info.latest)}
        </p>
        <p className="future-router-ami-summary__line">
          <span className="future-router-ami-summary__label">desk-router deploy:</span>{' '}
          {formatAmiLine(info.deploy)}
        </p>
        {info.warnings.map((w) => (
          <p key={w} className="future-router-ami-summary__warning">{w}</p>
        ))}
      </div>
    )
  }

  return null
}

export function InstanceList() {
  const queryClient = useQueryClient()
  const pollIntervalMs = useAdaptiveRefetchInterval(POLL_INTERVAL_MS, BACKGROUND_POLL_INTERVAL_MS)
  const [actingRows, setActingRows] = useState<Set<string>>(() => new Set())
  const [openAutoStopFor, setOpenAutoStopFor] = useState<string | null>(null)
  const [customTime, setCustomTime] = useState('')
  const [showCreateForm, setShowCreateForm] = useState(false)
  const [optimisticInstances, setOptimisticInstances] = useState<Instance[]>([])
  const autoStopMenuRef = useRef<HTMLDivElement>(null)
  const [listInfra, setListInfra] = useState(false)

  const markRowActing = useCallback((name: string) => {
    setActingRows((prev) => {
      if (prev.has(name)) return prev
      const next = new Set(prev)
      next.add(name)
      return next
    })
  }, [])

  const clearRowActing = useCallback((name: string) => {
    setActingRows((prev) => {
      if (!prev.has(name)) return prev
      const next = new Set(prev)
      next.delete(name)
      return next
    })
  }, [])

  const instancesQuery = useQuery({
    queryKey: queryKeys.workstations(listInfra),
    queryFn: () => listInstances({ infra: listInfra }),
    placeholderData: (previousData, previousQuery) => {
      if (previousQuery?.queryKey[1] === listInfra) return previousData
      return undefined
    },
    staleTime: 5_000,
    refetchInterval: pollIntervalMs,
  })

  const instances: Instance[] = instancesQuery.data?.instances ?? []
  const displayInstances = useMemo(() => {
    const serverNames = new Set(instances.map((inst) => inst.name))
    const pending = optimisticInstances.filter((inst) => !serverNames.has(inst.name))
    return [...instances, ...pending]
  }, [instances, optimisticInstances])
  const futureRouterAmi = instancesQuery.data?.future_router_ami
  const instancesLoading =
    instancesQuery.isFetching && instances.length === 0 && !instancesQuery.isError
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

  useEffect(() => {
    const serverNames = new Set(instances.map((inst) => inst.name))
    setOptimisticInstances((prev) => prev.filter((inst) => !serverNames.has(inst.name)))
  }, [instances])

  const refetchWorkstations = () => instancesQuery.refetch()

  const onStart = async (name: string) => {
    markRowActing(name)
    setActionError(null)
    try {
      await startInstance(name, { infra: listInfra })
      await queryClient.invalidateQueries({ queryKey: ['workstations'] })
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      clearRowActing(name)
    }
  }

  const onStop = async (name: string) => {
    markRowActing(name)
    setActionError(null)
    try {
      await stopInstance(name, { infra: listInfra })
      await queryClient.invalidateQueries({ queryKey: ['workstations'] })
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      clearRowActing(name)
    }
  }

  const onSetAutoStop = async (name: string, duration: string) => {
    markRowActing(name)
    setActionError(null)
    setOpenAutoStopFor(null)
    try {
      await setAutoStop(name, { duration })
      await queryClient.invalidateQueries({ queryKey: ['workstations'] })
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      clearRowActing(name)
    }
  }

  const onKill = async (name: string) => {
    if (!window.confirm('Terminate this workstation? This cannot be undone.')) return
    markRowActing(name)
    setActionError(null)
    try {
      await killInstance(name, { infra: listInfra })
      await queryClient.invalidateQueries({ queryKey: ['workstations'] })
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      clearRowActing(name)
    }
  }

  const onClearAutoStop = async (name: string) => {
    markRowActing(name)
    setActionError(null)
    setOpenAutoStopFor(null)
    try {
      await setAutoStop(name, { clear: true })
      await queryClient.invalidateQueries({ queryKey: ['workstations'] })
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      clearRowActing(name)
    }
  }

  const onSetAutoStopAt = async (name: string, localDatetime: string) => {
    markRowActing(name)
    setActionError(null)
    setOpenAutoStopFor(null)
    try {
      const utcIso = new Date(localDatetime).toISOString()
      await setAutoStop(name, { shutdown_at: utcIso })
      await queryClient.invalidateQueries({ queryKey: ['workstations'] })
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      clearRowActing(name)
    }
  }

  const onPlus2h = async (name: string, shutdownAt: string | null) => {
    markRowActing(name)
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
      clearRowActing(name)
    }
  }

  const createSection = (
    <div className="create-section">
      {showCreateForm ? (
        <CreateWorkstationForm
          onClose={() => setShowCreateForm(false)}
          onLaunchStarted={(wsName) => {
            setActionError(null)
            setOptimisticInstances((prev) => [
              ...prev.filter((inst) => inst.name !== wsName),
              {
                instance_id: `launching:${wsName}`,
                name: wsName,
                state: 'launching',
                shutdown_at: null,
              },
            ])
          }}
          onLaunchFinished={({ name: wsName, ok, error, result }) => {
            if (ok && result) {
              setOptimisticInstances((prev) =>
                prev.map((inst) =>
                  inst.name === wsName
                    ? {
                        instance_id: result.instance_id,
                        name: result.name,
                        state: result.state || 'pending',
                        shutdown_at: result.shutdown_at,
                      }
                    : inst,
                ),
              )
            } else {
              setOptimisticInstances((prev) => prev.filter((inst) => inst.name !== wsName))
              setActionError(error ?? `Failed to launch workstation “${wsName}”.`)
            }
          }}
        />
      ) : (
        <button
          type="button"
          className="btn btn-start"
          onClick={() => setShowCreateForm(true)}
        >
          Create
        </button>
      )}
    </div>
  )

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
        resourceLabel={listInfra ? 'Router infra list' : 'Workstation list'}
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
      <div className="instance-list-toolbar">
        <div className="instance-list-view-toggle" role="group" aria-label="Instance list view">
          <button
            type="button"
            className={`instance-list-view-toggle__btn${!listInfra ? ' instance-list-view-toggle__btn--active' : ''}`}
            aria-pressed={!listInfra}
            onClick={() => setListInfra(false)}
          >
            Workstations
          </button>
          <button
            type="button"
            className={`instance-list-view-toggle__btn${listInfra ? ' instance-list-view-toggle__btn--active' : ''}`}
            aria-pressed={listInfra}
            onClick={() => setListInfra(true)}
          >
            Router infra
          </button>
        </div>
      </div>
      {listInfra && futureRouterAmi && (
        <FutureRouterAmiSummary info={futureRouterAmi} />
      )}
      <div
        className={`table-wrap${instancesQuery.isFetching && displayInstances.length > 0 ? ' table-wrap--revalidating' : ''}`}
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
            {instancesLoading ? (
              <tr>
                <td colSpan={4} className="empty">
                  {listInfra ? 'Loading router instances…' : 'Loading workstations…'}
                </td>
              </tr>
            ) : displayInstances.length === 0 ? (
              <tr>
                <td colSpan={4} className="empty">
                  {listInfra ? 'No router instances found.' : 'No workstations found.'}
                </td>
              </tr>
            ) : (
              displayInstances.map((inst) => {
                const key = instanceKey(inst)
                const isLaunching = inst.state === 'launching'
                return (
                <tr key={inst.instance_id}>
                  <td className="name">
                    <div>{key}</div>
                    <div className="instance-ami-subline">{instanceAmiSubline(inst)}</div>
                  </td>
                  <td>
                    <span className="state-label" style={{ color: stateColor(inst.state) }}>
                      {inst.state}
                    </span>
                  </td>
                  <td className="shutdown">
                    {listInfra || isLaunching ? (
                      '—'
                    ) : (() => {
                      const { absolute, relative } = formatShutdownLocal(inst.shutdown_at, inst.state)
                      const isRunningOrPending = inst.state === 'running' || inst.state === 'pending'
                      const menuOpen = openAutoStopFor === key
                      const rowBusy = actingRows.has(key)
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
                            disabled={rowBusy}
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
                            disabled={rowBusy}
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
                    {isLaunching ? (
                      <span className="actions-placeholder">—</span>
                    ) : (
                      <>
                    {inst.state === 'stopped' && (
                      <button
                        type="button"
                        className="btn btn-start"
                        disabled={rowBusy}
                        onClick={() => onStart(key)}
                      >
                        {rowBusy ? '…' : 'Start'}
                      </button>
                    )}
                    {(inst.state === 'running' || inst.state === 'pending') && (
                      <button
                        type="button"
                        className="btn btn-stop"
                        disabled={rowBusy}
                        onClick={() => onStop(key)}
                      >
                        {rowBusy ? '…' : 'Stop'}
                      </button>
                    )}
                    {inst.state !== 'terminated' && inst.state !== 'shutting-down' && (
                      <button
                        type="button"
                        className="btn btn-kill"
                        disabled={rowBusy}
                        onClick={() => onKill(key)}
                      >
                        {rowBusy ? '…' : 'Kill'}
                      </button>
                    )}
                      </>
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
