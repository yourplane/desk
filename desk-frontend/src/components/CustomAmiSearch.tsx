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

export function CustomAmiSearch({
  selectedId,
  selectedName,
  disabled,
  onSelect,
  onClear,
}: CustomAmiSearchProps) {
  const [query, setQuery] = useState(selectedName || '')
  const [menuOpen, setMenuOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  const searchQuery = useQuery({
    queryKey: [...queryKeys.deskAmis, 'search', query.trim()],
    queryFn: () => listDeskAmis({ q: query.trim(), managedOnly: false }),
    enabled: menuOpen && query.trim().length >= MIN_QUERY_LEN,
    staleTime: 15_000,
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
    setMenuOpen(false)
  }

  return (
    <div className="ami-picker custom-ami-search" ref={containerRef}>
      <input
        className="create-input"
        type="search"
        placeholder="Search AMIs by name…"
        value={query}
        onChange={(e) => onInputChange(e.target.value)}
        onFocus={() => setMenuOpen(true)}
        disabled={disabled}
        autoComplete="off"
      />
      {selectedId && selectedName && query === selectedName && (
        <p className="create-notice" role="status">
          Selected: {selectedName}
        </p>
      )}
      {menuOpen && query.trim().length >= MIN_QUERY_LEN && (
        <div className="ami-picker-menu custom-ami-search-menu" role="listbox">
          {searchQuery.isPending && (
            <p className="ami-picker-empty">Searching…</p>
          )}
          {searchQuery.isError && (
            <p className="create-error" role="alert">
              {searchQuery.error instanceof Error
                ? searchQuery.error.message
                : 'AMI search failed.'}
            </p>
          )}
          {!searchQuery.isPending && !searchQuery.isError && results.length === 0 && (
            <p className="ami-picker-empty">No matching AMIs found.</p>
          )}
          {results.map((ami) => (
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
