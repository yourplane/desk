/** Shared TanStack Query keys for desk API resources. */

export const queryKeys = {
  workstations: (infra: boolean) => ['workstations', infra] as const,
  webRoutesAll: ['webRoutes', 'all'] as const,
  costs: ['costs'] as const,
  savedCommands: ['savedCommands'] as const,
}
