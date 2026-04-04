import { useState } from 'react'
import { isAuthEnabled, logout } from '../auth'
import { InstanceList } from './InstanceList'
import { WebRoutesTab } from './WebRoutesTab'

type WorkstationTab = 'overview' | 'web-routes'

export function WorkstationsPage() {
  const [tab, setTab] = useState<WorkstationTab>('overview')

  const pageHeader = (
    <div className="page-header">
      <h1 className="page-title">Workstations</h1>
      {isAuthEnabled() && (
        <div className="page-header-actions">
          <button type="button" className="btn btn-secondary" onClick={() => logout()}>
            Log out
          </button>
        </div>
      )}
    </div>
  )

  return (
    <div className="instance-list">
      {pageHeader}
      <div className="workstations-subnav" role="tablist" aria-label="Workstation sections">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'overview'}
          id="workstations-tab-overview"
          className={`workstations-subnav-tab${tab === 'overview' ? ' workstations-subnav-tab--active' : ''}`}
          onClick={() => setTab('overview')}
        >
          Overview
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'web-routes'}
          id="workstations-tab-web-routes"
          className={`workstations-subnav-tab${tab === 'web-routes' ? ' workstations-subnav-tab--active' : ''}`}
          onClick={() => setTab('web-routes')}
        >
          Web routes
        </button>
      </div>
      <div
        className="workstations-tab-panel"
        role="tabpanel"
        aria-labelledby={tab === 'overview' ? 'workstations-tab-overview' : 'workstations-tab-web-routes'}
      >
        {tab === 'overview' && <InstanceList />}
        {tab === 'web-routes' && <WebRoutesTab />}
      </div>
    </div>
  )
}
