import { useEffect, useMemo, useRef, useState } from 'react'
import { webRouteFaviconCandidates } from '../pages/workstationUtils'
import { resolvePortDisplayGroup, type PortDisplayGroup } from '../portDisplayGroup'
import { probeWebRouteReachability, type RouteReachability } from '../webRouteProbe'

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

const PLACEHOLDER_TOOLTIPS: Record<RouteReachability, string> = {
  live: 'Route is up, no favicon',
  dead: 'Route unreachable',
  unknown: 'Could not verify route status',
}

function FaviconSpinner() {
  return <span className="port-chip-favicon-spinner" aria-hidden="true" />
}

function FaviconPlaceholder({ reachability }: { reachability: RouteReachability }) {
  const className = `port-chip-favicon-placeholder port-chip-favicon-placeholder--${reachability}`
  const title = PLACEHOLDER_TOOLTIPS[reachability]

  if (reachability === 'dead') {
    return (
      <svg className={className} viewBox="0 0 16 16" aria-hidden="true">
        <title>{title}</title>
        <path
          fill="none"
          stroke="currentColor"
          strokeWidth="1.2"
          strokeLinecap="round"
          d="M4 11a4 4 0 0 1 4-6"
        />
        <path
          fill="none"
          stroke="currentColor"
          strokeWidth="1.2"
          strokeLinecap="round"
          d="M12 5a4 4 0 0 1-4 6"
        />
      </svg>
    )
  }

  if (reachability === 'unknown') {
    return (
      <svg className={className} viewBox="0 0 16 16" aria-hidden="true">
        <title>{title}</title>
        <text
          x="8"
          y="12"
          textAnchor="middle"
          fontSize="12"
          fontWeight="600"
          fill="currentColor"
          fontFamily="system-ui, sans-serif"
        >
          ?
        </text>
      </svg>
    )
  }

  return (
    <svg className={className} viewBox="0 0 16 16" aria-hidden="true">
      <title>{title}</title>
      <circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.2" />
      <ellipse cx="8" cy="8" rx="3" ry="6.5" fill="none" stroke="currentColor" strokeWidth="1.2" />
      <path d="M1.5 8h13" stroke="currentColor" strokeWidth="1.2" />
    </svg>
  )
}

export function WebRouteFavicon({
  baseUrl,
  onDisplayGroupChange,
}: {
  baseUrl: string
  onDisplayGroupChange?: (group: PortDisplayGroup) => void
}) {
  const [candidateIndex, setCandidateIndex] = useState(0)
  const [loaded, setLoaded] = useState(false)
  const [exhausted, setExhausted] = useState(false)
  const [reachability, setReachability] = useState<RouteReachability | null>(null)
  const [probeDone, setProbeDone] = useState(false)
  const [tick, setTick] = useState(refreshTick)
  const lastSettledGroupRef = useRef<PortDisplayGroup | null>(null)
  const lastReportedGroupRef = useRef<PortDisplayGroup | null>(null)

  const candidates = useMemo(() => webRouteFaviconCandidates(baseUrl), [baseUrl])

  useEffect(() => {
    setCandidateIndex(0)
    setLoaded(false)
    setExhausted(false)
    setReachability(null)
    setProbeDone(false)
    setTick(refreshTick)
    lastSettledGroupRef.current = null
    lastReportedGroupRef.current = null
  }, [baseUrl])

  useEffect(() => {
    const onRefresh = () => {
      setCandidateIndex(0)
      setLoaded(false)
      setExhausted(false)
      setReachability(null)
      setProbeDone(false)
      setTick(refreshTick)
    }
    refreshListeners.add(onRefresh)
    return () => {
      refreshListeners.delete(onRefresh)
    }
  }, [baseUrl])

  useEffect(() => {
    if (!exhausted) return

    let cancelled = false
    setReachability(null)
    setProbeDone(false)

    void probeWebRouteReachability(baseUrl).then((result) => {
      if (!cancelled) {
        setReachability(result)
        setProbeDone(true)
      }
    })

    return () => {
      cancelled = true
    }
  }, [baseUrl, exhausted, tick])

  const candidate = candidates[candidateIndex]
  const showFavicon = loaded && candidate
  const showPlaceholder = exhausted && probeDone && reachability !== null

  useEffect(() => {
    const group = resolvePortDisplayGroup({
      faviconLoaded: loaded,
      probeDone,
      reachability,
      lastSettledGroup: lastSettledGroupRef.current,
    })

    if (loaded || (probeDone && reachability !== null)) {
      lastSettledGroupRef.current = group
    }

    if (lastReportedGroupRef.current !== group) {
      lastReportedGroupRef.current = group
      onDisplayGroupChange?.(group)
    }
  }, [loaded, probeDone, reachability, onDisplayGroupChange])

  return (
    <span className="port-chip-favicon-slot" aria-hidden="true">
      {showFavicon ? (
        <img
          key={`${candidate}-${tick}`}
          className="port-chip-favicon"
          src={faviconSrc(candidate, tick)}
          alt=""
          aria-hidden="true"
          loading="lazy"
        />
      ) : showPlaceholder ? (
        <FaviconPlaceholder reachability={reachability} />
      ) : (
        <>
          <FaviconSpinner />
          {!exhausted && candidate && (
            <img
              key={`${candidate}-${tick}`}
              className="port-chip-favicon port-chip-favicon--loading"
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
        </>
      )}
    </span>
  )
}
