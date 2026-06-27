import type { Instance } from '../api/client'

/** Matches desk web-router hostname rules (letters, numbers, _, -). */
const WEB_ROUTE_WS = /^[a-zA-Z0-9_-]+$/

export function instanceKey(inst: Instance): string {
  return inst.name && inst.name !== '-' ? inst.name : inst.instance_id
}

/** First DNS label `{workstation}-{port}` for public web routes, or null if the name is unsupported. */
export function webRouteHostnameLabel(workstationKey: string, port: number): string | null {
  const ws = workstationKey.trim()
  if (!WEB_ROUTE_WS.test(ws)) return null
  if (port < 1 || port > 65535) return null
  const label = `${ws}-${port}`
  if (label.length > 63) return null
  return label
}

/** Apex FQDN for `{name}-{port}.<suffix>` links (build-time env, or current hostname when not on *.cloudfront.net / localhost). */
function webRouteHostSuffix(): string | null {
  const fromEnv = (import.meta.env.VITE_WEB_ROUTER_HOST_SUFFIX as string | undefined)?.trim()
  if (fromEnv) return fromEnv
  if (typeof window === 'undefined') return null
  const host = window.location.hostname
  if (!host || host === 'localhost' || host === '127.0.0.1') return null
  if (host.endsWith('.cloudfront.net')) return null
  return host
}

/** HTTPS URL for CloudFront → ALB web routes when suffix is known (deploy env or same host as the SPA). */
export function publicWebRouteUrl(workstationKey: string, port: number): string | null {
  const suffix = webRouteHostSuffix()
  if (!suffix) return null
  const label = webRouteHostnameLabel(workstationKey, port)
  if (!label) return null
  return `https://${label}.${suffix}/`
}

/** Common favicon paths to try for a public web route base URL (trailing slash optional). */
export function webRouteFaviconCandidates(baseUrl: string): string[] {
  const base = baseUrl.endsWith('/') ? baseUrl : `${baseUrl}/`
  return [
    `${base}favicon.ico`,
    `${base}favicon.svg`,
    `${base}vite.svg`,
    `${base}apple-touch-icon.png`,
  ]
}

export function stateColor(state: string): string {
  switch (state) {
    case 'running':
      return 'var(--state-running)'
    case 'pending':
      return 'var(--state-pending)'
    case 'stopped':
      return 'var(--state-stopped)'
    case 'stopping':
      return 'var(--state-pending)'
    default:
      return 'var(--state-default)'
  }
}
