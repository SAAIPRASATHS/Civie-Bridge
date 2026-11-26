/**
 * The single Axios instance every service module uses.
 *
 * Two properties matter for security:
 *
 * * `withCredentials: true` - the session lives in an HttpOnly cookie, so the
 *   browser must be allowed to attach it. The cookie is never read or stored by
 *   JavaScript; there is no token in `localStorage` and no `Authorization`
 *   header to leak.
 * * Every failure is converted to an `ApiError`, so callers handle one error
 *   type instead of inspecting `AxiosError` shapes.
 *
 * In development the Vite dev server proxies `/api` to the FastAPI process, so
 * the cookie is first-party and no cross-site configuration is needed. In a
 * deployed build `VITE_API_BASE_URL` may point at the API's own origin.
 */

import axios, { AxiosError } from 'axios'

import { toApiError } from './errors'

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true,
  headers: { Accept: 'application/json' },
  timeout: 120_000,
})

/**
 * Called when the API reports the session is no longer valid. `AuthContext`
 * registers a handler so a 401 anywhere in the app clears the cached user and
 * returns the visitor to the sign-in screen.
 */
type UnauthorizedHandler = () => void
let unauthorizedHandler: UnauthorizedHandler | null = null

export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler
}

apiClient.interceptors.response.use(
  (response) => response,
  (error: unknown) => {
    if (error instanceof AxiosError) {
      const status = error.response?.status ?? null
      // A 401 on the sign-in probe is expected for a visitor; only surface the
      // session-expiry callback for a request that was expected to succeed.
      if (status === 401 && unauthorizedHandler) {
        unauthorizedHandler()
      }
      return Promise.reject(toApiError(status, error.response?.data))
    }
    return Promise.reject(
      toApiError(null, error instanceof Error ? { detail: error.message } : null),
    )
  },
)
