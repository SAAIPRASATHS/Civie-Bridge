/**
 * The application router.
 *
 * One React application serves both surfaces. The two role-guarded route
 * branches are the whole of the "three interfaces" split: everything under
 * `/app` is the citizen portal and everything under `/officer` is the officer
 * dashboard, and the guard refuses a branch the signed-in role cannot use.
 *
 * The guards are a usability layer only - the FastAPI backend re-checks the
 * session and the role on every request, so hiding a route is a convenience,
 * never the security boundary.
 */

import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { Toaster } from 'sonner'

import { AuthProvider } from '@/auth/AuthContext'
import { RedirectIfAuthenticated, RequireRole } from '@/auth/guards'
import { CITIZEN_ROLES, OFFICER_ROLES } from '@/lib/routes'
import LandingPage from '@/pages/LandingPage'
import NotFoundPage from '@/pages/NotFoundPage'
import LoginPage from '@/pages/auth/LoginPage'
import RegisterPage from '@/pages/auth/RegisterPage'
import CitizenDashboardPage from '@/pages/citizen/CitizenDashboardPage'
import CitizenLayout from '@/pages/citizen/CitizenLayout'
import CitizenProfilePage from '@/pages/citizen/CitizenProfilePage'
import ComplaintDetailPage from '@/pages/citizen/ComplaintDetailPage'
import MyComplaintsPage from '@/pages/citizen/MyComplaintsPage'
import SubmitComplaintPage from '@/pages/citizen/SubmitComplaintPage'
import OfficerAnalyticsPage from '@/pages/officer/OfficerAnalyticsPage'
import OfficerComplaintDetailPage from '@/pages/officer/OfficerComplaintDetailPage'
import OfficerLayout from '@/pages/officer/OfficerLayout'
import OfficerOverviewPage from '@/pages/officer/OfficerOverviewPage'
import OfficerQueuePage from '@/pages/officer/OfficerQueuePage'

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/" element={<LandingPage />} />

          <Route
            path="/login"
            element={
              <RedirectIfAuthenticated>
                <LoginPage />
              </RedirectIfAuthenticated>
            }
          />
          <Route
            path="/register"
            element={
              <RedirectIfAuthenticated>
                <RegisterPage />
              </RedirectIfAuthenticated>
            }
          />

          <Route element={<RequireRole roles={CITIZEN_ROLES} />}>
            <Route path="/app" element={<CitizenLayout />}>
              <Route index element={<CitizenDashboardPage />} />
              <Route path="submit" element={<SubmitComplaintPage />} />
              <Route path="complaints" element={<MyComplaintsPage />} />
              <Route path="complaints/:referenceId" element={<ComplaintDetailPage />} />
              <Route path="profile" element={<CitizenProfilePage />} />
            </Route>
          </Route>

          <Route element={<RequireRole roles={OFFICER_ROLES} />}>
            <Route path="/officer" element={<OfficerLayout />}>
              <Route index element={<OfficerOverviewPage />} />
              <Route path="complaints" element={<OfficerQueuePage />} />
              <Route path="complaints/:complaintId" element={<OfficerComplaintDetailPage />} />
              <Route path="queue" element={<OfficerQueuePage pendingOnly />} />
              <Route path="analytics" element={<OfficerAnalyticsPage />} />
            </Route>
          </Route>

          <Route path="*" element={<NotFoundPage />} />
        </Routes>

        <Toaster position="top-right" richColors />
      </AuthProvider>
    </BrowserRouter>
  )
}
