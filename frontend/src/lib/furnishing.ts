/**
 * Furnishing vocabulary shared by every studio screen. The labels are the words
 * the client sees on the portal, so a request never reads differently here.
 */
export const FURNISHING_STYLES = [
  { value: 'modern', label: 'Modern' },
  { value: 'minimalist', label: 'Minimalist' },
  { value: 'scandinavian', label: 'Scandinavian' },
  { value: 'warm_luxury', label: 'Warm luxury' },
] as const

export type FurnishingStyleValue = (typeof FURNISHING_STYLES)[number]['value']

export const isFurnishingStyle = (value: unknown): value is FurnishingStyleValue =>
  FURNISHING_STYLES.some((option) => option.value === value)

export const styleLabel = (value?: string | null): string =>
  FURNISHING_STYLES.find((option) => option.value === value)?.label ?? value ?? 'Not chosen'

export const TERMINAL_JOB_STATUSES = new Set(['completed', 'failed', 'cancelled'])
