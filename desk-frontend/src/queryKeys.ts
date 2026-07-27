/** Shared TanStack Query keys for desk API resources. */

export const queryKeys = {
  workstations: (infra: boolean) => ['workstations', infra] as const,
  deskAmis: ['deskAmis'] as const,
  webRoutesAll: ['webRoutes', 'all'] as const,
  costs: ['costs'] as const,
  costsMonths: ['costs', 'months'] as const,
  costsDaily: ['costs', 'daily'] as const,
  costsTodayUtc: ['costs', 'today-utc'] as const,
  savedCommands: ['savedCommands'] as const,
}
