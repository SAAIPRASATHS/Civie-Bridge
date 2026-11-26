/**
 * The 404 page.
 *
 * It offers a route out of the dead end: home for a visitor, and the signed-in
 * user's own surface when a session is present, so nobody is stranded.
 */

import { ArrowLeft, LayoutDashboard } from 'lucide-react'
import { Link } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { useAuth } from '@/auth/AuthContext'
import { useDocumentTitle } from '@/lib/hooks'
import { homePathForRole } from '@/lib/routes'

export default function NotFoundPage() {
  useDocumentTitle('Page not found')
  const { user } = useAuth()

  return (
    <div className="flex min-h-dvh items-center justify-center bg-[var(--background)] px-4">
      <Card className="w-full max-w-lg p-8 text-center">
        <p className="text-sm font-semibold uppercase tracking-[0.14em] text-[var(--primary)]">404</p>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">This page could not be found</h1>
        <p className="mt-2 text-sm text-[var(--muted-foreground)]">
          The link may be out of date, or the page may have moved somewhere else.
        </p>

        <div className="mt-6 flex flex-wrap justify-center gap-3">
          <Button variant="outline" asChild>
            <Link to="/">
              <ArrowLeft className="size-4" /> Back to home
            </Link>
          </Button>
          {user ? (
            <Button asChild>
              <Link to={homePathForRole(user.role)}>
                <LayoutDashboard className="size-4" /> Go to my dashboard
              </Link>
            </Button>
          ) : (
            <Button asChild>
              <Link to="/login">
                <LayoutDashboard className="size-4" /> Sign in
              </Link>
            </Button>
          )}
        </div>
      </Card>
    </div>
  )
}
