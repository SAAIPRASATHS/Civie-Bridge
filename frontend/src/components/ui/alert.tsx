import { AlertTriangle, CheckCircle2, Info, ShieldAlert } from 'lucide-react'
import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

type AlertTone = 'info' | 'warning' | 'danger' | 'success'

const TONE_STYLES: Record<AlertTone, { wrap: string; icon: ReactNode }> = {
  info: {
    wrap: 'border-[#bcdcef] bg-[var(--info-subtle)] text-[#0b3f5c]',
    icon: <Info className="size-4 text-[var(--info)]" />,
  },
  warning: {
    wrap: 'border-[#f1dcb8] bg-[var(--warning-subtle)] text-[#7a3c07]',
    icon: <AlertTriangle className="size-4 text-[var(--warning)]" />,
  },
  danger: {
    wrap: 'border-[#f2c9c9] bg-[var(--destructive-subtle)] text-[#7d1a1a]',
    icon: <ShieldAlert className="size-4 text-[var(--destructive)]" />,
  },
  success: {
    wrap: 'border-[#c3e5cf] bg-[var(--success-subtle)] text-[#0f5628]',
    icon: <CheckCircle2 className="size-4 text-[var(--success)]" />,
  },
}

/**
 * An inline notice.
 *
 * `warning` is deliberately used for the AI-advisory banner so a reader is
 * never left in doubt that a recommendation is not yet a decision.
 */
export function Alert({
  tone = 'info',
  title,
  children,
  className,
  role = 'note',
}: {
  tone?: AlertTone
  title?: string
  children?: ReactNode
  className?: string
  role?: 'note' | 'alert' | 'status'
}) {
  const styles = TONE_STYLES[tone]
  return (
    <div
      role={role}
      className={cn('flex gap-3 rounded-lg border p-3.5 text-sm', styles.wrap, className)}
    >
      <span className="mt-0.5 shrink-0">{styles.icon}</span>
      <div className="space-y-1">
        {title ? <p className="font-semibold">{title}</p> : null}
        {children ? <div className="leading-relaxed">{children}</div> : null}
      </div>
    </div>
  )
}
