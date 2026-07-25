import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  fetchRouterInfraStatus,
  killInstance,
  listInstances,
  sleepRouterInfra,
  startInstance,
  stopInstance,
  wakeRouterInfra,
  type FutureRouterAmiInfo,
  type Instance,
  type RouterInfraStatus,
} from '../api/client'
import { useAdaptiveRefetchInterval } from '../hooks/useAdaptiveRefetchInterval'
import { queryKeys } from '../queryKeys'
import { formatAmiLine, futureRouterAmiSummaryClass, instanceKey, stateColor } from '../pages/workstationUtils'

const POLL_INTERVAL_MS = 10_000
const BACKGROUND_POLL_INTERVAL_MS = 5 * 60 * 1000

function friendlyPhaseColor(label: RouterInfraStatus['friendly_label']): string {
  switch (label) {
    case 'Running': return '#22c55e'
    case 'Starting':
    case 'Stopping': return '#eab308'
    case 'Stopped': return '#94a3b8'
    case 'Error': return '#ef4444'
    default: return '#94a3b8'
  }
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

function RouterInstanceRow({
  inst,
  instanceOpsEnabled,
  acting,
  onStart,
  onStop,
  onKill,
}: {
  inst: Instance
  instanceOpsEnabled: boolean
  acting: string | null
  onStart: (name: string) => void
  onStop: (name: string) => void
  onKill: (name: string) => void
}) {
  const key = instanceKey(inst)
  return (
    <tr key={inst.instance_id}>
      <td className="name">{key}</td>
      <td>
        <span className="state-label" style={{ color: stateColor(inst.state) }}>
          {inst.state}
        </span>
      </td>
      <td className="actions">
        {inst.state === 'stopped' && (
          <button
            type="button"
            className="btn btn-start"
            disabled={acting !== null || !instanceOpsEnabled}
            title={!instanceOpsEnabled ? 'Launch the router stack first' : undefined}
            onClick={() => onStart(key)}
          >
            {acting === key ? '…' : 'Start'}
          </button>
        )}
        {(inst.state === 'running' || inst.state === 'pending') && (
          <button
            type="button"
            className="btn btn-stop"
            disabled={acting !== null || !instanceOpsEnabled}
            title={!instanceOpsEnabled ? 'Launch the router stack first' : undefined}
            onClick={() => onStop(key)}
          >
            {acting === key ? '…' : 'Stop'}
          </button>
        )}
        {inst.state !== 'terminated' && inst.state !== 'shutting-down' && (
          <button
            type="button"
            className="btn btn-kill"
            disabled={acting !== null || !instanceOpsEnabled}
            title={!instanceOpsEnabled ? 'Launch the router stack first' : undefined}
            onClick={() => onKill(key)}
          >
            {acting === key ? '…' : 'Kill'}
          </button>
        )}
      </td>
    </tr>
  )
}

export function RouterInfraSection({
  pollIntervalMs,
  acting,
  setActing,
  setActionError,
}: {
  pollIntervalMs: number
  acting: string | null
  setActing: (v: string | null) => void
  setActionError: (v: string | null) => void
}) {
  const queryClient = useQueryClient()
  const [stackActing, setStackActing] = useState(false)

  const statusQuery = useQuery({
    queryKey: queryKeys.routerInfraStatus,
    queryFn: fetchRouterInfraStatus,
    staleTime: 5_000,
    refetchInterval: () => (acting !== null || stackActing ? false : pollIntervalMs),
  })

  const routerInstancesQuery = useQuery({
    queryKey: queryKeys.workstations(true),
    queryFn: () => listInstances({ infra: true }),
    staleTime: 5_000,
    refetchInterval: () => (acting !== null || stackActing ? false : pollIntervalMs),
  })

  const status = statusQuery.data
  const routerInstances = routerInstancesQuery.data?.instances ?? []
  const futureRouterAmi = routerInstancesQuery.data?.future_router_ami
  const instanceOpsEnabled = status?.instance_ops_enabled ?? false
  const busy = stackActing || acting !== null

  const onStackWake = async () => {
    setStackActing(true)
    setActionError(null)
    try {
      await wakeRouterInfra()
      await queryClient.invalidateQueries({ queryKey: queryKeys.routerInfraStatus })
      await queryClient.invalidateQueries({ queryKey: ['workstations'] })
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setStackActing(false)
    }
  }

  const onStackSleep = async (force: boolean) => {
    setStackActing(true)
    setActionError(null)
    try {
      await sleepRouterInfra(force)
      await queryClient.invalidateQueries({ queryKey: queryKeys.routerInfraStatus })
      await queryClient.invalidateQueries({ queryKey: ['workstations'] })
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setStackActing(false)
    }
  }

  const onRouterStart = async (name: string) => {
    setActing(name)
    setActionError(null)
    try {
      await startInstance(name, { infra: true })
      await queryClient.invalidateQueries({ queryKey: ['workstations'] })
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setActing(null)
    }
  }

  const onRouterStop = async (name: string) => {
    setActing(name)
    setActionError(null)
    try {
      await stopInstance(name, { infra: true })
      await queryClient.invalidateQueries({ queryKey: ['workstations'] })
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setActing(null)
    }
  }

  const onRouterKill = async (name: string) => {
    if (!window.confirm('Terminate the router instance? The ASG will typically replace it.')) return
    setActing(name)
    setActionError(null)
    try {
      await killInstance(name, { infra: true })
      await queryClient.invalidateQueries({ queryKey: ['workstations'] })
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setActing(null)
    }
  }

  const friendlyLabel = status?.friendly_label ?? '…'
  const badgeColor = friendlyPhaseColor(friendlyLabel)

  return (
    <details className="router-infra-section">
      <summary className="router-infra-section__summary">
        <span className="router-infra-section__title">Router infra</span>
        <span
          className="router-infra-section__badge"
          style={{ color: badgeColor, borderColor: badgeColor }}
        >
          {friendlyLabel}
        </span>
      </summary>
      <div className="router-infra-section__body">
        {status && (
          <>
            <p className="router-infra-section__friendly">{status.friendly_label}</p>
            <ul className="router-infra-status__details">
              {status.base_stack_status && <li>Base stack: {status.base_stack_status}</li>}
              {status.active_stack_status && <li>Active stack: {status.active_stack_status}</li>}
              {status.asg_desired != null && (
                <li>ASG desired/in-service: {status.asg_desired}/{status.asg_in_service ?? 0}</li>
              )}
              {status.target_health && <li>Target health: {status.target_health}</li>}
            </ul>
            {status.demand_sources.length > 0 ? (
              <div className="router-infra-section__demand">
                <div className="router-infra-section__demand-title">Keeping router infra awake</div>
                <ul>
                  {status.demand_sources.map((src) => (
                    <li key={src.name}>
                      {src.name} ({src.state}) — ports {src.ports.join(', ')}
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="router-infra-section__demand-empty">
                No pending or running workstations with web-route ports.
              </p>
            )}
            {status.messages.length > 0 && (
              <ul className="router-infra-section__messages">
                {status.messages.map((m) => (
                  <li key={m}>{m}</li>
                ))}
              </ul>
            )}
            <div className="router-infra-status__actions">
              <button type="button" className="btn btn-start" disabled={busy} onClick={() => void onStackWake()}>
                Launch stack
              </button>
              <button
                type="button"
                className="btn btn-stop"
                disabled={busy}
                onClick={() => {
                  if (status.demand) {
                    if (
                      !window.confirm(
                        'Pending/running workstations still have web-route ports. Public routes will be unavailable until the stack wakes again. Continue?',
                      )
                    ) {
                      return
                    }
                    void onStackSleep(true)
                  } else {
                    void onStackSleep(false)
                  }
                }}
              >
                Shutdown stack
              </button>
            </div>
          </>
        )}
        {futureRouterAmi && <FutureRouterAmiSummary info={futureRouterAmi} />}
        <div className="table-wrap router-infra-section__table">
          <table className="instances-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {routerInstances.length === 0 ? (
                <tr>
                  <td colSpan={3} className="empty">No router instances found.</td>
                </tr>
              ) : (
                routerInstances.map((inst) => (
                  <RouterInstanceRow
                    key={inst.instance_id}
                    inst={inst}
                    instanceOpsEnabled={instanceOpsEnabled}
                    acting={acting}
                    onStart={(n) => void onRouterStart(n)}
                    onStop={(n) => void onRouterStop(n)}
                    onKill={(n) => void onRouterKill(n)}
                  />
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </details>
  )
}

export function useRouterInfraPollInterval() {
  return useAdaptiveRefetchInterval(POLL_INTERVAL_MS, BACKGROUND_POLL_INTERVAL_MS)
}
