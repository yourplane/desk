/** Timeout for browser reachability probes of public web routes. */
export const ROUTE_PROBE_TIMEOUT_MS = 8_000

export type RouteReachability = 'live' | 'dead' | 'unknown'

const ROUTER_DEAD_STATUSES = new Set([502, 504])

function normalizeBaseUrl(baseUrl: string): string {
  return baseUrl.endsWith('/') ? baseUrl : `${baseUrl}/`
}

/** Classify an HTTP status from a credentialed CORS probe. */
export function classifyRouteHttpStatus(status: number): RouteReachability {
  if (ROUTER_DEAD_STATUSES.has(status) || status === 404) return 'dead'
  if (status >= 200 && status < 400) return 'live'
  if (status >= 400) return 'live'
  return 'unknown'
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
 * Uses credentialed CORS requests (desk_web_gate cookie + CloudFront/Caddy ACAO) to read status.
 * Dead = network failure, timeout, 404, or router 502/504. Unknown = responded but status hidden.
 */
export async function probeWebRouteReachability(baseUrl: string): Promise<RouteReachability> {
  const url = normalizeBaseUrl(baseUrl)
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), ROUTE_PROBE_TIMEOUT_MS)

  const probeCors = async (method: 'HEAD' | 'GET'): Promise<RouteReachability | null> => {
    try {
      const response = await fetchWithTimeout(
        url,
        {
          method,
          mode: 'cors',
          credentials: 'include',
          redirect: 'manual',
        },
        controller.signal,
      )
      if (response.type === 'opaqueredirect' || response.status === 0) return 'unknown'
      if (response.status >= 300 && response.status < 400) return 'unknown'
      return classifyRouteHttpStatus(response.status)
    } catch {
      if (controller.signal.aborted) return 'dead'
      return null
    }
  }

  const probeNoCors = async (method: 'HEAD' | 'GET'): Promise<boolean> => {
    try {
      await fetchWithTimeout(
        url,
        { method, mode: 'no-cors', credentials: 'include' },
        controller.signal,
      )
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
