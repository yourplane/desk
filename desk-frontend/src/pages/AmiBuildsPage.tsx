import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { Fragment, useState } from 'react'
import {
  cancelAmiBuild,
  getAmiBuildDetail,
  listAmiBuilds,
  type AmiBuildDetail,
  type AmiBuildRecipeDetail,
} from '../api/client'
import { DataFreshnessBar } from '../DataFreshnessBar'
import { useAdaptiveRefetchInterval } from '../hooks/useAdaptiveRefetchInterval'
import { queryKeys } from '../queryKeys'

const POLL_INTERVAL_MS = 10_000
const BACKGROUND_POLL_INTERVAL_MS = 5 * 60 * 1000
const PAGE_SIZE = 20

function phaseClass(phase: string): string {
  switch (phase) {
    case 'complete':
      return 'ami-phase ami-phase--complete'
    case 'failed':
      return 'ami-phase ami-phase--failed'
    case 'pending':
      return 'ami-phase ami-phase--pending'
    default:
      return 'ami-phase ami-phase--active'
  }
}

function formatCreated(iso: string | null): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return iso
    return d.toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
  } catch {
    return iso
  }
}

function RecipeSection({ title, recipe }: { title: string; recipe: AmiBuildRecipeDetail }) {
  const [showVerbose, setShowVerbose] = useState(false)
  const verbose = recipe.verbose

  return (
    <div className="ami-detail-section">
      <h3 className="ami-detail-section-title">{title}</h3>
      {recipe.message && <p className="ami-detail-message">{recipe.message}</p>}
      {recipe.total_steps !== undefined && (
        <p className="ami-detail-meta">Steps in config: {recipe.total_steps}</p>
      )}
      {recipe.recipe_complete && <p className="ami-detail-ok">All steps completed successfully.</p>}
      {recipe.blocked && recipe.blocked_step_index !== undefined && recipe.blocked_step_index !== null && (
        <p className="ami-detail-error" role="alert">
          Failed at step {recipe.blocked_step_index}
          {recipe.blocked_step_description ? `: ${recipe.blocked_step_description}` : ''}
        </p>
      )}
      {recipe.last_error && (
        <p className="ami-detail-error-meta">{recipe.last_error}</p>
      )}
      {recipe.in_progress_step_index !== undefined && recipe.in_progress_step_index !== null && (
        <p className="ami-detail-meta">
          Step {recipe.in_progress_step_index} in progress
          {recipe.in_progress_step_description ? `: ${recipe.in_progress_step_description}` : ''}
        </p>
      )}
      {recipe.next_step_index !== undefined && recipe.next_step_index !== null && (
        <p className="ami-detail-meta">
          Next: step {recipe.next_step_index}
          {recipe.next_step_description ? ` — ${recipe.next_step_description}` : ''}
        </p>
      )}
      {recipe.steps && recipe.steps.length > 0 && (
        <ol className="ami-step-list">
          {recipe.steps.map((s) => (
            <li key={s.index}>{s.description}</li>
          ))}
        </ol>
      )}
      {verbose && (
        <div className="ami-verbose-block">
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => setShowVerbose((v) => !v)}
          >
            {showVerbose ? 'Hide' : 'Show'} SSM output
          </button>
          {showVerbose && (
            <div className="ami-verbose-output">
              {verbose.error && <p className="ami-detail-error">{verbose.error}</p>}
              {verbose.script && (
                <>
                  <p className="ami-verbose-label">Command script</p>
                  <pre className="ami-verbose-pre">{verbose.script}</pre>
                </>
              )}
              <p className="ami-verbose-label">stdout</p>
              <pre className="ami-verbose-pre">{verbose.stdout || '(empty)'}</pre>
              <p className="ami-verbose-label">stderr</p>
              <pre className="ami-verbose-pre ami-verbose-pre--stderr">{verbose.stderr || '(empty)'}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function BuildDetailPanel({ detail }: { detail: AmiBuildDetail }) {
  return (
    <div className="ami-build-detail">
      <div className="ami-detail-grid">
        <div>
          <span className="ami-detail-label">S3</span>
          <code className="ami-detail-code">s3://{detail.bucket}/{detail.s3_prefix}</code>
        </div>
        <div>
          <span className="ami-detail-label">Builder</span>
          <span>
            {detail.builder.instance_id ?? '(not created)'}
            {detail.builder.ec2_state ? ` · ${detail.builder.ec2_state}` : ''}
            {detail.builder.ssm_ready === true ? ' · SSM ready' : detail.builder.ssm_ready === false ? ' · SSM not ready' : ''}
          </span>
        </div>
        {detail.registered_ami.image_id && (
          <div>
            <span className="ami-detail-label">Registered AMI</span>
            <span>
              {detail.registered_ami.image_id}
              {detail.registered_ami.state ? ` (${detail.registered_ami.state})` : ''}
            </span>
          </div>
        )}
        {detail.test_instance.instance_id && (
          <div>
            <span className="ami-detail-label">Test instance</span>
            <span>
              {detail.test_instance.instance_id}
              {detail.test_instance.ec2_state ? ` · ${detail.test_instance.ec2_state}` : ''}
            </span>
          </div>
        )}
      </div>
      {detail.build_recipe && <RecipeSection title="Build recipe" recipe={detail.build_recipe} />}
      {detail.test_recipe && <RecipeSection title="Test recipe" recipe={detail.test_recipe} />}
      {detail.post_build && (
        <div className="ami-detail-section">
          <h3 className="ami-detail-section-title">Post-build</h3>
          <p>{String(detail.post_build.message ?? JSON.stringify(detail.post_build))}</p>
        </div>
      )}
    </div>
  )
}

export function AmiBuildsPage() {
  const queryClient = useQueryClient()
  const pollIntervalMs = useAdaptiveRefetchInterval(POLL_INTERVAL_MS, BACKGROUND_POLL_INTERVAL_MS)
  const [archived, setArchived] = useState(false)
  const [page, setPage] = useState(1)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [cancelTarget, setCancelTarget] = useState<string | null>(null)
  const [acting, setActing] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  const listQuery = useQuery({
    queryKey: queryKeys.amiBuilds(archived, page, PAGE_SIZE),
    queryFn: () => listAmiBuilds({ archived, page, pageSize: PAGE_SIZE }),
    placeholderData: keepPreviousData,
    staleTime: 5_000,
    refetchInterval: acting ? false : pollIntervalMs,
  })

  const detailQuery = useQuery({
    queryKey: queryKeys.amiBuildDetail(expandedId ?? '', archived, true),
    queryFn: () => getAmiBuildDetail(expandedId!, { archived, verbose: true }),
    enabled: expandedId !== null,
    staleTime: 5_000,
    refetchInterval: acting || expandedId === null ? false : pollIntervalMs,
  })

  const items = listQuery.data?.items ?? []
  const totalPages = listQuery.data?.total_pages ?? 0
  const total = listQuery.data?.total ?? 0

  const blockingError =
    listQuery.isError && listQuery.data === undefined
      ? listQuery.error instanceof Error
        ? listQuery.error.message
        : String(listQuery.error)
      : null

  const onToggleArchived = () => {
    setArchived((v) => !v)
    setPage(1)
    setExpandedId(null)
  }

  const onConfirmCancel = async () => {
    if (!cancelTarget) return
    setActing(true)
    setActionError(null)
    try {
      await cancelAmiBuild(cancelTarget)
      setCancelTarget(null)
      setExpandedId(null)
      await queryClient.invalidateQueries({ queryKey: ['amiBuilds'] })
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setActing(false)
    }
  }

  return (
    <div className="instance-list">
      <div className="page-header">
        <h1 className="page-title">AMI Builds</h1>
        <div className="page-header-actions">
          <label className="ami-archived-toggle">
            <input
              type="checkbox"
              checked={archived}
              onChange={onToggleArchived}
            />
            Show archived
          </label>
        </div>
      </div>

      <p className="ami-page-description">
        View staged AMI build pipelines and cancel active builds (archives in S3 only).
      </p>

      <DataFreshnessBar
        resourceLabel="AMI build list"
        dataUpdatedAt={listQuery.dataUpdatedAt}
        isFetching={listQuery.isFetching}
        onRefresh={() => listQuery.refetch()}
      />

      {blockingError && (
        <p className="error-message" role="alert">{blockingError}</p>
      )}
      {actionError && (
        <p className="error-message" role="alert">{actionError}</p>
      )}

      {items.length === 0 && !listQuery.isLoading && !blockingError ? (
        <p className="ami-empty">No {archived ? 'archived ' : ''}AMI builds found.</p>
      ) : (
        <div className="table-wrap">
          <table className="instances-table ami-builds-table">
            <thead>
              <tr>
                <th>Build ID</th>
                <th>AMI name</th>
                <th>Created</th>
                <th>Status</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {items.map((row) => {
                const isExpanded = expandedId === row.build_id
                return (
                  <Fragment key={row.build_id}>
                    <tr
                      className={`ami-build-row${isExpanded ? ' ami-build-row--expanded' : ''}`}
                      onClick={() => setExpandedId(isExpanded ? null : row.build_id)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          setExpandedId(isExpanded ? null : row.build_id)
                        }
                      }}
                      tabIndex={0}
                      role="button"
                      aria-expanded={isExpanded}
                    >
                      <td className="ami-build-id">{row.build_id}</td>
                      <td>{row.ami_name}</td>
                      <td>{formatCreated(row.created_at)}</td>
                      <td>
                        <span className={phaseClass(row.status_summary.phase)}>
                          {row.status_summary.label}
                        </span>
                      </td>
                      <td className="ami-build-actions" onClick={(e) => e.stopPropagation()}>
                        {!archived && (
                          <button
                            type="button"
                            className="btn btn-danger btn-sm"
                            disabled={acting}
                            onClick={() => setCancelTarget(row.build_id)}
                          >
                            Cancel
                          </button>
                        )}
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr className="ami-build-detail-row">
                        <td colSpan={5}>
                          {detailQuery.isLoading && expandedId === row.build_id && (
                            <p>Loading pipeline detail…</p>
                          )}
                          {detailQuery.isError && expandedId === row.build_id && (
                            <p className="error-message" role="alert">
                              {detailQuery.error instanceof Error
                                ? detailQuery.error.message
                                : String(detailQuery.error)}
                            </p>
                          )}
                          {detailQuery.data && expandedId === row.build_id && (
                            <BuildDetailPanel detail={detailQuery.data} />
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {totalPages > 1 && (
        <div className="ami-pagination">
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            disabled={page <= 1 || listQuery.isFetching}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            Previous
          </button>
          <span className="ami-pagination-label">
            Page {page} of {totalPages} ({total} builds)
          </span>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            disabled={page >= totalPages || listQuery.isFetching}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </button>
        </div>
      )}

      {cancelTarget && (
        <div className="ami-confirm-overlay" role="dialog" aria-modal="true" aria-labelledby="ami-cancel-title">
          <div className="ami-confirm-dialog">
            <h2 id="ami-cancel-title" className="ami-confirm-title">Cancel AMI build?</h2>
            <p>
              This will move build <code>{cancelTarget}</code> from active staging to the archive in S3.
              It does not terminate any builder or test EC2 instances.
            </p>
            <div className="ami-confirm-actions">
              <button
                type="button"
                className="btn btn-secondary"
                disabled={acting}
                onClick={() => setCancelTarget(null)}
              >
                Keep build
              </button>
              <button
                type="button"
                className="btn btn-danger"
                disabled={acting}
                onClick={() => void onConfirmCancel()}
              >
                {acting ? 'Archiving…' : 'Archive build'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
