/**
 * Minimal Cognito auth for production.
 *
 * When VITE_COGNITO_* env is set:
 * - redirects to hosted UI (OAuth code + PKCE)
 * - stores `id_token` in sessionStorage + a short-lived cookie
 * - stores `refresh_token` in localStorage so we can renew the session
 * - API calls use the id_token, and we refresh on 401s
 *
 * Local dev: no env set, no auth.
 */

const CONFIG = {
  userPoolId: import.meta.env.VITE_COGNITO_USER_POOL_ID as string | undefined,
  clientId: import.meta.env.VITE_COGNITO_CLIENT_ID as string | undefined,
  domain: import.meta.env.VITE_COGNITO_DOMAIN as string | undefined,
}

/** OAuth redirect_uri: optional env override; otherwise current origin (supports CloudFront + custom domain). */
function getRedirectUri(): string {
  const fromEnv = import.meta.env.VITE_COGNITO_REDIRECT_URI as string | undefined
  if (fromEnv) return fromEnv
  if (typeof window !== 'undefined') return window.location.origin
  return ''
}

const TOKEN_KEY = 'desk_id_token'
const REFRESH_TOKEN_KEY = 'desk_refresh_token'
const COOKIE_NAME = 'desk_token'
const PKCE_VERIFIER_KEY = 'desk_pkce_verifier'
const PKCE_VERIFIER_COOKIE = 'desk_pkce_verifier'

/** Optional `Domain=` for desk_token (e.g. .desk.example.com) so subdomains receive the cookie. */
function cookieDomainAttr(): string {
  const d = (import.meta.env.VITE_COOKIE_DOMAIN as string | undefined)?.trim()
  if (!d) return ''
  return `; Domain=${d}`
}

export function isAuthEnabled(): boolean {
  if (!CONFIG.userPoolId || !CONFIG.clientId || !CONFIG.domain) return false
  if (import.meta.env.VITE_COGNITO_REDIRECT_URI) return true
  if (typeof window !== 'undefined') return true
  return false
}

export function getToken(): string | null {
  const fromStorage = sessionStorage.getItem(TOKEN_KEY)
  const fromCookie = (() => {
    const match = document.cookie.match(new RegExp('(^| )' + COOKIE_NAME + '=([^;]+)'))
    return match ? decodeURIComponent(match[2]!) : null
  })()
  const token = fromStorage || fromCookie
  if (!token) return null
  // If we can tell it's expired, treat it as missing so callers can refresh.
  return isIdTokenExpired(token) ? null : token
}

function setIdToken(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token)
  const maxAge = 3600
  const secure = window.location.protocol === 'https:' ? '; Secure' : ''
  document.cookie = `${COOKIE_NAME}=${encodeURIComponent(token)}; path=/; max-age=${maxAge}; SameSite=Lax${secure}${cookieDomainAttr()}`
}

function getRefreshToken(): string | null {
  try {
    return localStorage.getItem(REFRESH_TOKEN_KEY)
  } catch {
    // localStorage may be blocked (e.g. 3rd party settings); fail closed.
    return null
  }
}

function setRefreshToken(token: string): void {
  try {
    localStorage.setItem(REFRESH_TOKEN_KEY, token)
  } catch {
    // If we can't persist refresh tokens, we still keep working for the current tab.
  }
}

function clearToken(): void {
  sessionStorage.removeItem(TOKEN_KEY)
  try {
    localStorage.removeItem(REFRESH_TOKEN_KEY)
  } catch {
    // ignore
  }
  sessionStorage.removeItem(PKCE_VERIFIER_KEY)
  document.cookie = `${COOKIE_NAME}=; path=/; max-age=0${cookieDomainAttr()}`
  document.cookie = `${PKCE_VERIFIER_COOKIE}=; path=/; max-age=0`
}

/** Clear stored tokens and PKCE state (e.g. before retrying login after an OAuth error). */
export function clearAuthTokens(): void {
  clearToken()
}

/**
 * Cognito redirects to the app with `?error=...&error_description=...` when the authorize
 * request fails (e.g. invalid_scope). No `code` is present.
 */
export function readOAuthAuthorizeError(): { error: string; errorDescription: string } | null {
  const params = new URLSearchParams(window.location.search)
  const err = params.get('error')
  if (!err) return null
  return { error: err, errorDescription: params.get('error_description') ?? '' }
}

function parseJwtPayload(token: string): unknown | null {
  const parts = token.split('.')
  if (parts.length < 2) return null
  const payloadB64Url = parts[1]!
  const payloadB64 = payloadB64Url.replace(/-/g, '+').replace(/_/g, '/')
  // Handle missing padding.
  const padded = payloadB64.padEnd(payloadB64.length + ((4 - (payloadB64.length % 4)) % 4), '=')
  try {
    const json = atob(padded)
    return JSON.parse(json)
  } catch {
    return null
  }
}

function isIdTokenExpired(token: string): boolean {
  const payload = parseJwtPayload(token) as any
  const exp = typeof payload?.exp === 'number' ? payload.exp : null
  if (!exp) return true
  const nowMs = Date.now()
  const expMs = exp * 1000
  // Small buffer so we don't race expiry.
  return expMs <= nowMs + 60_000
}

