/**
 * API-layer tests.
 *
 * Two things are checked here that nothing else can: that a backend status code
 * becomes the right `ApiError` kind (the error vocabulary every screen depends
 * on), and that the service modules build the exact request the backend
 * documents. A mistyped query parameter is invisible in a component test and
 * fatal against the real API, so it is asserted at the layer that writes it.
 */

import { AxiosError, AxiosHeaders } from 'axios'
import type { AxiosAdapter, AxiosResponse } from 'axios'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  apiClient,
  citizenApi,
  decisionApi,
  officerApi,
  setUnauthorizedHandler,
  toApiError,
} from '@/api'

/** An adapter that answers a fixed status and body, including non-2xx. */
function adapterFor(status: number, body: unknown): AxiosAdapter {
  return async (config) => {
    const response = {
      data: body,
      status,
      statusText: String(status),
      headers: new AxiosHeaders(),
      config,
    } as AxiosResponse
    if (status >= 200 && status < 300) return response
    throw new AxiosError(
      `Request failed with status code ${status}`,
      String(status),
      config,
      undefined,
      response,
    )
  }
}

afterEach(() => {
  vi.restoreAllMocks()
  setUnauthorizedHandler(null)
})

describe('error mapping', () => {
  it('maps each backend status to its own error kind', () => {
    expect(toApiError(401, { detail: 'no session' }).kind).toBe('unauthorized')
    expect(toApiError(403, { detail: 'officer role required' }).kind).toBe('forbidden')
    expect(toApiError(404, { detail: 'not found' }).kind).toBe('not_found')
    expect(toApiError(409, { detail: 'already submitted' }).kind).toBe('conflict')
    expect(toApiError(422, { detail: 'bad' }).kind).toBe('validation')
    expect(toApiError(500, { detail: 'boom' }).kind).toBe('server')
    expect(toApiError(503, { detail: 'busy' }).kind).toBe('server')
  })

  it('keeps the backend message so a screen can show it verbatim', () => {
    const error = toApiError(409, { detail: 'this request id was already used' })
    expect(error.detail).toBe('this request id was already used')
    expect(error.userMessage).toBe('this request id was already used')
  })

  it('folds a FastAPI 422 body into per-field messages', () => {
    const error = toApiError(422, {
      detail: [
        { loc: ['body', 'text'], msg: 'String should have at least 1 character' },
        { loc: ['body', 'latitude'], msg: 'Input should be less than or equal to 90' },
      ],
    })
    expect(error.kind).toBe('validation')
    expect(error.fieldErrors.text).toBe('String should have at least 1 character')
    expect(error.fieldErrors.latitude).toBe('Input should be less than or equal to 90')
    expect(error.detail).toContain('text:')
  })

  it('treats a transport failure as a network error, not a server error', () => {
    const error = toApiError(null, null)
    expect(error.kind).toBe('network')
    expect(error.status).toBeNull()
    expect(error.userMessage).toContain('could not be reached')
  })

  it('never leaks the raw body into a user-facing message for a server fault', () => {
    const error = toApiError(500, { detail: 'Traceback (most recent call last): psycopg' })
    expect(error.userMessage).not.toContain('Traceback')
  })
})

describe('the axios client', () => {
  it('sends the session cookie and keeps no token in browser storage', () => {
    expect(apiClient.defaults.withCredentials).toBe(true)
    expect(apiClient.defaults.baseURL).toBe('/api')
    expect(window.localStorage.length).toBe(0)
  })

  it('converts a 403 into a forbidden ApiError', async () => {
    await expect(
      apiClient.get('/officer/complaints', {
        adapter: adapterFor(403, { detail: 'officer role required' }),
      }),
    ).rejects.toMatchObject({ kind: 'forbidden', detail: 'officer role required' })
  })

  it('reports an expired session through the registered handler', async () => {
    const handler = vi.fn()
    setUnauthorizedHandler(handler)

    await expect(
      apiClient.get('/auth/me', { adapter: adapterFor(401, { detail: 'Not authenticated' }) }),
    ).rejects.toBeInstanceOf(ApiError)

    expect(handler).toHaveBeenCalledTimes(1)
  })

  it('converts a dropped connection into a network ApiError', async () => {
    const dropped: AxiosAdapter = async () => {
      throw new Error('socket hang up')
    }
    await expect(apiClient.get('/health', { adapter: dropped })).rejects.toMatchObject({
      kind: 'network',
    })
  })
})

describe('service modules', () => {
  it('posts a complaint submission to /citizen/complaints', async () => {
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { replayed: false } } as never)

    await citizenApi.submit({ text: 'a burst pipe', request_id: 'abc' })

    expect(post).toHaveBeenCalledTimes(1)
    expect(post.mock.calls[0][0]).toBe('/citizen/complaints')
    expect(post.mock.calls[0][1]).toEqual({ text: 'a burst pipe', request_id: 'abc' })
  })

  it('serialises repeated status filters without array brackets', async () => {
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: {} } as never)

    await officerApi.listComplaints({
      status: ['submitted', 'under_review'],
      limit: 20,
      offset: 40,
    })

    const params = get.mock.calls[0][1]?.params as URLSearchParams
    expect(params.getAll('status')).toEqual(['submitted', 'under_review'])
    expect(String(params)).toBe('status=submitted&status=under_review&limit=20&offset=40')
  })

  it('omits a filter that was not set rather than sending an empty value', async () => {
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: {} } as never)

    await officerApi.listComplaints({ limit: 20, offset: 0 })

    expect(String(get.mock.calls[0][1]?.params)).toBe('limit=20&offset=0')
  })

  it('prefers the decision path the backend reports', async () => {
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: {} } as never)

    await decisionApi.decideRouting(
      'complaint-1',
      { decision: 'approve' },
      { routing_decision: '/api/complaints/complaint-1/routing/decision' },
    )

    // The `/api` prefix is stripped so it is not doubled by the instance baseURL.
    expect(post.mock.calls[0][0]).toBe('/complaints/complaint-1/routing/decision')
  })

  it('falls back to the documented path when the backend reports none', async () => {
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: {} } as never)

    await decisionApi.reviewDuplicate('complaint-1', {
      candidate_id: 'complaint-2',
      decision: 'reject',
    })

    expect(post.mock.calls[0][0]).toBe('/complaints/complaint-1/duplicates/review')
    expect(post.mock.calls[0][1]).toEqual({
      candidate_id: 'complaint-2',
      decision: 'reject',
    })
  })
})
