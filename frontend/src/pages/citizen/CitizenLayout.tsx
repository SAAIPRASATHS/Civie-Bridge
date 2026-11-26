/**
 * The citizen portal shell.
 *
 * It exists only to hand `AppShell` the citizen navigation and the account
 * summary, so the citizen and officer surfaces share one layout.
 */

import { ClipboardList, LayoutDashboard, Send, User } from 'lucide-react'
import { Outlet } from 'react-router-dom'

import { AccountPanel } from '@/components/layout/AccountPanel'
import { AppShell } from '@/components/layout/AppShell'
import type { NavItem } from '@/components/layout/AppShell'

const NAV: NavItem[] = [
  { to: '/app', label: 'Dashboard', icon: <LayoutDashboard className="size-4" />, end: true },
  { to: '/app/submit', label: 'Submit a complaint', icon: <Send className="size-4" /> },
  { to: '/app/complaints', label: 'My complaints', icon: <ClipboardList className="size-4" /> },
  { to: '/app/profile', label: 'Profile', icon: <User className="size-4" /> },
]

export default function CitizenLayout() {
  return (
    <AppShell nav={NAV} surfaceLabel="Citizen portal" account={<AccountPanel />}>
      <Outlet />
    </AppShell>
  )
}
