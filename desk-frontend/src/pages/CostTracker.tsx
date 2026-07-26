import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { fetchCosts, type CostMonth, type HourlyCost } from '../api/client'
import { DataFreshnessBar } from '../DataFreshnessBar'
import { useAdaptiveRefetchInterval } from '../hooks/useAdaptiveRefetchInterval'
import { queryKeys } from '../queryKeys'
import { isAuthEnabled, logout } from '../auth'

const POLL_INTERVAL_MS = 5 * 60 * 1000
const BACKGROUND_POLL_INTERVAL_MS = 15 * 60 * 1000

function fmtUsd(amount: number): string {
  return `$${amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function pctChange(current: number, previous: number): { text: string; className: string } {
  if (previous === 0 && current === 0) return { text: '—', className: '' }
  if (previous === 0) return { text: '+∞', className: 'delta-up' }
  const pct = ((current - previous) / previous) * 100
  const sign = pct > 0 ? '+' : ''
  const cls = pct > 1 ? 'delta-up' : pct < -1 ? 'delta-down' : ''
  return { text: `${sign}${pct.toFixed(1)}%`, className: cls }
}

function monthLabel(month: string): string {
  const [y, m] = month.split('-')
  const date = new Date(Number(y), Number(m) - 1)
  return date.toLocaleDateString(undefined, { month: 'short', year: 'numeric' })
}

function dayLabel(dateStr: string): string {
  const d = new Date(dateStr + 'T00:00:00')
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function utcTodayString(): string {
  return new Date().toISOString().slice(0, 10)
}

function computeMonthlyScale(totals: number[]): { yMax: number; isOutlier: (i: number) => boolean } {
  if (totals.length === 0) return { yMax: 1, isOutlier: () => false }

  const sorted = [...totals].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  const median =
    sorted.length % 2 === 0 ? (sorted[mid - 1]! + sorted[mid]!) / 2 : sorted[mid]!
  const threshold = median * 2

  const isOutlier = (i: number) => totals[i]! > threshold && totals[i]! > 0
  const nonOutlierTotals = totals.filter((_, i) => !isOutlier(i))
  const yMax = Math.max(...(nonOutlierTotals.length > 0 ? nonOutlierTotals : totals), 1)

  return { yMax, isOutlier }
}

interface CategoryRow {
  category: string
  currentAmount: number
  previousAmount: number
}

function buildCategoryRows(current: CostMonth | undefined, previous: CostMonth | undefined): CategoryRow[] {
  const catMap: Record<string, { cur: number; prev: number }> = {}

  for (const s of current?.services ?? []) {
    if (!catMap[s.category]) catMap[s.category] = { cur: 0, prev: 0 }
    catMap[s.category].cur += s.amount
  }
  for (const s of previous?.services ?? []) {
    if (!catMap[s.category]) catMap[s.category] = { cur: 0, prev: 0 }
    catMap[s.category].prev += s.amount
  }

  return Object.entries(catMap)
    .map(([cat, v]) => ({
      category: cat,
      currentAmount: Math.round(v.cur * 100) / 100,
      previousAmount: Math.round(v.prev * 100) / 100,
    }))
    .sort((a, b) => b.currentAmount - a.currentAmount)
}

interface ServiceRow {
  name: string
  category: string
  currentAmount: number
  previousAmount: number
}

function buildServiceRows(current: CostMonth | undefined, previous: CostMonth | undefined): ServiceRow[] {
  const svcMap: Record<string, { cat: string; cur: number; prev: number }> = {}

  for (const s of current?.services ?? []) {
    if (!svcMap[s.name]) svcMap[s.name] = { cat: s.category, cur: 0, prev: 0 }
    svcMap[s.name].cur += s.amount
  }
  for (const s of previous?.services ?? []) {
    if (!svcMap[s.name]) svcMap[s.name] = { cat: s.category, cur: 0, prev: 0 }
    svcMap[s.name].prev += s.amount
  }

  return Object.entries(svcMap)
    .map(([name, v]) => ({
      name,
      category: v.cat,
      currentAmount: Math.round(v.cur * 100) / 100,
      previousAmount: Math.round(v.prev * 100) / 100,
    }))
    .sort((a, b) => b.currentAmount - a.currentAmount)
}

function hourLabel(hour: number): string {
  return `${hour.toString().padStart(2, '0')}:00`
}

export function CostTracker() {
  const pollIntervalMs = useAdaptiveRefetchInterval(POLL_INTERVAL_MS, BACKGROUND_POLL_INTERVAL_MS)
  const [selectedDay, setSelectedDay] = useState<string>(() => utcTodayString())
  const [selectedHour, setSelectedHour] = useState<number | null>(null)

  const costsQuery = useQuery({
    queryKey: queryKeys.costs,
    queryFn: fetchCosts,
    staleTime: 60_000,
    refetchInterval: pollIntervalMs,
  })

  const data = costsQuery.data
  const loading = costsQuery.isPending && costsQuery.data === undefined
  const error =
    costsQuery.isError && costsQuery.data === undefined
      ? costsQuery.error instanceof Error
        ? costsQuery.error.message
        : String(costsQuery.error)
      : null
  const refreshError =
    costsQuery.isError && costsQuery.data !== undefined
      ? 'Could not refresh costs. Showing last successful load.'
      : null

  const pageHeader = (
    <div className="page-header">
      <h1 className="page-title">AWS Costs</h1>
      {isAuthEnabled() && (
        <button type="button" className="btn btn-secondary" onClick={() => logout()}>
          Log out
        </button>
      )}
    </div>
  )

  const monthlyScale = useMemo(() => {
    if (!data?.months.length) return { yMax: 1, isOutlier: () => false }
    return computeMonthlyScale(data.months.map((m) => m.total))
  }, [data?.months])

  const hourlyMax = useMemo(() => {
    const hourly = data?.today_utc?.hourly ?? []
    const amounts = hourly.filter((h) => h.status !== 'future').map((h) => h.total)
    return Math.max(...amounts, 0.01)
  }, [data?.today_utc?.hourly])

  if (loading) {
    return (
      <div className="cost-tracker">
        {pageHeader}
        <p className="loading">Loading cost data…</p>
      </div>
    )
  }

  if (error && !data) {
    return (
      <div className="cost-tracker">
        {pageHeader}
        <p className="error-message" role="alert">{error}</p>
      </div>
    )
  }

  if (!data || data.months.length === 0) {
    return (
      <div className="cost-tracker">
        {pageHeader}
        <p className="loading">No cost data available.</p>
      </div>
    )
  }

  const months = data.months
  const currentMonth = months[months.length - 1]!
  const previousMonth = months.length >= 2 ? months[months.length - 2]! : undefined

  const categoryRows = buildCategoryRows(currentMonth, previousMonth)
  const serviceRows = buildServiceRows(currentMonth, previousMonth)

  const daily = data.daily_current_month
  const dailyMax = Math.max(...daily.map((d) => d.total), 1)

  const totalDelta = previousMonth ? pctChange(currentMonth.total, previousMonth.total) : null
  const todayUtc = data.today_utc

  return (
    <div className="cost-tracker">
      {pageHeader}
      <DataFreshnessBar
        resourceLabel="Cost data"
        dataUpdatedAt={costsQuery.dataUpdatedAt}
        isFetching={costsQuery.isFetching}
        onRefresh={() => void costsQuery.refetch()}
      />
      {refreshError && (
        <p className="refresh-error" role="status">{refreshError}</p>
      )}

      {/* Summary cards */}
      <div className="cost-cards">
        <div className="cost-card">
          <div className="cost-card-label">Current month</div>
          <div className="cost-card-value">{fmtUsd(currentMonth.total)}</div>
          <div className="cost-card-sub">{monthLabel(currentMonth.month)}</div>
        </div>
        {previousMonth && (
          <div className="cost-card">
            <div className="cost-card-label">Previous month</div>
            <div className="cost-card-value">{fmtUsd(previousMonth.total)}</div>
            <div className="cost-card-sub">{monthLabel(previousMonth.month)}</div>
          </div>
        )}
        {totalDelta && (
          <div className="cost-card">
            <div className="cost-card-label">Month-over-month</div>
            <div className={`cost-card-value ${totalDelta.className}`}>{totalDelta.text}</div>
            <div className="cost-card-sub">vs. previous month</div>
          </div>
        )}
      </div>

      {/* Monthly trend */}
      <section className="cost-section">
        <h2 className="cost-section-title">Monthly trend</h2>
        <div className="chart-scroll-wrap chart-scroll-wrap--monthly">
          <div className="bar-chart">
            {months.map((m, i) => {
              const outlier = monthlyScale.isOutlier(i)
              const barHeight = Math.max((Math.min(m.total, monthlyScale.yMax) / monthlyScale.yMax) * 100, 2)
              return (
                <div key={m.month} className={`bar-col${outlier ? ' bar-col--clipped' : ''}`}>
                  <div className={`bar-value${outlier ? ' bar-value--clipped' : ''}`}>
                    {outlier ? `↑ ${fmtUsd(m.total)}` : fmtUsd(m.total)}
                  </div>
                  <div className="bar-track">
                    <div className="bar-fill" style={{ height: `${barHeight}%` }} />
                  </div>
                  <div className="bar-label">{monthLabel(m.month)}</div>
                </div>
              )
            })}
          </div>
        </div>
      </section>

      {/* Daily breakdown - current month */}
      {daily.length > 0 && (
        <section className="cost-section">
          <h2 className="cost-section-title">Daily — {monthLabel(currentMonth.month)}</h2>
          <div className="chart-scroll-wrap">
            <div className="daily-chart">
              {daily.map((d) => {
                const selected = d.date === selectedDay
                return (
                  <button
                    key={d.date}
                    type="button"
                    className={`daily-col${selected ? ' daily-col--selected' : ''}`}
                    onClick={() => setSelectedDay(d.date)}
                    aria-pressed={selected}
                    aria-label={`${dayLabel(d.date)}: ${fmtUsd(d.total)}`}
                  >
                    {selected && <div className="bar-value">{fmtUsd(d.total)}</div>}
                    <div className="daily-track">
                      <div
                        className="daily-fill"
                        style={{ height: `${Math.max((d.total / dailyMax) * 100, 2)}%` }}
                      />
                    </div>
                    <div className="daily-label">{new Date(d.date + 'T00:00:00').getDate()}</div>
                  </button>
                )
              })}
            </div>
          </div>
        </section>
      )}

      {/* Today (UTC) — hourly + projection */}
      {todayUtc && (
        <section className="cost-section cost-section--today">
          <h2 className="cost-section-title">Today (UTC)</h2>

          <div className="chart-scroll-wrap">
            <div className="hourly-chart">
              {todayUtc.hourly.map((h: HourlyCost) => {
                const selected = h.hour === selectedHour
                const isFuture = h.status === 'future'
                const isPartial = h.status === 'partial'
                const barHeight =
                  !isFuture && h.total > 0
                    ? Math.max((h.total / hourlyMax) * 100, 2)
                    : 0
                return (
                  <button
                    key={h.hour}
                    type="button"
                    className={`hourly-col${selected ? ' hourly-col--selected' : ''}${isFuture ? ' hourly-col--future' : ''}${isPartial ? ' hourly-col--partial' : ''}`}
                    onClick={() => !isFuture && setSelectedHour(h.hour)}
                    disabled={isFuture}
                    aria-pressed={selected}
                    aria-label={
                      isFuture
                        ? `${hourLabel(h.hour)}: future`
                        : `${hourLabel(h.hour)}: ${fmtUsd(h.total)}${isPartial ? ' (in progress)' : ''}`
                    }
                  >
                    {selected && !isFuture && <div className="bar-value">{fmtUsd(h.total)}</div>}
                    <div className="hourly-track">
                      {!isFuture && (
                        <div
                          className="hourly-fill"
                          style={{ height: `${barHeight}%` }}
                        />
                      )}
                    </div>
                    <div className="hourly-label">{h.hour}</div>
                  </button>
                )
              })}
            </div>
          </div>

          <div className="today-projection">
            <div className="today-projection-row">
              <span className="today-projection-label">Spend so far</span>
              <span className="today-projection-value">{fmtUsd(todayUtc.spend_so_far)}</span>
            </div>
            {todayUtc.projection_available && todayUtc.projected_total != null ? (
              <div className="today-projection-row">
                <span className="today-projection-label">Projected total</span>
                <span className="today-projection-value today-projection-value--estimate">
                  ~{fmtUsd(todayUtc.projected_total)}
                </span>
              </div>
            ) : (
              <p className="today-projection-note">
                Projection available after the first full hour
              </p>
            )}
          </div>
        </section>
      )}

      {/* Category breakdown */}
      <section className="cost-section">
        <h2 className="cost-section-title">By category</h2>
        <div className="table-wrap">
          <table className="cost-table">
            <thead>
              <tr>
                <th>Category</th>
                <th className="num-col">{currentMonth ? monthLabel(currentMonth.month) : 'Current'}</th>
                {previousMonth && <th className="num-col">{monthLabel(previousMonth.month)}</th>}
                {previousMonth && <th className="num-col">Change</th>}
              </tr>
            </thead>
            <tbody>
              {categoryRows.map((row) => {
                const delta = previousMonth ? pctChange(row.currentAmount, row.previousAmount) : null
                return (
                  <tr key={row.category}>
                    <td className="name">{row.category}</td>
                    <td className="num-col">{fmtUsd(row.currentAmount)}</td>
                    {previousMonth && <td className="num-col">{fmtUsd(row.previousAmount)}</td>}
                    {delta && <td className={`num-col ${delta.className}`}>{delta.text}</td>}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* Service breakdown */}
      <section className="cost-section">
        <h2 className="cost-section-title">By service</h2>
        <div className="table-wrap">
          <table className="cost-table">
            <thead>
              <tr>
                <th>Service</th>
                <th>Category</th>
                <th className="num-col">{currentMonth ? monthLabel(currentMonth.month) : 'Current'}</th>
                {previousMonth && <th className="num-col">{monthLabel(previousMonth.month)}</th>}
                {previousMonth && <th className="num-col">Change</th>}
              </tr>
            </thead>
            <tbody>
              {serviceRows.map((row) => {
                const delta = previousMonth ? pctChange(row.currentAmount, row.previousAmount) : null
                return (
                  <tr key={row.name}>
                    <td className="name">{row.name}</td>
                    <td className="category-label">{row.category}</td>
                    <td className="num-col">{fmtUsd(row.currentAmount)}</td>
                    {previousMonth && <td className="num-col">{fmtUsd(row.previousAmount)}</td>}
                    {delta && <td className={`num-col ${delta.className}`}>{delta.text}</td>}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