function getPkceVerifier(): string | null {
  const fromStorage = sessionStorage.getItem(PKCE_VERIFIER_KEY)
  if (fromStorage) return fromStorage
  const match = document.cookie.match(new RegExp('(^| )' + PKCE_VERIFIER_COOKIE.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '=([^;]+)'))
  return match ? decodeURIComponent(match[2]!) : null
}

function clearPkceVerifier(): void {
  sessionStorage.removeItem(PKCE_VERIFIER_KEY)
  document.cookie = `${PKCE_VERIFIER_COOKIE}=; path=/; max-age=0`
}

/** Redirect to Cognito hosted UI if auth is enabled and no token. Call once at app load. */
let refreshInFlight: Promise<boolean> | null = null

async function refreshIdToken(): Promise<boolean> {
  if (!isAuthEnabled()) return false
  const refreshToken = getRefreshToken()
  if (!refreshToken) return false
  if (refreshInFlight) return refreshInFlight

  refreshInFlight = (async () => {
    const region = import.meta.env.VITE_COGNITO_REGION || 'us-east-1'
    const base = `https://${CONFIG.domain}.auth.${region}.amazoncognito.com`
    const body = new URLSearchParams({
      grant_type: 'refresh_token',
      client_id: CONFIG.clientId!,
      refresh_token: refreshToken,
    })
    const res = await fetch(`${base}/oauth2/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    })
    if (!res.ok) {
      clearToken()
      return false
    }
    const data = await res.json()
    const idToken = data.id_token as string | undefined
    if (!idToken) {
      clearToken()
      return false
    }
    setIdToken(idToken)
    const newRefresh = data.refresh_token as string | undefined
    if (newRefresh) setRefreshToken(newRefresh)
    return true
  })()

  try {
    return await refreshInFlight
  } finally {
    refreshInFlight = null
  }
}

export async function ensureAuth(): Promise<boolean> {
  if (!isAuthEnabled()) return true
  if (getToken()) return true
  if (await refreshIdToken()) return true
  goToLogin()
  return false
}

/** Navigate to Cognito hosted UI login (e.g. after sign-in failure or no session). */
export async function goToLogin(): Promise<void> {
  const verifier = randomString(43)
  const challenge = await sha256Base64Url(verifier)
  sessionStorage.setItem(PKCE_VERIFIER_KEY, verifier)
  const secure = window.location.protocol === 'https:' ? '; Secure' : ''
  document.cookie = `${PKCE_VERIFIER_COOKIE}=${encodeURIComponent(verifier)}; path=/; max-age=600; SameSite=Lax${secure}`
  const base = `https://${CONFIG.domain}.auth.${import.meta.env.VITE_COGNITO_REGION || 'us-east-1'}.amazoncognito.com`
  const params = new URLSearchParams({
    client_id: CONFIG.clientId!,
    response_type: 'code',
    // Must match Cognito app client AllowedOAuthScopes (see desk-infra cloudformation).
    // Refresh tokens still come from the authorization code exchange when the client allows them.
    scope: 'openid email profile',
    redirect_uri: getRedirectUri(),
    code_challenge: challenge,
    code_challenge_method: 'S256',
  })
  window.location.href = `${base}/oauth2/authorize?${params}`
}

/** Handle callback: exchange code for token and store. Returns true if we got a token. */
export async function handleCallback(): Promise<boolean> {
  if (!isAuthEnabled()) return true
  const params = new URLSearchParams(window.location.search)
  const code = params.get('code')
  const verifier = getPkceVerifier()
  if (!code || !verifier) return false
  const region = import.meta.env.VITE_COGNITO_REGION || 'us-east-1'
  const base = `https://${CONFIG.domain}.auth.${region}.amazoncognito.com`
  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    client_id: CONFIG.clientId!,
    code,
    redirect_uri: getRedirectUri(),
    code_verifier: verifier,
  })
  const res = await fetch(`${base}/oauth2/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: body.toString(),
  })
  if (!res.ok) return false
  const data = await res.json()
  const idToken = data.id_token as string | undefined
  if (!idToken) return false
  setIdToken(idToken)
  const refreshToken = data.refresh_token as string | undefined
  if (refreshToken) setRefreshToken(refreshToken)
  clearPkceVerifier()
  return true
}

export function logout(): void {
  clearToken()
  if (isAuthEnabled() && CONFIG.domain) {
    const region = import.meta.env.VITE_COGNITO_REGION || 'us-east-1'
    const base = `https://${CONFIG.domain}.auth.${region}.amazoncognito.com`
    window.location.href = `${base}/logout?client_id=${CONFIG.clientId}&logout_uri=${encodeURIComponent(getRedirectUri())}`
  }
}

export { refreshIdToken }

function randomString(length: number): string {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~'
  let s = ''
  const bytes = new Uint8Array(length)
  crypto.getRandomValues(bytes)
  for (let i = 0; i < length; i++) s += chars[bytes[i]! % chars.length]
  return s
}

async function sha256Base64Url(input: string): Promise<string> {
  const buf = new TextEncoder().encode(input)
  const hash = await crypto.subtle.digest('SHA-256', buf)
  return btoa(String.fromCharCode(...new Uint8Array(hash)))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '')
}
