function formatDataAge(updatedAtMs: number): string {
  const s = Math.floor((Date.now() - updatedAtMs) / 1000)
  if (s < 10) return 'just now'
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} min ago`
  const h = Math.floor(m / 60)
  if (h < 48) return `${h} hr ago`
  const d = Math.floor(h / 24)
  return `${d} days ago`
}

export interface DataFreshnessBarProps {
  /** Short noun phrase, e.g. "workstation list" */
  resourceLabel: string
  /** Query `dataUpdatedAt` (epoch ms). Omit when unknown. */
  dataUpdatedAt?: number
  isFetching: boolean
  onRefresh: () => void
}

/**
 * Non-blocking freshness cue: cached data stays visible while refetching;
 * this bar explains age and offers an explicit refresh.
 */
export function DataFreshnessBar({
  resourceLabel,
  dataUpdatedAt,
  isFetching,
  onRefresh,
}: DataFreshnessBarProps) {
  if (dataUpdatedAt === undefined || dataUpdatedAt <= 0) return null

  const age = formatDataAge(dataUpdatedAt)

  return (
    <div
      className={`data-freshness-bar${isFetching ? ' data-freshness-bar--fetching' : ''}`}
      role="status"
      aria-live="polite"
    >
      <span className="data-freshness-bar-text">
        <span className="data-freshness-bar-label">{resourceLabel}</span>
        {' · '}
        updated {age}
        {isFetching ? ' · refreshing…' : ''}
      </span>
      <button
        type="button"
        className="btn btn-secondary btn-sm data-freshness-bar-refresh"
        onClick={() => void onRefresh()}
        disabled={isFetching}
      >
        Refresh now
      </button>
    </div>
  )
}
