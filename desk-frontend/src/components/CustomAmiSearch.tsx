import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { listDeskAmis } from '../api/client'
import { queryKeys } from '../queryKeys'
import { formatAmiVersionDate } from '../pages/amiUtils'

interface CustomAmiSearchProps {
  selectedId: string
  selectedName: string
  disabled?: boolean
  onSelect: (ami: { image_id: string; name: string }) => void
  onClear: () => void
}

const MIN_QUERY_LEN = 2
const SEARCH_DEBOUNCE_MS = 300

function isSearchableQuery(query: string): boolean {
  const trimmed = query.trim()
  return trimmed.length >= MIN_QUERY_LEN
}

export function CustomAmiSearch({
  selectedId,
  selectedName,
  disabled,
  onSelect,
  onClear,
}: CustomAmiSearchProps) {
  const [query, setQuery] = useState(selectedName || '')
  const [debouncedQuery, setDebouncedQuery] = useState(query.trim())
  const [menuOpen, setMenuOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handle = window.setTimeout(() => {
      setDebouncedQuery(query.trim())
    }, SEARCH_DEBOUNCE_MS)
    return () => window.clearTimeout(handle)
  }, [query])

  const searchQuery = useQuery({
    queryKey: [...queryKeys.deskAmis, 'search', debouncedQuery],
    queryFn: () => listDeskAmis({ q: debouncedQuery, publicOnly: true }),
    enabled: menuOpen && isSearchableQuery(debouncedQuery),
    staleTime: 30_000,
  })

  const results = (searchQuery.data ?? []).filter((a) => a.state === 'available')

  useEffect(() => {
    if (!menuOpen) return
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [menuOpen])

  const onInputChange = (value: string) => {
    setQuery(value)
    setMenuOpen(true)
    if (selectedId && value !== selectedName) {
      onClear()
    }
  }

  const pick = (ami: { image_id: string; name: string }) => {
    onSelect(ami)
    setQuery(ami.name)
    setDebouncedQuery(ami.name)
    setMenuOpen(false)
  }

  const showResults = menuOpen && isSearchableQuery(query.trim())
  const waitingForDebounce = showResults && debouncedQuery !== query.trim()

  return (
    <div className="ami-picker custom-ami-search" ref={containerRef}>
      <input
        className="create-input custom-ami-search-input"
        type="text"
        placeholder="Search public AMIs (keywords)…"
        value={query}
        onChange={(e) => onInputChange(e.target.value)}
        onFocus={() => setMenuOpen(true)}
        disabled={disabled}
        autoComplete="off"
        autoCapitalize="off"
        autoCorrect="off"
        spellCheck={false}
      />
      {selectedId && selectedName && query === selectedName && (
        <p className="create-notice" role="status">
          Selected: {selectedName}
        </p>
      )}
      {showResults && (
        <div className="ami-picker-menu custom-ami-search-menu" role="listbox">
          {(searchQuery.isPending || waitingForDebounce) && (
            <p className="ami-picker-empty">Searching…</p>
          )}
          {searchQuery.isError && (
            <p className="create-error" role="alert">
              {searchQuery.error instanceof Error
                ? searchQuery.error.message
                : 'AMI search failed.'}
            </p>
          )}
          {!searchQuery.isPending &&
            !waitingForDebounce &&
            !searchQuery.isError &&
            results.length === 0 && (
            <p className="ami-picker-empty">No matching AMIs found.</p>
          )}
          {!searchQuery.isPending &&
            !waitingForDebounce &&
            results.map((ami) => (
              <button
                key={ami.image_id}
                type="button"
                className={`ami-picker-option${selectedId === ami.image_id ? ' ami-picker-option--selected' : ''}`}
                onClick={() => pick(ami)}
              >
                <span className="ami-picker-option-label">{ami.name}</span>
                <span className="ami-picker-option-meta">
                  {formatAmiVersionDate(ami.creation_date)}
                </span>
              </button>
            ))}
        </div>
      )}
      {menuOpen && query.trim().length > 0 && query.trim().length < MIN_QUERY_LEN && (
        <p className="ami-picker-empty">Type at least {MIN_QUERY_LEN} characters to search.</p>
      )}
    </div>
  )
}
