export interface Instance {
  instance_id: string
  name: string
  state: string
  shutdown_at: string | null
}

export async function listInstances(): Promise<Instance[]> {
  const res = await fetch('/api/instances')
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `HTTP ${res.status}`)
  }
  return res.json()
}

export async function startInstance(name: string): Promise<{ instance_id: string }> {
  const res = await fetch(`/api/instances/${encodeURIComponent(name)}/start`, {
    method: 'POST',
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
  const res = await fetch(`/api/instances/${encodeURIComponent(name)}/stop`, {
    method: 'POST',
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
