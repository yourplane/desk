import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { createWorkstation, listDeskAmis, type CreateWorkstationResult } from '../api/client'
import { queryKeys } from '../queryKeys'
import {
  defaultVisibleVersion,
  extractFamilyBaseName,
  formatAmiVersionDate,
  groupDeskAmis,
  isTestedAmi,
  olderVersions,
  selectableDeskAmis,
  type AmiFamily,
} from '../pages/amiUtils'
import {
  clearCreateFormName,
  loadCreateFormPrefs,
  saveCreateFormPrefs,
  type AmiInputMode,
  type CreateFormPrefs,
} from '../pages/createFormPrefs'
import { CustomAmiSearch } from './CustomAmiSearch'

interface CreateWorkstationFormProps {
  onClose: () => void
  onLaunchStarted?: (name: string) => void
  onLaunchFinished?: (result: {
    name: string
    ok: boolean
    error?: string
    result?: CreateWorkstationResult
  }) => void
}

function prefsFromState(state: {
  amiMode: AmiInputMode
  deskAmiId: string | null
  customAmiId: string
  customAmiName: string
  allowUntestedAmi: boolean
  instanceType: string
  name: string
}): CreateFormPrefs {
  return { ...state }
}

function AmiOptionRow({
  ami,
  baseName,
  selected,
  indent,
  onSelect,
}: {
  ami: { image_id: string; name: string; creation_date: string; build_status: string | null }
  baseName?: string
  selected: boolean
  indent?: boolean
  onSelect: (id: string) => void
}) {
  const showBadge = !isTestedAmi(ami.build_status)
  return (
    <button
      type="button"
      className={`ami-picker-option${selected ? ' ami-picker-option--selected' : ''}${indent ? ' ami-picker-option--indent' : ''}`}
      onClick={() => onSelect(ami.image_id)}
    >
      <span className="ami-picker-option-label">
        {indent ? formatAmiVersionDate(ami.creation_date) : (baseName ?? extractFamilyBaseName(ami.name))}
      </span>
      {!indent && (
        <span className="ami-picker-option-meta">
          {formatAmiVersionDate(ami.creation_date)}
          {showBadge && <span className="ami-badge ami-badge--untested">Untested</span>}
        </span>
      )}
      {indent && showBadge && <span className="ami-badge ami-badge--untested">Untested</span>}
    </button>
  )
}

function FamilyRows({
  family,
  allowUntested,
  deskAmiId,
  expanded,
  onToggleExpand,
  onSelect,
}: {
  family: AmiFamily
  allowUntested: boolean
  deskAmiId: string | null
  expanded: boolean
  onToggleExpand: () => void
  onSelect: (id: string) => void
}) {
  const visible = defaultVisibleVersion(family, { allowUntested })
  const older = olderVersions(family, { allowUntested })
  return (
    <div className="ami-picker-family">
      <AmiOptionRow
        ami={visible}
        baseName={family.baseName}
        selected={deskAmiId === visible.image_id}
        onSelect={onSelect}
      />
      {older.length > 0 && (
        <>
          <button
            type="button"
            className="ami-picker-expand"
            onClick={onToggleExpand}
            aria-expanded={expanded}
          >
            {expanded ? 'Hide older versions' : 'Show older versions'}
          </button>
          {expanded &&
            older.map((ami) => (
              <AmiOptionRow
                key={ami.image_id}
                ami={ami}
                selected={deskAmiId === ami.image_id}
                indent
                onSelect={onSelect}
              />
            ))}
        </>
      )}
    </div>
  )
}

