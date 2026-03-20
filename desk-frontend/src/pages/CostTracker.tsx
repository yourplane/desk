import { useEffect, useState } from 'react'
import { fetchCosts, type CostMonth, type CostSummary } from '../api/client'
import { isAuthEnabled, logout } from '../auth'

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

export function CostTracker() {
  const [data, setData] = useState<CostSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    fetchCosts()
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }, [])

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

  if (loading) {
    return (
      <div className="cost-tracker">
        {pageHeader}
        <p className="loading">Loading cost data…</p>
      </div>
    )
  }

  if (error) {
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

  const monthlyMax = Math.max(...months.map((m) => m.total), 1)

  const daily = data.daily_current_month
  const dailyMax = Math.max(...daily.map((d) => d.total), 1)

  const totalDelta = previousMonth ? pctChange(currentMonth.total, previousMonth.total) : null

  return (
    <div className="cost-tracker">
      {pageHeader}

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
        <div className="bar-chart">
          {months.map((m) => (
            <div key={m.month} className="bar-col">
              <div className="bar-value">{fmtUsd(m.total)}</div>
              <div className="bar-track">
                <div
                  className="bar-fill"
                  style={{ height: `${Math.max((m.total / monthlyMax) * 100, 2)}%` }}
                />
              </div>
              <div className="bar-label">{monthLabel(m.month)}</div>
            </div>
          ))}
        </div>
      </section>

      {/* Daily breakdown - current month */}
      {daily.length > 0 && (
        <section className="cost-section">
          <h2 className="cost-section-title">Daily — {monthLabel(currentMonth.month)}</h2>
          <div className="daily-chart">
            {daily.map((d) => (
              <div key={d.date} className="daily-col" title={`${dayLabel(d.date)}: ${fmtUsd(d.total)}`}>
                <div className="daily-track">
                  <div
                    className="daily-fill"
                    style={{ height: `${Math.max((d.total / dailyMax) * 100, 2)}%` }}
                  />
                </div>
                <div className="daily-label">{new Date(d.date + 'T00:00:00').getDate()}</div>
              </div>
            ))}
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
