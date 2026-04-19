import { useEffect, useRef, useState } from 'react'
import {
  addWebRoute,
  fetchWebRoutesAll,
  listInstances,
  removeWebRoute,
  type Instance,
} from '../api/client'
import { instanceKey, publicWebRouteUrl, stateColor } from './workstationUtils'

const POLL_INTERVAL_MS = 10_000
const BACKGROUND_POLL_INTERVAL_MS = 5 * 60 * 1000

export function WebRoutesTab() {
  const [instances, setInstances] = useState<Instance[]>([])
  const [loading, setLoading] = useState(true)
  const [bannerError, setBannerError] = useState<string | null>(null)
  const [refreshError, setRefreshError] = useState<string | null>(null)
  const [webRoutesByName, setWebRoutesByName] = useState<Record<string, number[]>>({})
  const [webRoutesBusy, setWebRoutesBusy] = useState<string | null>(null)
  const [portDraftByKey, setPortDraftByKey] = useState<Record<string, string>>({})
  const loadInFlightRef = useRef(false)
  const webRoutesBusyRef = useRef<string | null>(null)
  webRoutesBusyRef.current = webRoutesBusy

  const load = async (opts?: { isBackgroundRefresh?: boolean }) => {
    const isBackground = opts?.isBackgroundRefresh === true
    if (loadInFlightRef.current) return
    loadInFlightRef.current = true
    if (!isBackground) {
      setLoading(true)
    }
    try {
      const [list, webRes] = await Promise.all([
        listInstances(),
        fetchWebRoutesAll().catch((e) => {
          console.error('fetchWebRoutesAll failed:', e)
          return { routes: {} as Record<string, number[]> }
        }),
      ])
      setInstances(list)
      setWebRoutesByName(webRes.routes ?? {})
      setRefreshError(null)
      if (!isBackground) setBannerError(null)
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      const fallback = 'Unable to load workstations. Check the browser console or API logs.'
      if (isBackground) {
        setRefreshError('Could not refresh. Will retry.')
        console.error('WebRoutesTab poll failed:', e)
      } else {
        setBannerError(!msg.trim() ? fallback : msg)
        console.error('WebRoutesTab load failed:', e)
      }
    } finally {
      if (!isBackground) setLoading(false)
      loadInFlightRef.current = false
    }
  }

  useEffect(() => {
    load()

    let intervalId: number | null = null
    let intervalMs = POLL_INTERVAL_MS

    const schedule = () => {
      if (intervalId !== null) window.clearInterval(intervalId)
      intervalId = window.setInterval(() => {
        if (loadInFlightRef.current || webRoutesBusyRef.current !== null) return
        load({ isBackgroundRefresh: true })
      }, intervalMs)
    }
    schedule()

    const onVisibility = () => {
      intervalMs = document.visibilityState === 'hidden' ? BACKGROUND_POLL_INTERVAL_MS : POLL_INTERVAL_MS
      schedule()
    }
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      document.removeEventListener('visibilitychange', onVisibility)
      if (intervalId !== null) window.clearInterval(intervalId)
    }
  }, [])

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
      const result = await addWebRoute(key, port)
      setWebRoutesByName((prev) => ({ ...prev, [result.name]: result.ports }))
      setPortDraftByKey((prev) => ({ ...prev, [key]: '' }))
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
      const result = await removeWebRoute(key, port)
      setWebRoutesByName((prev) => ({ ...prev, [result.name]: result.ports }))
    } catch (e) {
      setBannerError(e instanceof Error ? e.message : String(e))
    } finally {
      setWebRoutesBusy(null)
    }
  }

  if (loading) {
    return <p className="loading">Loading web routes…</p>
  }

  return (
    <>
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
      {refreshError && (
        <p className="refresh-error" role="status">{refreshError}</p>
      )}
      <div className="table-wrap">
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
