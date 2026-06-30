/** Timeout for browser reachability probes of public web routes. */
export const ROUTE_PROBE_TIMEOUT_MS = 8_000

export type RouteReachability = 'live' | 'dead' | 'unknown'

const ROUTER_DEAD_STATUSES = new Set([502, 504])

function normalizeBaseUrl(baseUrl: string): string {
  return baseUrl.endsWith('/') ? baseUrl : `${baseUrl}/`
}

function isRouterDeadStatus(status: number): boolean {
  return ROUTER_DEAD_STATUSES.has(status)
}

async function fetchWithTimeout(
  url: string,
  init: RequestInit,
  signal: AbortSignal,
): Promise<Response> {
  const response = await fetch(url, { ...init, signal, cache: 'no-store' })
  return response
}

/**
 * Probe a public web route from the browser when favicons are unavailable.
 * Dead = network failure, timeout, or router 502/504. Unknown = responded but status hidden (CORS).
 */
export async function probeWebRouteReachability(baseUrl: string): Promise<RouteReachability> {
  const url = normalizeBaseUrl(baseUrl)
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), ROUTE_PROBE_TIMEOUT_MS)

  const probeCors = async (method: 'HEAD' | 'GET'): Promise<RouteReachability | null> => {
    try {
      const response = await fetchWithTimeout(url, { method, mode: 'cors' }, controller.signal)
      if (isRouterDeadStatus(response.status)) return 'dead'
      return 'live'
    } catch (error) {
      if (controller.signal.aborted) return 'dead'
      return null
    }
  }

  const probeNoCors = async (method: 'HEAD' | 'GET'): Promise<boolean> => {
    try {
      await fetchWithTimeout(url, { method, mode: 'no-cors' }, controller.signal)
      return true
    } catch {
      return false
    }
  }

  try {
    const headResult = await probeCors('HEAD')
    if (headResult) return headResult

    const getResult = await probeCors('GET')
    if (getResult) return getResult

    if (controller.signal.aborted) return 'dead'

    if (await probeNoCors('HEAD')) return 'unknown'
    if (controller.signal.aborted) return 'dead'
    if (await probeNoCors('GET')) return 'unknown'

    return 'dead'
  } finally {
    window.clearTimeout(timeoutId)
  }
}
