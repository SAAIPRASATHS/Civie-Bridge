import { Menu, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import type { ReactNode } from 'react'

import { BrandLockup } from './Logo'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export interface NavItem {
  to: string
  label: string
  icon?: ReactNode
  /** Match only the exact path, for an index route inside a section. */
  end?: boolean
}

/**
 * The dashboard shell: a persistent section sidebar on wide screens that
 * collapses into a drawer on small ones.
 *
 * Using one shell for both the citizen and officer surfaces keeps the two
 * consistent - the same spacing, the same header, the same mobile behaviour -
 * while the nav items and the account summary differ per role.
 */
export function AppShell({
  nav,
  children,
  surfaceLabel,
  account,
  footer,
}: {
  nav: NavItem[]
  children: ReactNode
  surfaceLabel: string
  account?: ReactNode
  footer?: ReactNode
}) {
  const [drawerOpen, setDrawerOpen] = useState(false)
  const location = useLocation()

  // Close the mobile drawer whenever the route changes.
  useEffect(() => {
    setDrawerOpen(false)
  }, [location.pathname])

  return (
    <div className="min-h-dvh bg-[var(--background)] lg:grid lg:grid-cols-[16rem_1fr]">
      {/* Mobile top bar */}
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-[var(--border)] bg-white/95 px-4 py-3 backdrop-blur lg:hidden">
        <BrandLockup subtitle={surfaceLabel} />
        <Button
          variant="outline"
          size="icon"
          aria-label={drawerOpen ? 'Close navigation' : 'Open navigation'}
          aria-expanded={drawerOpen}
          onClick={() => setDrawerOpen((open) => !open)}
        >
          {drawerOpen ? <X className="size-4" /> : <Menu className="size-4" />}
        </Button>
      </header>

      {/* Sidebar */}
      <aside
        className={cn(
          'z-40 border-r border-[var(--border)] bg-white lg:sticky lg:top-0 lg:flex lg:h-dvh lg:flex-col',
          drawerOpen ? 'fixed inset-x-0 top-[57px] bottom-0 flex flex-col overflow-y-auto' : 'hidden',
        )}
      >
        <div className="hidden items-center border-b border-[var(--border)] px-5 py-5 lg:flex">
          <BrandLockup subtitle={surfaceLabel} />
        </div>

        <nav aria-label={`${surfaceLabel} sections`} className="flex-1 space-y-1 p-3">
          {nav.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
                  isActive
                    ? 'bg-[var(--primary-subtle)] text-[var(--primary)]'
                    : 'text-[var(--secondary-foreground)] hover:bg-[var(--secondary)]',
                )
              }
            >
              {item.icon}
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="space-y-3 border-t border-[var(--border)] p-4">
          {account}
          {footer}
        </div>
      </aside>

      {/* Drawer scrim */}
      {drawerOpen ? (
        <button
          type="button"
          aria-label="Close navigation"
          className="fixed inset-0 top-[57px] z-30 bg-[#0f172a]/30 lg:hidden"
          onClick={() => setDrawerOpen(false)}
        />
      ) : null}

      <main className="min-w-0">
        <div className="mx-auto w-full max-w-[1200px] space-y-6 px-4 py-6 sm:px-6 lg:py-8">
          {children}
        </div>
      </main>
    </div>
  )
}
