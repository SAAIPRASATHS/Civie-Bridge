/**
 * Centralised API surface. Components import from here, never from `axios`
 * directly, so every request shares the session-cookie configuration and the
 * normalised error handling.
 */

export { API_BASE_URL, apiClient, setUnauthorizedHandler } from './client'
export { ApiError, toApiError } from './errors'
export type { ApiErrorKind, FieldErrors } from './errors'

export { authApi } from './auth'
export { citizenApi } from './citizen'
export { officerApi } from './officer'
export { decisionApi } from './decisions'
