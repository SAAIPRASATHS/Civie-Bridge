import type { ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogClose } from '@/components/ui/dialog'

/**
 * A confirmation step for a consequential action.
 *
 * Every officer decision (approving a duplicate link, approving a routing)
 * routes through this dialog, so a decision that changes a complaint's official
 * state is never one stray click away. The dialog can host the reason and notes
 * fields that the audit trail records.
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  cancelLabel = 'Cancel',
  onConfirm,
  pending = false,
  tone = 'default',
  children,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: ReactNode
  confirmLabel: string
  cancelLabel?: string
  onConfirm: () => void
  pending?: boolean
  tone?: 'default' | 'destructive' | 'success'
  children?: ReactNode
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={title} description={description}>
        {children ? <div className="mb-4 space-y-3">{children}</div> : null}
        <div className="flex justify-end gap-2">
          <DialogClose asChild>
            <Button variant="outline" disabled={pending}>
              {cancelLabel}
            </Button>
          </DialogClose>
          <Button
            variant={tone === 'default' ? 'default' : tone === 'success' ? 'success' : 'destructive'}
            onClick={onConfirm}
            disabled={pending}
          >
            {pending ? 'Working…' : confirmLabel}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
