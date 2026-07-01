/**
 * Injected into port-route HTML responses. Loads a hidden apex iframe that
 * refreshes auth cookies (localStorage refresh_token is apex-only).
 */
(function sessionKeeperBootstrap() {
  const script = document.currentScript as HTMLScriptElement | null
  if (!script?.src) return

  let apex: string
  try {
    apex = new URL(script.src).origin
  } catch {
    return
  }

  const iframe = document.createElement('iframe')
  iframe.setAttribute('aria-hidden', 'true')
  iframe.setAttribute('tabindex', '-1')
  iframe.title = 'Desk session'
  iframe.src = `${apex}/session-bridge.html?mode=keeper`
  Object.assign(iframe.style, {
    position: 'absolute',
    width: '0',
    height: '0',
    border: '0',
    visibility: 'hidden',
    pointerEvents: 'none',
  })

  function mount(): void {
    if (iframe.isConnected) return
    ;(document.body || document.documentElement).appendChild(iframe)
  }

  if (document.body) mount()
  else document.addEventListener('DOMContentLoaded', mount, { once: true })

  window.addEventListener('message', (event) => {
    if (event.origin !== apex) return
    const data = event.data as { type?: string; ok?: boolean } | null
    if (data?.type !== 'desk-session-keeper') return
    if (!data.ok) console.warn('[desk session-keeper] bridge reported refresh failure')
  })
})()
