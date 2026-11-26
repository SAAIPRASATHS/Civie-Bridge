import { LogOut } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { RoleBadge } from '@/components/common/badges'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { Button } from '@/components/ui/button'
import { useAuth } from '@/auth/AuthContext'

/** The signed-in account summary shown at the foot of the sidebar. */
export function AccountPanel() {
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
    <div className="space-y-3">
      <div className="space-y-1">
        <p className="truncate text-sm font-medium text-[var(--foreground)]" title={user.full_name}>
          {user.full_name}
        </p>
        <p className="truncate text-xs text-[var(--muted-foreground)]" title={user.email}>
          {user.email}
        </p>
        <div className="pt-1">
          <RoleBadge role={user.role} />
        </div>
      </div>

      <Button
        variant="outline"
        size="sm"
        className="w-full justify-start"
        onClick={() => setConfirmOpen(true)}
      >
        <LogOut className="size-3.5" /> Sign out
      </Button>

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
