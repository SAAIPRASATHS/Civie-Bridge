/**
 * The citizen's read-only profile.
 *
 * There is no update endpoint for a profile in this phase, so the page states
 * that plainly rather than offering fields that would silently fail. The note
 * about the session cookie explains where the sign-in actually lives.
 */

import { LogOut } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { RoleBadge } from '@/components/common/badges'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { DescriptionItem, DescriptionList } from '@/components/common/DescriptionList'
import { PageHeader } from '@/components/common/PageHeader'
import { Alert } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { useAuth } from '@/auth/AuthContext'
import { useDocumentTitle } from '@/lib/hooks'
import { languageLabel } from '@/lib/labels'

export default function CitizenProfilePage() {
  useDocumentTitle('Profile')
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [pending, setPending] = useState(false)

  if (!user) return null

  const signOut = async () => {
    setPending(true)
    try {
      await logout()
      navigate('/login', { replace: true })
    } finally {
      setPending(false)
      setConfirmOpen(false)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Profile"
        description="Your account details in the citizen portal."
        actions={
          <Button variant="outline" onClick={() => setConfirmOpen(true)}>
            <LogOut className="size-4" /> Sign out
          </Button>
        }
      />

      <Card>
        <CardHeader>
          <CardTitle>Account</CardTitle>
          <CardDescription>These details come from your account.</CardDescription>
        </CardHeader>
        <CardContent>
          <DescriptionList columns={2}>
            <DescriptionItem label="Full name">{user.full_name}</DescriptionItem>
            <DescriptionItem label="Email">{user.email}</DescriptionItem>
            <DescriptionItem label="Role">
              <RoleBadge role={user.role} />
            </DescriptionItem>
            <DescriptionItem label="Preferred language">
              {languageLabel(user.preferred_language)}
            </DescriptionItem>
            <DescriptionItem label="Account status">
              <Badge tone={user.is_active ? 'success' : 'neutral'}>
                {user.is_active ? 'Active' : 'Inactive'}
              </Badge>
            </DescriptionItem>
          </DescriptionList>
        </CardContent>
      </Card>

      <Alert tone="info" title="Editing is not available in this phase">
        Profile editing and password changes are not exposed by the API in this phase, so these
        details cannot be changed here. Your sign-in is held in an HttpOnly cookie that browser
        scripts cannot read; signing out clears it on the server.
      </Alert>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="Sign out of CivicBridge AI?"
        description="The session cookie is cleared on the server. You will need your password to sign back in."
        confirmLabel="Sign out"
        onConfirm={signOut}
        pending={pending}
      />
    </div>
  )
}
