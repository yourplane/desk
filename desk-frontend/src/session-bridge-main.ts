/**
 * Apex-only session bridge: refreshes Cognito tokens and rewrites auth cookies.
 * Used as a hidden iframe on port-route tabs and as a fallback redirect target.
 */
import {
  getToken,
  goToLogin,
  isAuthEnabled,
  refreshIdToken,
  startSessionKeeper,
} from './auth'

const KEEPER_MESSAGE = 'desk-session-keeper'

function notifyParent(ok: boolean): void {
  if (window.parent === window) return
  window.parent.postMessage({ type: KEEPER_MESSAGE, ok }, '*')
}

async function runBridge(): Promise<void> {
  if (!isAuthEnabled()) return

  const params = new URLSearchParams(window.location.search)
  const returnUrl = params.get('return')
  const mode = params.get('mode')

  let ok = Boolean(getToken())
  if (!ok) ok = await refreshIdToken()

  if (returnUrl) {
    if (ok) {
      window.location.replace(returnUrl)
      return
    }
    void goToLogin()
    return
  }

  if (mode === 'keeper' || window.parent !== window) {
    if (ok) startSessionKeeper()
    notifyParent(ok)
  }
}

void runBridge()