export function CreateWorkstationForm({ onClose, onLaunchStarted, onLaunchFinished }: CreateWorkstationFormProps) {
  const queryClient = useQueryClient()
  const initial = loadCreateFormPrefs()
  const [amiMode, setAmiMode] = useState<AmiInputMode>(initial.amiMode)
  const [deskAmiId, setDeskAmiId] = useState<string | null>(initial.deskAmiId)
  const [customAmiId, setCustomAmiId] = useState(initial.customAmiId)
  const [customAmiName, setCustomAmiName] = useState(initial.customAmiName)
  const [allowUntestedAmi, setAllowUntestedAmi] = useState(initial.allowUntestedAmi)
  const [instanceType, setInstanceType] = useState(initial.instanceType)
  const [name, setName] = useState(initial.name)
  const [submitting, setSubmitting] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [staleNotice, setStaleNotice] = useState<string | null>(null)
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [expandedFamilies, setExpandedFamilies] = useState<Set<string>>(new Set())
  const dropdownRef = useRef<HTMLDivElement>(null)
  const staleCheckedRef = useRef(false)

  const amisQuery = useQuery({
    queryKey: queryKeys.deskAmis,
    queryFn: () => listDeskAmis(),
    staleTime: 30_000,
  })

  const families = groupDeskAmis(amisQuery.data ?? [], { allowUntested: allowUntestedAmi })
  const selectable = selectableDeskAmis(amisQuery.data ?? [], { allowUntested: allowUntestedAmi })
  const selectedDeskAmi = selectable.find((a) => a.image_id === deskAmiId) ?? null

  useEffect(() => {
    saveCreateFormPrefs(
      prefsFromState({
        amiMode,
        deskAmiId,
        customAmiId,
        customAmiName,
        allowUntestedAmi,
        instanceType,
        name,
      }),
    )
  }, [amiMode, deskAmiId, customAmiId, customAmiName, allowUntestedAmi, instanceType, name])

  useEffect(() => {
    if (staleCheckedRef.current || amisQuery.isPending || amisQuery.isError) return
    staleCheckedRef.current = true
    if (deskAmiId && !selectable.some((a) => a.image_id === deskAmiId)) {
      setDeskAmiId(null)
      setStaleNotice('Previously selected AMI is no longer available. Please choose again.')
    }
  }, [amisQuery.isPending, amisQuery.isError, deskAmiId, selectable])

  useEffect(() => {
    if (!dropdownOpen) return
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [dropdownOpen])

  const onAllowUntestedChange = (checked: boolean) => {
    setAllowUntestedAmi(checked)
    if (!checked && deskAmiId) {
      const ami = (amisQuery.data ?? []).find((a) => a.image_id === deskAmiId)
      if (ami && !isTestedAmi(ami.build_status)) {
        setDeskAmiId(null)
      }
    }
  }

  const toggleFamilyExpanded = (baseName: string) => {
    setExpandedFamilies((prev) => {
      const next = new Set(prev)
      if (next.has(baseName)) next.delete(baseName)
      else next.add(baseName)
      return next
    })
  }

  const selectDeskAmi = (imageId: string) => {
    setDeskAmiId(imageId)
    setDropdownOpen(false)
    setStaleNotice(null)
  }

  const canLaunch =
    name.trim().length > 0 &&
    !submitting &&
    (amiMode === 'desk' ? deskAmiId !== null : customAmiId.length > 0)

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed || !canLaunch) return

    const amiId = amiMode === 'desk' ? deskAmiId! : customAmiId
    const launchOptions = {
      instanceType: instanceType || undefined,
      amiId,
      allowUntestedAmi: amiMode === 'desk' ? allowUntestedAmi : undefined,
    }

    setSubmitting(true)
    setCreateError(null)
    onLaunchStarted?.(trimmed)
    clearCreateFormName()
    setName('')
    onClose()

    void (async () => {
      try {
        const result = await createWorkstation(trimmed, launchOptions)
        await queryClient.invalidateQueries({ queryKey: ['workstations'] })
        onLaunchFinished?.({ name: trimmed, ok: true, result })
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err)
        onLaunchFinished?.({ name: trimmed, ok: false, error: message })
      } finally {
        setSubmitting(false)
      }
    })()
  }

  const dropdownLabel = selectedDeskAmi
    ? `${extractFamilyBaseName(selectedDeskAmi.name)} — ${formatAmiVersionDate(selectedDeskAmi.creation_date)}`
    : families.length === 0
      ? 'No desk AMIs available'
      : 'Select an AMI'

  return (
    <form className="create-form" onSubmit={onSubmit}>
      <div className="create-form-fields">
        <input
          className="create-input"
          type="text"
          placeholder="Workstation name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          autoFocus
          disabled={submitting}
        />
        <input
          className="create-input create-input--narrow"
          type="text"
          placeholder="Instance type"
          value={instanceType}
          onChange={(e) => setInstanceType(e.target.value)}
          disabled={submitting}
        />
      </div>

      <div className="create-form-ami-section">
        <div className="ami-mode-switch" role="group" aria-label="AMI input mode">
          <button
            type="button"
            className={`ami-mode-switch-btn${amiMode === 'desk' ? ' ami-mode-switch-btn--active' : ''}`}
            onClick={() => setAmiMode('desk')}
            disabled={submitting}
          >
            Desk AMI
          </button>
          <button
            type="button"
            className={`ami-mode-switch-btn${amiMode === 'custom' ? ' ami-mode-switch-btn--active' : ''}`}
            onClick={() => setAmiMode('custom')}
            disabled={submitting}
          >
            Custom AMI
          </button>
        </div>

        {amiMode === 'desk' ? (
          <div className="ami-picker">
            {amisQuery.isError && (
              <p className="create-error" role="alert">
                {amisQuery.error instanceof Error
                  ? amisQuery.error.message
                  : 'Failed to load desk AMIs.'}
              </p>
            )}
            {staleNotice && (
              <p className="create-notice" role="status">{staleNotice}</p>
            )}
            <div className="ami-picker-dropdown" ref={dropdownRef}>
              <button
                type="button"
                className="ami-picker-trigger"
                onClick={() => setDropdownOpen((o) => !o)}
                disabled={submitting || amisQuery.isPending}
                aria-haspopup="listbox"
                aria-expanded={dropdownOpen}
              >
                {amisQuery.isPending ? 'Loading AMIs…' : dropdownLabel}
              </button>
              {dropdownOpen && !amisQuery.isPending && (
                <div className="ami-picker-menu" role="listbox">
                  {families.length === 0 ? (
                    <p className="ami-picker-empty">
                      No desk AMIs available. Enable untested AMIs or switch to Custom AMI.
                    </p>
                  ) : (
                    families.map((family) => (
                      <FamilyRows
                        key={family.baseName}
                        family={family}
                        allowUntested={allowUntestedAmi}
                        deskAmiId={deskAmiId}
                        expanded={expandedFamilies.has(family.baseName)}
                        onToggleExpand={() => toggleFamilyExpanded(family.baseName)}
                        onSelect={selectDeskAmi}
                      />
                    ))
                  )}
                </div>
              )}
            </div>
            <label className="ami-untested-toggle">
              <input
                type="checkbox"
                checked={allowUntestedAmi}
                onChange={(e) => onAllowUntestedChange(e.target.checked)}
                disabled={submitting}
              />
              {' '}
              Allow untested AMI
            </label>
          </div>
        ) : (
          <CustomAmiSearch
            selectedId={customAmiId}
            selectedName={customAmiName}
            disabled={submitting}
            onSelect={(ami) => {
              setCustomAmiId(ami.image_id)
              setCustomAmiName(ami.name)
            }}
            onClear={() => {
              setCustomAmiId('')
              setCustomAmiName('')
            }}
          />
        )}
      </div>

      <div className="create-form-actions">
        <button type="submit" className="btn btn-start" disabled={!canLaunch}>
          {submitting ? 'Launching…' : 'Launch'}
        </button>
        <button
          type="button"
          className="btn btn-secondary"
          onClick={onClose}
          disabled={submitting}
        >
          Cancel
        </button>
      </div>
      {createError && <p className="create-error" role="alert">{createError}</p>}
    </form>
  )
}
