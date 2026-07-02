import { describe, expect, it } from 'vitest'
import type { DeskAmi } from '../api/client'
import {
  extractFamilyBaseName,
  formatAmiVersionDate,
  groupDeskAmis,
  isTestedAmi,
  olderVersions,
  selectableDeskAmis,
} from './amiUtils'

function ami(
  id: string,
  name: string,
  creationDate: string,
  buildStatus: string | null = 'tested',
): DeskAmi {
  return {
    image_id: id,
    name,
    state: 'available',
    creation_date: creationDate,
    source_instance: null,
    build_status: buildStatus,
  }
}

describe('extractFamilyBaseName', () => {
  it('strips build timestamp suffix', () => {
    expect(extractFamilyBaseName('default-desk-ami-20250701-120000')).toBe('default-desk-ami')
  })

  it('strips async build id suffix with commit hash', () => {
    expect(extractFamilyBaseName('default-desk-ami-20260101-010101-abcdef01')).toBe(
      'default-desk-ami',
    )
  })

  it('returns full name when no timestamp suffix', () => {
    expect(extractFamilyBaseName('router-ami')).toBe('router-ami')
  })
})

describe('groupDeskAmis', () => {
  const amis = [
    ami('ami-old', 'default-desk-ami-20250101-100000', '2025-01-01T10:00:00.000Z'),
    ami('ami-new', 'default-desk-ami-20250701-120000', '2025-07-01T12:00:00.000Z'),
    ami('ami-router', 'router-ami-20250601-080000', '2025-06-01T08:00:00.000Z', 'untested'),
  ]

  it('groups async build AMIs into the same family', () => {
    const amis = [
      ami('ami-old', 'default-desk-ami-20250101-100000-abc12345', '2025-01-01T10:00:00.000Z'),
      ami('ami-new', 'default-desk-ami-20250701-120000-deadbeef', '2025-07-01T12:00:00.000Z'),
    ]
    const families = groupDeskAmis(amis, { allowUntested: true })
    expect(families).toHaveLength(1)
    expect(families[0].baseName).toBe('default-desk-ami')
    expect(families[0].versions.map((v) => v.image_id)).toEqual(['ami-new', 'ami-old'])
  })

  it('groups by family and orders families newest first', () => {
    const families = groupDeskAmis(amis, { allowUntested: true })
    expect(families.map((f) => f.baseName)).toEqual(['default-desk-ami', 'router-ami'])
    expect(families[0].versions.map((v) => v.image_id)).toEqual(['ami-new', 'ami-old'])
  })

  it('hides untested families when allowUntested is false', () => {
    const families = groupDeskAmis(amis, { allowUntested: false })
    expect(families.map((f) => f.baseName)).toEqual(['default-desk-ami'])
  })

  it('omits families with only untested versions when allowUntested is false', () => {
    const onlyUntested = [ami('ami-u', 'router-ami-20250601-080000', '2025-06-01T08:00:00.000Z', 'untested')]
    expect(groupDeskAmis(onlyUntested, { allowUntested: false })).toEqual([])
  })
})

describe('selectableDeskAmis', () => {
  it('includes all available versions when allowUntested is true', () => {
    const amis = [
      ami('ami-old', 'default-desk-ami-20250101-100000', '2025-01-01T10:00:00.000Z'),
      ami('ami-new', 'default-desk-ami-20250701-120000', '2025-07-01T12:00:00.000Z'),
    ]
    expect(selectableDeskAmis(amis, { allowUntested: true }).map((a) => a.image_id)).toEqual([
      'ami-new',
      'ami-old',
    ])
  })
})

describe('olderVersions', () => {
  it('returns versions after the default visible one', () => {
    const families = groupDeskAmis(
      [
        ami('ami-old', 'default-desk-ami-20250101-100000', '2025-01-01T10:00:00.000Z'),
        ami('ami-new', 'default-desk-ami-20250701-120000', '2025-07-01T12:00:00.000Z'),
      ],
      { allowUntested: true },
    )
    expect(olderVersions(families[0], { allowUntested: true }).map((a) => a.image_id)).toEqual(['ami-old'])
  })
})

describe('isTestedAmi', () => {
  it('returns true only for tested status', () => {
    expect(isTestedAmi('tested')).toBe(true)
    expect(isTestedAmi('untested')).toBe(false)
    expect(isTestedAmi(null)).toBe(false)
  })
})

describe('formatAmiVersionDate', () => {
  it('formats a valid ISO date', () => {
    const formatted = formatAmiVersionDate('2025-07-01T12:00:00.000Z')
    expect(formatted).toContain('2025')
  })
})
