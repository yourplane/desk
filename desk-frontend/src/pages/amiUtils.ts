import type { DeskAmi } from '../api/client'

const FAMILY_SUFFIX_RE = /^(.+)-(\d{8}-\d{6})$/

export function extractFamilyBaseName(amiName: string): string {
  const match = amiName.match(FAMILY_SUFFIX_RE)
  return match ? match[1] : amiName
}

export function isTestedAmi(buildStatus: string | null | undefined): boolean {
  return buildStatus === 'tested'
}

export function formatAmiVersionDate(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export interface AmiFamily {
  baseName: string
  versions: DeskAmi[]
}

export function groupDeskAmis(
  amis: DeskAmi[],
  options: { allowUntested: boolean },
): AmiFamily[] {
  const available = amis.filter((a) => a.state === 'available')
  const byFamily = new Map<string, DeskAmi[]>()

  for (const ami of available) {
    const base = extractFamilyBaseName(ami.name)
    const list = byFamily.get(base) ?? []
    list.push(ami)
    byFamily.set(base, list)
  }

  const families: AmiFamily[] = []
  for (const [baseName, versions] of byFamily) {
    const sorted = [...versions].sort((a, b) => b.creation_date.localeCompare(a.creation_date))
    const visible = options.allowUntested
      ? sorted
      : sorted.filter((a) => isTestedAmi(a.build_status))
    if (visible.length === 0) continue
    families.push({ baseName, versions: sorted })
  }

  families.sort((a, b) => {
    const aDate = a.versions[0]?.creation_date ?? ''
    const bDate = b.versions[0]?.creation_date ?? ''
    return bDate.localeCompare(aDate)
  })

  return families
}

export function selectableDeskAmis(
  amis: DeskAmi[],
  options: { allowUntested: boolean },
): DeskAmi[] {
  const families = groupDeskAmis(amis, options)
  const out: DeskAmi[] = []
  for (const family of families) {
    const versions = options.allowUntested
      ? family.versions
      : family.versions.filter((a) => isTestedAmi(a.build_status))
    if (versions.length > 0) out.push(...versions)
  }
  return out
}

export function defaultVisibleVersion(
  family: AmiFamily,
  options: { allowUntested: boolean },
): DeskAmi {
  const versions = options.allowUntested
    ? family.versions
    : family.versions.filter((a) => isTestedAmi(a.build_status))
  return versions[0]
}

export function olderVersions(
  family: AmiFamily,
  options: { allowUntested: boolean },
): DeskAmi[] {
  const versions = options.allowUntested
    ? family.versions
    : family.versions.filter((a) => isTestedAmi(a.build_status))
  return versions.slice(1)
}
