/**
 * Authentication and role-access tests.
 *
 * These mount the real `App` router with the API module mocked, so they assert
 * the behaviour that actually ships: which surface a role lands on, that a
 * citizen cannot reach the officer dashboard, and that no credential or token
 * is written to browser storage.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// The map is the one component that needs a real layout engine, so it is
// replaced here; everything else is the shipping implementation.
vi.mock('@/components/maps/LocationPicker', () => ({
  LocationPicker: () => <div data-testid="map-placeholder" />,
}))

vi.mock('@/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api')>()
  return {
    ...actual,
    authApi: {
      me: vi.fn(),
      login: vi.fn(),
      register: vi.fn(),
      logout: vi.fn(),
      demoAccounts: vi.fn().mockResolvedValue([]),
    },
    citizenApi: { submit: vi.fn(), listMine: vi.fn(), getMine: vi.fn() },
    officerApi: {
      listComplaints: vi.fn(),
      reviewQueue: vi.fn(),
      departments: vi.fn(),
      analytics: vi.fn(),
      complaintDetail: vi.fn(),
      auditTrail: vi.fn(),
      duplicateMatches: vi.fn(),
    },
    decisionApi: { reviewDuplicate: vi.fn(), decideRouting: vi.fn() },
  }
})

import { ApiError, authApi, citizenApi, officerApi } from '@/api'
import App from '@/App'
import type { OfficerAnalytics, UserProfile } from '@/types'

const CITIZEN: UserProfile = {
  id: '11111111-1111-1111-1111-111111111111',
  email: 'citizen@example.test',
  full_name: 'Citizen One',
  role: 'citizen',
  preferred_language: 'en',
  is_active: true,
}

const OFFICER: UserProfile = {
  id: '22222222-2222-2222-2222-222222222222',
  email: 'officer@example.test',
  full_name: 'Officer Two',
  role: 'officer',
  preferred_language: 'en',
  is_active: true,
}

const EMPTY_PAGE = {
  total: 0,
  limit: 20,
  offset: 0,
  returned: 0,
  items: [],
  suggestion_notice: 'Everything shown here is AI-generated advisory output.',
}

const ANALYTICS: OfficerAnalytics = {
  generated_at: '2026-01-01T00:00:00Z',
  window_days: 30,
  total_complaints: 0,
  pending_review: 0,
  decided: 0,
  sla_breached: 0,
  awaiting_triage: 0,
  unassigned: 0,
  by_status: [],
  by_category: [],
  by_priority: [],
  by_language: [],
  by_department: [],
  submitted_trend: [],
  resolved_trend: [],
  provenance: 'Prototype data.',
}

function anonymousSession() {
  vi.mocked(authApi.me).mockRejectedValue(
    new ApiError({ kind: 'unauthorized', status: 401, detail: 'Not authenticated' }),
  )
}

function citizenSession() {
  vi.mocked(authApi.me).mockResolvedValue(CITIZEN)
  vi.mocked(citizenApi.listMine).mockResolvedValue(EMPTY_PAGE)
}

function officerSession() {
  vi.mocked(authApi.me).mockResolvedValue(OFFICER)
  vi.mocked(officerApi.analytics).mockResolvedValue(ANALYTICS)
  vi.mocked(officerApi.reviewQueue).mockResolvedValue(EMPTY_PAGE)
}

beforeEach(() => {
  vi.clearAllMocks()
  window.history.pushState({}, '', '/')
})

describe('anonymous visitors', () => {
  it('are sent to the sign-in page instead of the citizen portal', async () => {
    anonymousSession()
    window.history.pushState({}, '', '/app')

    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    await waitFor(() => expect(window.location.pathname).toBe('/login'))
  })

  it('are sent to the sign-in page instead of the officer dashboard', async () => {
    anonymousSession()
    window.history.pushState({}, '', '/officer/complaints')

    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    await waitFor(() => expect(window.location.pathname).toBe('/login'))
  })

  it('see the public landing page without signing in', async () => {
    anonymousSession()
    render(<App />)

    expect(
      await screen.findByRole('heading', { level: 1, name: /CivicBridge/i }),
    ).toBeInTheDocument()
    // The landing page must not expose a dashboard.
    expect(screen.queryByRole('navigation', { name: /Citizen portal sections/i })).toBeNull()
  })
})

describe('a signed-in citizen', () => {
  it('lands on the citizen portal dashboard', async () => {
    citizenSession()
    window.history.pushState({}, '', '/app')

    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Your complaints' })).toBeInTheDocument()
    expect(
      screen.getByRole('navigation', { name: /Citizen portal sections/i }),
    ).toBeInTheDocument()
  })

  it('cannot open the officer dashboard', async () => {
    citizenSession()
    window.history.pushState({}, '', '/officer')

    render(<App />)

    // The guard redirects to the citizen home rather than rendering the queue.
    await waitFor(() => expect(window.location.pathname).toBe('/app'))
    expect(await screen.findByRole('heading', { name: 'Your complaints' })).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: /Officer dashboard sections/i })).toBeNull()
  })

  it('cannot open an officer complaint review by URL', async () => {
    citizenSession()
    window.history.pushState({}, '', '/officer/complaints/33333333-3333-3333-3333-333333333333')

    render(<App />)

    await waitFor(() => expect(window.location.pathname).toBe('/app'))
    expect(vi.mocked(officerApi.complaintDetail)).not.toHaveBeenCalled()
  })

  it('cannot open the officer analytics page', async () => {
    citizenSession()
    window.history.pushState({}, '', '/officer/analytics')

    render(<App />)

    await waitFor(() => expect(window.location.pathname).toBe('/app'))
    expect(vi.mocked(officerApi.analytics)).not.toHaveBeenCalled()
  })
})

describe('a signed-in officer', () => {
  it('lands on the officer dashboard and never sees the citizen portal', async () => {
    officerSession()
    window.history.pushState({}, '', '/officer')

    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Overview' })).toBeInTheDocument()
    expect(
      screen.getByRole('navigation', { name: /Officer dashboard sections/i }),
    ).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: /Citizen portal sections/i })).toBeNull()
  })

  it('is redirected away from the citizen portal', async () => {
    officerSession()
    window.history.pushState({}, '', '/app')

    render(<App />)

    await waitFor(() => expect(window.location.pathname).toBe('/officer'))
  })
})

describe('the sign-in form', () => {
  it('signs in and keeps no credential in browser storage', async () => {
    anonymousSession()
    vi.mocked(authApi.login).mockResolvedValue({ user: OFFICER, expires_in_seconds: 3600 })
    window.history.pushState({}, '', '/login')

    render(<App />)

    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Email'), OFFICER.email)
    await user.type(screen.getByLabelText('Password'), 'correct horse battery staple')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    await waitFor(() => expect(authApi.login).toHaveBeenCalledTimes(1))
    expect(authApi.login).toHaveBeenCalledWith({
      email: OFFICER.email,
      password: 'correct horse battery staple',
    })
    // The session lives in an HttpOnly cookie the frontend cannot read.
    expect(window.localStorage.length).toBe(0)
    expect(window.sessionStorage.length).toBe(0)
  })

  it('shows the reason a sign-in was refused', async () => {
    anonymousSession()
    vi.mocked(authApi.login).mockRejectedValue(
      new ApiError({ kind: 'unauthorized', status: 401, detail: 'Incorrect email or password' }),
    )
    window.history.pushState({}, '', '/login')

    render(<App />)

    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Email'), 'someone@example.test')
    await user.type(screen.getByLabelText('Password'), 'wrong')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(
      await screen.findByText('Your session has ended. Please sign in again.'),
    ).toBeInTheDocument()
    expect(authApi.login).toHaveBeenCalledTimes(1)
  })
})
