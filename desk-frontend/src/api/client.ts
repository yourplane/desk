import { getToken } from '../auth'

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

function errorMessage(res: Response, text: string): string {
  if (res.status === 401) {
    return 'Session expired or invalid. Please log in again.'
  }
  return text?.trim() || `Request failed (${res.status})`
}

export async function listInstances(): Promise<Instance[]> {
  const res = await fetch('/api/workstations', { headers: authHeaders() })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(errorMessage(res, text))
  }
  return res.json()
}

export async function startInstance(name: string): Promise<{ instance_id: string; shutdown_at?: string | null }> {
  const res = await fetch(`/api/workstations/${encodeURIComponent(name)}/start`, {
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
  const res = await fetch(`/api/workstations/${encodeURIComponent(name)}/stop`, {
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
  const res = await fetch(`/api/instances/${encodeURIComponent(name)}/kill`, {
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
