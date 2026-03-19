import { getToken, refreshIdToken } from '../auth'

export interface Instance {
  instance_id: string
  name: string
  state: string
  shutdown_at: string | null
}

function authHeaders(): HeadersInit {
  const token = getToken()
  if (token) return { Authorization: `Bearer ${token}` }
  return {}
}

function buildHeaders(existing?: HeadersInit): Headers {
  const h = new Headers(existing)
  const auth = authHeaders() as any
  if (auth?.Authorization) h.set('Authorization', auth.Authorization)
  else h.delete('Authorization')
  return h
}

async function fetchWithAuthRetry(url: string, init: RequestInit): Promise<Response> {
  let res = await fetch(url, { ...init, headers: buildHeaders(init.headers) })
  if (res.status !== 401) return res

  const refreshed = await refreshIdToken()
  if (!refreshed) return res

  res = await fetch(url, { ...init, headers: buildHeaders(init.headers) })
  return res
}

function errorMessage(res: Response, text: string): string {
  if (res.status === 401) {
    return 'Session expired or invalid. Please log in again.'
  }
  return text?.trim() || `Request failed (${res.status})`
}

export async function listInstances(): Promise<Instance[]> {
  const res = await fetchWithAuthRetry('/api/workstations', { headers: authHeaders() })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(errorMessage(res, text))
  }
  return res.json()
}

export async function startInstance(name: string): Promise<{ instance_id: string; shutdown_at?: string | null }> {
  const res = await fetchWithAuthRetry(`/api/workstations/${encodeURIComponent(name)}/start`, {
    method: 'POST',
    headers: authHeaders(),
  })
  if (!res.ok) {
    const text = await res.text()
    let detail = text
    try {
      const j = JSON.parse(text)
      if (j.detail) detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch {
      // use text as-is
    }
    throw new Error(detail)
  }
  return res.json()
}

export async function stopInstance(name: string): Promise<{ instance_id: string }> {
  const res = await fetchWithAuthRetry(`/api/workstations/${encodeURIComponent(name)}/stop`, {
    method: 'POST',
    headers: authHeaders(),
  })
  if (!res.ok) {
    const text = await res.text()
    let detail = text
    try {
      const j = JSON.parse(text)
      if (j.detail) detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch {
      // use text as-is
    }
    throw new Error(detail)
  }
  return res.json()
}

export async function killInstance(name: string): Promise<{ instance_id: string }> {
  const res = await fetchWithAuthRetry(`/api/workstations/${encodeURIComponent(name)}/kill`, {
    method: 'POST',
    headers: authHeaders(),
  })
  if (!res.ok) {
    const text = await res.text()
    let detail = text
    try {
      const j = JSON.parse(text)
      if (j.detail) detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch {
      // use text as-is
    }
    throw new Error(detail)
  }
  return res.json()
}

export type SetAutoStopResult =
  | { instance_id: string; shutdown_at: string }
  | { instance_id: string; shutdown_cleared: true }

export async function setAutoStop(
  name: string,
  options: { duration?: string; clear?: boolean }
): Promise<SetAutoStopResult> {
  const body = options.clear ? { clear: true } : { duration: options.duration ?? '4h' }
  const res = await fetchWithAuthRetry(
    `/api/workstations/${encodeURIComponent(name)}/auto-stop`,
    {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }
  )

  // CloudFront is configured to return `index.html` (200) for some API errors (e.g. 403/404),
  // which would break `res.json()`. Always parse as text first.
  const text = await res.text()
  const trimmed = text.trim()

  let parsed: any = null
  if (trimmed) {
    try {
      parsed = JSON.parse(trimmed)
    } catch {
      // Not JSON (likely HTML).
    }
  }

  if (!res.ok) {
    const detail =
      (parsed && (parsed.detail ?? parsed.error)) ||
      trimmed ||
      `Request failed (${res.status})`
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }

  // If API returned HTML even with 200, surface a clearer error.
  if (!parsed || typeof parsed !== 'object') {
    if (trimmed.startsWith('<')) {
      const rawSnippet = trimmed.length > 800 ? `${trimmed.slice(0, 800)}…` : trimmed
      throw new Error(
        `Auto-stop request returned HTML. This usually means the API route is missing or access is denied. Raw response: ${rawSnippet}`
      )
    }
    throw new Error(trimmed || 'Auto-stop request failed.')
  }

  return parsed as SetAutoStopResult
}
