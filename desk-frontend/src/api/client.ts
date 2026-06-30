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

export async function listInstances(options?: { infra?: boolean }): Promise<Instance[]> {
  const q = options?.infra ? '?infra=true' : ''
  const res = await fetchWithAuthRetry(`/api/workstations${q}`, { headers: authHeaders() })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(errorMessage(res, text))
  }
  return res.json()
}

export async function startInstance(
  name: string,
  options?: { infra?: boolean },
): Promise<{ instance_id: string; shutdown_at?: string | null }> {
  const q = options?.infra ? '?infra=true' : ''
  const res = await fetchWithAuthRetry(`/api/workstations/${encodeURIComponent(name)}/start${q}`, {
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

export async function stopInstance(name: string, options?: { infra?: boolean }): Promise<{ instance_id: string }> {
  const q = options?.infra ? '?infra=true' : ''
  const res = await fetchWithAuthRetry(`/api/workstations/${encodeURIComponent(name)}/stop${q}`, {
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

export async function killInstance(name: string, options?: { infra?: boolean }): Promise<{ instance_id: string }> {
  const q = options?.infra ? '?infra=true' : ''
  const res = await fetchWithAuthRetry(`/api/workstations/${encodeURIComponent(name)}/kill${q}`, {
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

export interface CostService {
  name: string
  amount: number
  category: string
}

export interface CostMonth {
  month: string
  total: number
  services: CostService[]
}

export interface DailyTotal {
  date: string
  total: number
}

export interface CostSummary {
  months: CostMonth[]
  daily_current_month: DailyTotal[]
}

export async function fetchCosts(): Promise<CostSummary> {
  const res = await fetchWithAuthRetry('/api/costs', { headers: authHeaders() })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(errorMessage(res, text))
  }
  return res.json()
}

export interface ReapResult {
  stopped: { instance_id: string; name: string; shutdown_at: string | null }[]
}

export async function reapWorkstations(): Promise<ReapResult> {
  const res = await fetchWithAuthRetry('/api/workstations/reap', {
    method: 'POST',
    headers: authHeaders(),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(errorMessage(res, text))
  }
  return res.json()
}

export interface CreateWorkstationResult {
  instance_id: string
  name: string
  shutdown_at: string | null
}

export async function createWorkstation(
  name: string,
  instanceType?: string,
): Promise<CreateWorkstationResult> {
  const body: Record<string, string> = { name }
  if (instanceType) body.instance_type = instanceType
  const res = await fetchWithAuthRetry('/api/workstations', {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
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

export interface RunCommandResult {
  command_id: string
  instance_id: string
}

export interface CommandStatus {
  command_id: string
  status: string
  stdout: string
  stderr: string
  exit_code: number | null
}

export async function runCommand(
  name: string,
  script: string,
  user?: string,
  timeout?: number,
): Promise<RunCommandResult> {
  const body: Record<string, unknown> = { script }
  if (user) body.user = user
  if (timeout !== undefined) body.timeout = timeout
  const res = await fetchWithAuthRetry(
    `/api/workstations/${encodeURIComponent(name)}/run`,
    {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  )
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

export async function getCommandStatus(
  name: string,
  commandId: string,
): Promise<CommandStatus> {
  const res = await fetchWithAuthRetry(
    `/api/workstations/${encodeURIComponent(name)}/commands/${encodeURIComponent(commandId)}`,
    { headers: authHeaders() },
  )
  if (!res.ok) {
    const text = await res.text()
    throw new Error(errorMessage(res, text))
  }
  return res.json()
}

// ---- Saved Commands ----

export interface SavedCommandParam {
  name: string
  default?: string
}

export interface SavedCommandItem {
  id: string
  name: string
  script: string
  description: string
  parameters: SavedCommandParam[]
}

export async function listSavedCommands(): Promise<SavedCommandItem[]> {
  const res = await fetchWithAuthRetry('/api/saved-commands', { headers: authHeaders() })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(errorMessage(res, text))
  }
  return res.json()
}

export async function createSavedCommand(
  body: { name: string; script: string; description?: string; parameters?: SavedCommandParam[] },
): Promise<SavedCommandItem> {
  const res = await fetchWithAuthRetry('/api/saved-commands', {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
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

export async function updateSavedCommand(
  id: string,
  body: { name?: string; script?: string; description?: string; parameters?: SavedCommandParam[] },
): Promise<SavedCommandItem> {
  const res = await fetchWithAuthRetry(`/api/saved-commands/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
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

export async function deleteSavedCommand(id: string): Promise<{ deleted: boolean }> {
  const res = await fetchWithAuthRetry(`/api/saved-commands/${encodeURIComponent(id)}`, {
    method: 'DELETE',
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

// ---- Web routes (S3-backed port registry) ----

export interface WebRoutesMapResponse {
  routes: Record<string, number[]>
}

export async function fetchWebRoutesAll(): Promise<WebRoutesMapResponse> {
  const res = await fetchWithAuthRetry('/api/web-routes', { headers: authHeaders() })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(errorMessage(res, text))
  }
  return res.json()
}

export interface WebRouteMutationResult {
  name: string
  ports: number[]
}

export async function addWebRoute(name: string, port: number): Promise<WebRouteMutationResult> {
  const res = await fetchWithAuthRetry(
    `/api/workstations/${encodeURIComponent(name)}/web-routes`,
    {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ port }),
    },
  )
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

export async function removeWebRoute(name: string, port: number): Promise<WebRouteMutationResult> {
  const res = await fetchWithAuthRetry(
    `/api/workstations/${encodeURIComponent(name)}/web-routes/${encodeURIComponent(String(port))}`,
    { method: 'DELETE', headers: authHeaders() },
  )
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
  options: { duration?: string; shutdown_at?: string; clear?: boolean }
): Promise<SetAutoStopResult> {
  let body: Record<string, unknown>
  if (options.clear) {
    body = { clear: true }
  } else if (options.shutdown_at) {
    body = { shutdown_at: options.shutdown_at }
  } else {
    body = { duration: options.duration ?? '4h' }
  }
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

// ---- AMI builds ----

export interface AmiBuildStatusSummary {
  phase: string
  label: string
}

export interface AmiBuildListItem {
  build_id: string
  ami_name: string
  created_at: string | null
  status_summary: AmiBuildStatusSummary
}

export interface AmiBuildListResponse {
  items: AmiBuildListItem[]
  page: number
  page_size: number
  total: number
  total_pages: number
}

export interface AmiBuildRecipeStep {
  index: number
  description: string
}

export interface AmiBuildRecipeDetail {
  label: string
  total_steps?: number
  recipe_complete?: boolean
  blocked?: boolean
  blocked_step_index?: number | null
  blocked_command_id?: string | null
  last_error?: string | null
  in_progress_step_index?: number | null
  in_progress_command_id?: string | null
  next_step_index?: number | null
  steps?: AmiBuildRecipeStep[]
  blocked_step_description?: string
  in_progress_step_description?: string
  next_step_description?: string
  message?: string
  verbose?: {
    command_id?: string
    script?: string
    stdout?: string
    stderr?: string
    status?: string
    exit_code?: number | null
    error?: string
  }
}

export interface AmiBuildDetail {
  build_id: string
  ami_name: string
  created_at: string | null
  archived: boolean
  bucket: string
  s3_prefix: string
  status_summary: AmiBuildStatusSummary
  pipeline_complete: boolean
  builder: {
    instance_id: string | null
    ec2_state: string | null
    ec2_missing: boolean
    ssm_ready: boolean | null
  }
  registered_ami: {
    image_id: string | null
    state: string | null
  }
  test_instance: {
    instance_id: string | null
    ec2_state: string | null
    ec2_missing: boolean
    ssm_ready: boolean | null
  }
  test_failed: boolean
  build_recipe?: AmiBuildRecipeDetail
  test_recipe?: AmiBuildRecipeDetail
  post_build?: Record<string, unknown>
}

export async function listAmiBuilds(options: {
  archived?: boolean
  page?: number
  pageSize?: number
}): Promise<AmiBuildListResponse> {
  const params = new URLSearchParams()
  if (options.archived) params.set('archived', 'true')
  if (options.page) params.set('page', String(options.page))
  if (options.pageSize) params.set('page_size', String(options.pageSize))
  const q = params.toString()
  const res = await fetchWithAuthRetry(`/api/ami-builds${q ? `?${q}` : ''}`, {
    headers: authHeaders(),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(errorMessage(res, text))
  }
  return res.json()
}

export async function getAmiBuildDetail(
  buildId: string,
  options?: { archived?: boolean; verbose?: boolean },
): Promise<AmiBuildDetail> {
  const params = new URLSearchParams()
  if (options?.archived) params.set('archived', 'true')
  if (options?.verbose) params.set('verbose', 'true')
  const q = params.toString()
  const res = await fetchWithAuthRetry(
    `/api/ami-builds/${encodeURIComponent(buildId)}${q ? `?${q}` : ''}`,
    { headers: authHeaders() },
  )
  if (!res.ok) {
    const text = await res.text()
    throw new Error(errorMessage(res, text))
  }
  return res.json()
}

export async function cancelAmiBuild(buildId: string): Promise<{ build_id: string; archived: boolean }> {
  const res = await fetchWithAuthRetry(`/api/ami-builds/${encodeURIComponent(buildId)}/cancel`, {
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
