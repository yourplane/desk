import { useEffect, useState } from 'react'

/** Faster polling when the tab is visible; slower when backgrounded (fewer AWS calls). */
export function useAdaptiveRefetchInterval(fastMs: number, slowMs: number): number {
  const [intervalMs, setIntervalMs] = useState(() =>
    typeof document !== 'undefined' && document.visibilityState === 'hidden' ? slowMs : fastMs,
  )

  useEffect(() => {
    const update = () =>
      setIntervalMs(document.visibilityState === 'hidden' ? slowMs : fastMs)
    update()
    document.addEventListener('visibilitychange', update)
    return () => document.removeEventListener('visibilitychange', update)
  }, [fastMs, slowMs])

  return intervalMs
}
