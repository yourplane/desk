import type { RouteReachability } from './webRouteProbe'

export type PortDisplayGroup = 'active' | 'broken'

/** Decide whether a port chip belongs with active links or the collapsed broken group. */
export function resolvePortDisplayGroup(state: {
  faviconLoaded: boolean
  probeDone: boolean
  reachability: RouteReachability | null
  lastSettledGroup: PortDisplayGroup | null
}): PortDisplayGroup {
  if (state.faviconLoaded) return 'active'
  if (state.probeDone && state.reachability !== null) {
    return state.reachability === 'dead' ? 'broken' : 'active'
  }
  return state.lastSettledGroup ?? 'active'
}
