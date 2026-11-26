import {
  cloneElement,
  createContext,
  isValidElement,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
} from 'react'
import type { ReactElement, ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'

import { cn } from '@/lib/utils'

/**
 * A small, dependency-free modal dialog.
 *
 * It provides the accessibility essentials a confirmation step needs: the panel
 * is `role="dialog" aria-modal`, `Escape` closes it, the page behind is
 * scroll-locked, focus moves into the panel on open and is trapped there while
 * it is open, and focus returns to the trigger on close. It is used only for
 * officer decisions and sign-out, so a hand-rolled implementation keeps the
 * frontend's dependency surface small.
 */

interface DialogContextValue {
  close: () => void
  titleId: string
  descriptionId: string
}

const DialogContext = createContext<DialogContextValue | null>(null)

function useDialogContext(): DialogContextValue {
  const context = useContext(DialogContext)
  if (context === null) {
    throw new Error('Dialog sub-components must be used within a <Dialog>')
  }
  return context
}

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea, input:not([disabled]), select, [tabindex]:not([tabindex="-1"])'

export function Dialog({
  open,
  onOpenChange,
  children,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  children: ReactNode
}) {
  const panelRef = useRef<HTMLDivElement | null>(null)
  const previouslyFocused = useRef<HTMLElement | null>(null)
  const titleId = useId()
  const descriptionId = useId()

  const close = useCallback(() => onOpenChange(false), [onOpenChange])

  useEffect(() => {
    if (!open) return

    previouslyFocused.current = document.activeElement as HTMLElement | null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    // Move focus into the panel so a keyboard user is not left behind it.
    const panel = panelRef.current
    const first = panel?.querySelector<HTMLElement>(FOCUSABLE)
    ;(first ?? panel)?.focus()

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        close()
        return
      }
      if (event.key !== 'Tab' || !panelRef.current) return
      const focusable = Array.from(panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE))
      if (focusable.length === 0) return
      const firstItem = focusable[0]
      const lastItem = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === firstItem) {
        event.preventDefault()
        lastItem.focus()
      } else if (!event.shiftKey && document.activeElement === lastItem) {
        event.preventDefault()
        firstItem.focus()
      }
    }

    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
      previouslyFocused.current?.focus()
    }
  }, [open, close])

  if (!open) return null

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-[#0f172a]/45 backdrop-blur-[1px]"
        onClick={close}
        aria-hidden
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        tabIndex={-1}
        className="relative z-10 max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-xl border border-[var(--border)] bg-white p-6 shadow-xl focus:outline-none"
      >
        <DialogContext.Provider value={{ close, titleId, descriptionId }}>
          {children}
        </DialogContext.Provider>
      </div>
    </div>,
    document.body,
  )
}

export function DialogContent({
  title,
  description,
  children,
  className,
  hideClose = false,
}: {
  title: string
  description?: ReactNode
  children: ReactNode
  className?: string
  hideClose?: boolean
}) {
  const { close, titleId, descriptionId } = useDialogContext()
  return (
    <div className={cn('space-y-5', className)}>
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1">
          <h2 id={titleId} className="text-lg font-semibold tracking-tight">
            {title}
          </h2>
          <div id={descriptionId} className="text-sm text-[var(--muted-foreground)]">
            {description}
          </div>
        </div>
        {hideClose ? null : (
          <button
            type="button"
            aria-label="Close dialog"
            onClick={close}
            className="rounded-md p-1 text-[var(--muted-foreground)] transition-colors hover:bg-[var(--secondary)]"
          >
            <X className="size-4" />
          </button>
        )}
      </div>
      {children}
    </div>
  )
}

/** A trigger that closes the dialog when activated; supports `asChild`. */
export function DialogClose({
  children,
  asChild = false,
}: {
  children: ReactElement<{ onClick?: (event: unknown) => void }> | ReactNode
  asChild?: boolean
}) {
  const { close } = useDialogContext()
  if (asChild && isValidElement(children)) {
    const child = children as ReactElement<{ onClick?: (event: unknown) => void }>
    return cloneElement(child, {
      onClick: (event: unknown) => {
        child.props.onClick?.(event)
        close()
      },
    })
  }
  return (
    <button type="button" onClick={close}>
      {children}
    </button>
  )
}
