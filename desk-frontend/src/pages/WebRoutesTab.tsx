import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import {
  addWebRoute,
  fetchWebRoutesAll,
  listInstances,
  removeWebRoute,
  type Instance,
} from '../api/client'
import { DataFreshnessBar } from '../DataFreshnessBar'
import { useAdaptiveRefetchInterval } from '../hooks/useAdaptiveRefetchInterval'
import { queryKeys } from '../queryKeys'
import { instanceKey, publicWebRouteUrl, stateColor } from './workstationUtils'

const POLL_INTERVAL_MS = 10_000
const BACKGROUND_POLL_INTERVAL_MS = 5 * 60 * 1000

async function fetchWebRoutesSafe(): Promise<{ routes: Record<string, number[]> }> {
  try {
    return await fetchWebRoutesAll()
  } catch (e) {
    console.error('fetchWebRoutesAll failed:', e)
    return { routes: {} }
  }
}

export function WebRoutesTab() {
  const queryClient = useQueryClient()
  const pollIntervalMs = useAdaptiveRefetchInterval(POLL_INTERVAL_MS, BACKGROUND_POLL_INTERVAL_MS)
  const [bannerError, setBannerError] = useState<string | null>(null)
  const [webRoutesBusy, setWebRoutesBusy] = useState<string | null>(null)
  const [portDraftByKey, setPortDraftByKey] = useState<Record<string, string>>({})
  const webRoutesBusyRef = useRef<string | null>(null)
  webRoutesBusyRef.current = webRoutesBusy

  const instancesQuery = useQuery({
    queryKey: queryKeys.workstations(false),
    queryFn: () => listInstances(),
    staleTime: 5_000,
    refetchInterval: () => (webRoutesBusyRef.current !== null ? false : pollIntervalMs),
  })

  const webRoutesQuery = useQuery({
    queryKey: queryKeys.webRoutesAll,
    queryFn: fetchWebRoutesSafe,
    staleTime: 5_000,
    refetchInterval: () => (webRoutesBusyRef.current !== null ? false : pollIntervalMs),
  })

  const instances: Instance[] = instancesQuery.data ?? []
  const webRoutesByName = webRoutesQuery.data?.routes ?? {}

  const blockingError =
    instancesQuery.isError && instancesQuery.data === undefined
      ? instancesQuery.error instanceof Error
        ? instancesQuery.error.message
        : String(instancesQuery.error)
      : null
  const fallbackMsg = 'Unable to load workstations. Check the browser console or API logs.'
  const loadError = blockingError && !blockingError.trim() ? fallbackMsg : blockingError

  const instancesRefreshError =
    instancesQuery.isError && instancesQuery.data !== undefined
      ? 'Could not refresh workstation list. Will retry.'
      : null

  const combinedFetching = instancesQuery.isFetching || webRoutesQuery.isFetching
  const dataUpdatedAt = Math.max(instancesQuery.dataUpdatedAt ?? 0, webRoutesQuery.dataUpdatedAt ?? 0)

  const refetchAll = () => {
    void instancesQuery.refetch()
    void webRoutesQuery.refetch()
  }

  const onAddWebRoute = async (key: string) => {
    const raw = (portDraftByKey[key] ?? '').trim()
    const port = parseInt(raw, 10)
    if (Number.isNaN(port) || port < 1 || port > 65535) {
      setBannerError('Enter a valid port (1–65535).')
      return
    }
    setWebRoutesBusy(key)
    setBannerError(null)
    try {
      await addWebRoute(key, port)
      setPortDraftByKey((prev) => ({ ...prev, [key]: '' }))
      await queryClient.invalidateQueries({ queryKey: queryKeys.webRoutesAll })
    } catch (e) {
      setBannerError(e instanceof Error ? e.message : String(e))
    } finally {
      setWebRoutesBusy(null)
    }
  }

  const onRemoveWebRoute = async (key: string, port: number) => {
    setWebRoutesBusy(key)
    setBannerError(null)
    try {
      await removeWebRoute(key, port)
      await queryClient.invalidateQueries({ queryKey: queryKeys.webRoutesAll })
    } catch (e) {
      setBannerError(e instanceof Error ? e.message : String(e))
    } finally {
      setWebRoutesBusy(null)
    }
  }

  if (instancesQuery.isPending && instancesQuery.data === undefined) {
    return <p className="loading">Loading web routes…</p>
  }

  if (loadError) {
    return (
      <>
        <p className="error-message" role="alert">{loadError}</p>
      </>
    )
  }

  return (
    <>
      <DataFreshnessBar
        resourceLabel="Web routes & workstations"
        dataUpdatedAt={dataUpdatedAt || undefined}
        isFetching={combinedFetching}
        onRefresh={refetchAll}
      />
      {instancesRefreshError && (
        <p className="refresh-error" role="status">{instancesRefreshError}</p>
      )}
      {bannerError && (
        <div className="web-routes-banner web-routes-banner--error" role="alert">
          <span className="web-routes-banner-text">{bannerError}</span>
          <button
            type="button"
            className="web-routes-banner-dismiss"
            onClick={() => setBannerError(null)}
            aria-label="Dismiss error"
          >
            ×
          </button>
        </div>
      )}
      <div
        className={`table-wrap${combinedFetching && instances.length > 0 ? ' table-wrap--revalidating' : ''}`}
      >
        <table className="instances-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Status</th>
              <th>Web routes</th>
            </tr>
          </thead>
          <tbody>
            {instances.length === 0 ? (
              <tr>
                <td colSpan={3} className="empty">No workstations found.</td>
              </tr>
            ) : (
              instances.map((inst) => {
                const key = instanceKey(inst)
                const ports = webRoutesByName[key] ?? []
                const routeBusy = webRoutesBusy === key
                const canEditWebRoutes = inst.state !== 'terminated' && inst.state !== 'shutting-down'
                return (
                  <tr key={inst.instance_id}>
                    <td className="name">{key}</td>
                    <td>
                      <span className="state-label" style={{ color: stateColor(inst.state) }}>
                        {inst.state}
                      </span>
                    </td>
                    <td className="web-routes-cell">
                      {canEditWebRoutes ? (
                        <div className="web-routes-editor">
                          <div className="web-routes-chips">
                            {ports.map((p) => {
                              const publicUrl = publicWebRouteUrl(key, p)
                              return (
                                <span key={p} className="port-chip">
                                  {publicUrl ? (
                                    <a
                                      className="port-chip-label port-chip-link"
                                      href={publicUrl}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      title={`Open web route (new tab): ${publicUrl}`}
                                    >
                                      {p}
                                    </a>
                                  ) : (
                                    <span className="port-chip-label" title="Set VITE_WEB_ROUTER_HOST_SUFFIX at build (custom domain) for public links">
                                      {p}
                                    </span>
                                  )}
                                  <button
                                    type="button"
                                    className="port-chip-remove"
                                    disabled={routeBusy}
                                    onClick={() => onRemoveWebRoute(key, p)}
                                    title={`Remove port ${p}`}
                                  >
                                    ×
                                  </button>
                                </span>
                              )
                            })}
                          </div>
                          <div className="web-routes-add">
                            <input
                              type="number"
                              className="web-routes-port-input"
                              min={1}
                              max={65535}
                              placeholder="Port"
                              value={portDraftByKey[key] ?? ''}
                              disabled={routeBusy}
                              onChange={(e) =>
                                setPortDraftByKey((prev) => ({ ...prev, [key]: e.target.value }))
                              }
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') {
                                  e.preventDefault()
                                  void onAddWebRoute(key)
                                }
                              }}
                            />
                            <button
                              type="button"
                              className="btn btn-secondary btn-web-route-add"
                              disabled={routeBusy}
                              onClick={() => void onAddWebRoute(key)}
                            >
                              {routeBusy ? '…' : 'Add'}
                            </button>
                          </div>
                        </div>
                      ) : (
                        <span className="web-routes-readonly">
                          {ports.length > 0 ? ports.join(', ') : '—'}
                        </span>
                      )}
                    </td>
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>
    </>
  )
}
