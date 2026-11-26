/**
 * Normalised API errors.
 *
 * The backend answers an `HTTPException` with `{"detail": "<string>"}` and a
 * Pydantic validation failure with `{"detail": [{loc, msg, type}, ...]}`. Both
 * shapes are folded into one `ApiError` here so a component never has to know
 * which endpoint produced the failure.
 */

export type ApiErrorKind =
  | 'unauthorized'
  | 'forbidden'
  | 'not_found'
  | 'conflict'
  | 'validation'
  | 'server'
  | 'network'
  | 'unknown'

/** A per-field validation message, keyed by the offending field name. */
export type FieldErrors = Record<string, string>

export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status: number | null
  readonly detail: string
  readonly fieldErrors: FieldErrors

  constructor(options: {
    kind: ApiErrorKind
    status?: number | null
    detail: string
    fieldErrors?: FieldErrors
  }) {
    super(options.detail)
    this.name = 'ApiError'
    this.kind = options.kind
    this.status = options.status ?? null
    this.detail = options.detail
    this.fieldErrors = options.fieldErrors ?? {}
  }

  /** A short, user-facing sentence safe to render in a toast or an alert. */
  get userMessage(): string {
    switch (this.kind) {
      case 'unauthorized':
        return 'Your session has ended. Please sign in again.'
      case 'forbidden':
        return 'Your account does not have permission to perform this action.'
      case 'not_found':
        return this.detail || 'That record could not be found.'
      case 'conflict':
        return this.detail || 'That action conflicts with the current state of the record.'
      case 'validation':
        return this.detail || 'Some of the submitted values were not accepted.'
      case 'network':
        return 'The CivicBridge AI service could not be reached. Check your connection and try again.'
      case 'server':
        return 'The CivicBridge AI service reported an error. Please try again shortly.'
      default:
        return this.detail || 'Something went wrong.'
    }
  }
}

const KIND_BY_STATUS: Record<number, ApiErrorKind> = {
  401: 'unauthorized',
  403: 'forbidden',
  404: 'not_found',
  409: 'conflict',
  422: 'validation',
}

/** Build an `ApiError` from a status code and a parsed response body. */
export function toApiError(status: number | null, body: unknown): ApiError {
  if (status === null) {
    return new ApiError({
      kind: 'network',
      status: null,
      detail: 'the CivicBridge AI service could not be reached',
    })
  }

  const kind: ApiErrorKind =
    KIND_BY_STATUS[status] ?? (status >= 500 ? 'server' : 'unknown')

  const detail = body as { detail?: unknown } | null | undefined
  const rawDetail = detail?.detail

  // FastAPI's 422 body: a list of per-field validation failures.
  if (Array.isArray(rawDetail)) {
    const fieldErrors: FieldErrors = {}
    const messages: string[] = []
    for (const item of rawDetail) {
      const entry = item as { loc?: unknown[]; msg?: string }
      const path = Array.isArray(entry.loc) ? entry.loc.slice(1).join('.') : ''
      const message = entry.msg ?? 'is not valid'
      if (path) {
        fieldErrors[path] = message
        messages.push(`${path}: ${message}`)
      } else {
        messages.push(message)
      }
    }
    return new ApiError({
      kind,
      status,
      detail: messages.join('; '),
      fieldErrors,
    })
  }

  if (typeof rawDetail === 'string') {
    return new ApiError({ kind, status, detail: rawDetail })
  }

  return new ApiError({ kind, status, detail: `request failed with status ${status}` })
}
