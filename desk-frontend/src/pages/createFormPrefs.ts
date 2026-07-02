export type AmiInputMode = 'desk' | 'custom'

export interface CreateFormPrefs {
  amiMode: AmiInputMode
  deskAmiId: string | null
  customAmiId: string
  allowUntestedAmi: boolean
  instanceType: string
  name: string
}

const STORAGE_KEY = 'desk-create-form-prefs'

const DEFAULT_PREFS: CreateFormPrefs = {
  amiMode: 'desk',
  deskAmiId: null,
  customAmiId: '',
  allowUntestedAmi: false,
  instanceType: 't3.medium',
  name: '',
}

export function loadCreateFormPrefs(): CreateFormPrefs {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { ...DEFAULT_PREFS }
    const parsed = JSON.parse(raw) as Partial<CreateFormPrefs>
    return {
      amiMode: parsed.amiMode === 'custom' ? 'custom' : 'desk',
      deskAmiId: typeof parsed.deskAmiId === 'string' ? parsed.deskAmiId : null,
      customAmiId: typeof parsed.customAmiId === 'string' ? parsed.customAmiId : '',
      allowUntestedAmi: Boolean(parsed.allowUntestedAmi),
      instanceType: typeof parsed.instanceType === 'string' && parsed.instanceType.trim()
        ? parsed.instanceType
        : DEFAULT_PREFS.instanceType,
      name: typeof parsed.name === 'string' ? parsed.name : '',
    }
  } catch {
    return { ...DEFAULT_PREFS }
  }
}

export function saveCreateFormPrefs(prefs: CreateFormPrefs): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs))
  } catch {
    // ignore quota / private mode errors
  }
}

export function clearCreateFormName(): void {
  const prefs = loadCreateFormPrefs()
  saveCreateFormPrefs({ ...prefs, name: '' })
}
