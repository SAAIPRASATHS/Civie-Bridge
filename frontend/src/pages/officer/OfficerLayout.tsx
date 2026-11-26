/**
 * The officer dashboard shell.
 *
 * Like the citizen layout it only supplies navigation to the shared
 * `AppShell`, so both surfaces keep the same header, spacing and mobile
 * behaviour. The sections mirror the officer's job: see the workload, work the
 * queue, review one complaint, then read the aggregate picture.
 */

import { BarChart3, ClipboardList, Inbox, LayoutDashboard } from 'lucide-react'
import { Outlet } from 'react-router-dom'

import { AccountPanel } from '@/components/layout/AccountPanel'
import { AppShell } from '@/components/layout/AppShell'
import type { NavItem } from '@/components/layout/AppShell'

const NAV: NavItem[] = [
  { to: '/officer', label: 'Overview', icon: <LayoutDashboard className="size-4" />, end: true },
  { to: '/officer/complaints', label: 'Complaints', icon: <ClipboardList className="size-4" /> },
  { to: '/officer/queue', label: 'Review queue', icon: <Inbox className="size-4" /> },
  { to: '/officer/analytics', label: 'Analytics', icon: <BarChart3 className="size-4" /> },
]

export default function OfficerLayout() {
  return (
    <AppShell nav={NAV} surfaceLabel="Officer dashboard" account={<AccountPanel />}>
      <Outlet />
    </AppShell>
  )
}
