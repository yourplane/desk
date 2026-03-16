/**
 * Minimal Cognito auth for production. When VITE_COGNITO_* env is set,
 * redirects to hosted UI and stores id_token; API client sends it.
 * Local dev: no env set, no auth.
 */

const CONFIG = {
  userPoolId: import.meta.env.VITE_COGNITO_USER_POOL_ID as string | undefined,
  clientId: import.meta.env.VITE_COGNITO_CLIENT_ID as string | undefined,
  domain: import.meta.env.VITE_COGNITO_DOMAIN as string | undefined,
  redirectUri: import.meta.env.VITE_COGNITO_REDIRECT_URI as string | undefined,
}

const TOKEN_KEY = 'desk_id_token'
const COOKIE_NAME = 'desk_token'
const PKCE_VERIFIER_KEY = 'desk_pkce_verifier'
const PKCE_VERIFIER_COOKIE = 'desk_pkce_verifier'

export function isAuthEnabled(): boolean {
  return !!(CONFIG.userPoolId && CONFIG.clientId && CONFIG.domain && CONFIG.redirectUri)
}

export function getToken(): string | null {
  const fromStorage = sessionStorage.getItem(TOKEN_KEY)
  if (fromStorage) return fromStorage
  const match = document.cookie.match(new RegExp('(^| )' + COOKIE_NAME + '=([^;]+)'))
  return match ? decodeURIComponent(match[2]!) : null
}

function setToken(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token)
  const maxAge = 3600
  const secure = window.location.protocol === 'https:' ? '; Secure' : ''
  document.cookie = `${COOKIE_NAME}=${encodeURIComponent(token)}; path=/; max-age=${maxAge}; SameSite=Lax${secure}`
}

function clearToken(): void {
  sessionStorage.removeItem(TOKEN_KEY)
  sessionStorage.removeItem(PKCE_VERIFIER_KEY)
  document.cookie = `${COOKIE_NAME}=; path=/; max-age=0`
  document.cookie = `${PKCE_VERIFIER_COOKIE}=; path=/; max-age=0`
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
export function ensureAuth(): boolean {
  if (!isAuthEnabled()) return true
  if (getToken()) return true
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
    scope: 'openid email profile',
    redirect_uri: CONFIG.redirectUri!,
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
    redirect_uri: CONFIG.redirectUri!,
    code_verifier: verifier,
  })
  const res = await fetch(`${base}/oauth2/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: body.toString(),
  })
  if (!res.ok) return false
  const data = await res.json()
  const idToken = data.id_token
  if (!idToken) return false
  setToken(idToken)
  clearPkceVerifier()
  return true
}

export function logout(): void {
  clearToken()
  if (isAuthEnabled() && CONFIG.domain) {
    const region = import.meta.env.VITE_COGNITO_REGION || 'us-east-1'
    const base = `https://${CONFIG.domain}.auth.${region}.amazoncognito.com`
    window.location.href = `${base}/logout?client_id=${CONFIG.clientId}&logout_uri=${encodeURIComponent(CONFIG.redirectUri!)}`
  }
}

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
