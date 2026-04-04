import type { Instance } from '../api/client'

export function instanceKey(inst: Instance): string {
  return inst.name && inst.name !== '-' ? inst.name : inst.instance_id
}

export function stateColor(state: string): string {
  switch (state) {
    case 'running':
      return 'var(--state-running)'
    case 'pending':
      return 'var(--state-pending)'
    case 'stopped':
      return 'var(--state-stopped)'
    case 'stopping':
      return 'var(--state-pending)'
    default:
      return 'var(--state-default)'
  }
}
