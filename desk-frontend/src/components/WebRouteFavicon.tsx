import { useEffect, useMemo, useState } from 'react'
import { webRouteFaviconCandidates } from '../pages/workstationUtils'

/** Retry favicon loads periodically so icons appear when routes come up or change. */
export const FAVICON_POLL_INTERVAL_MS = 30_000

let refreshTick = 0
const refreshListeners = new Set<() => void>()

if (typeof window !== 'undefined') {
  window.setInterval(() => {
    refreshTick += 1
    refreshListeners.forEach((listener) => listener())
  }, FAVICON_POLL_INTERVAL_MS)
}

function faviconSrc(url: string, tick: number): string {
  const sep = url.includes('?') ? '&' : '?'
  return `${url}${sep}desk-favicon=${tick}`
}

export function WebRouteFavicon({ baseUrl }: { baseUrl: string }) {
  const [candidateIndex, setCandidateIndex] = useState(0)
  const [loaded, setLoaded] = useState(false)
  const [exhausted, setExhausted] = useState(false)
  const [tick, setTick] = useState(refreshTick)

  const candidates = useMemo(() => webRouteFaviconCandidates(baseUrl), [baseUrl])

  useEffect(() => {
    setCandidateIndex(0)
    setLoaded(false)
    setExhausted(false)
    setTick(refreshTick)
  }, [baseUrl])

  useEffect(() => {
    const onRefresh = () => {
      setCandidateIndex(0)
      setLoaded(false)
      setExhausted(false)
      setTick(refreshTick)
    }
    refreshListeners.add(onRefresh)
    return () => {
      refreshListeners.delete(onRefresh)
    }
  }, [baseUrl])

  const candidate = candidates[candidateIndex]

  return (
    <span className="port-chip-favicon-slot" aria-hidden="true">
      {!exhausted && candidate && (
        <img
          key={`${candidate}-${tick}`}
          className={`port-chip-favicon${loaded ? '' : ' port-chip-favicon--loading'}`}
          src={faviconSrc(candidate, tick)}
          alt=""
          aria-hidden="true"
          loading="lazy"
          onLoad={() => setLoaded(true)}
          onError={() => {
            const next = candidateIndex + 1
            if (next >= candidates.length) {
              setExhausted(true)
              setLoaded(false)
              return
            }
            setCandidateIndex(next)
            setLoaded(false)
          }}
        />
      )}
    </span>
  )
}
