import type {
  InputHTMLAttributes,
  LabelHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from 'react'

import { cn } from '@/lib/utils'

const FIELD_BASE =
  'w-full rounded-md border bg-white px-3 py-2 text-sm text-[var(--foreground)] shadow-[0_1px_1px_rgba(15,23,42,0.02)] placeholder:text-[var(--muted-foreground)] focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--ring)] disabled:cursor-not-allowed disabled:opacity-60 aria-invalid:border-[var(--destructive)]'

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean
}

export function Input({ className, invalid, ...props }: InputProps) {
  return (
    <input
      className={cn(FIELD_BASE, 'h-10', !invalid && 'border-[var(--input)]', className)}
      aria-invalid={invalid || undefined}
      {...props}
    />
  )
}

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  invalid?: boolean
}

export function Textarea({ className, invalid, ...props }: TextareaProps) {
  return (
    <textarea
      className={cn(FIELD_BASE, 'min-h-28 resize-y', !invalid && 'border-[var(--input)]', className)}
      aria-invalid={invalid || undefined}
      {...props}
    />
  )
}

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  invalid?: boolean
}

/**
 * A styled native `<select>`.
 *
 * A native control is keyboard- and screen-reader-perfect by default, requires
 * no portal and works inside a filtered table header without layout tricks, so
 * it is preferred over a scripted listbox for the dashboard filters.
 */
export function Select({ className, invalid, children, ...props }: SelectProps) {
  return (
    <select
      className={cn(
        FIELD_BASE,
        'h-10 cursor-pointer appearance-none bg-[length:16px] bg-[right_0.6rem_center] bg-no-repeat pr-9',
        !invalid && 'border-[var(--input)]',
        className,
      )}
      style={{
        backgroundImage:
          "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%235b6a83' stroke-width='2' stroke-linecap='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E\")",
      }}
      aria-invalid={invalid || undefined}
      {...props}
    >
      {children}
    </select>
  )
}

export function Label({ className, ...props }: LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label
      className={cn('text-sm font-medium text-[var(--foreground)]', className)}
      {...props}
    />
  )
}

/** A field's help or validation message, announced to assistive technology. */
export function FieldMessage({
  children,
  tone = 'muted',
  className,
  id,
}: {
  children: ReactNode
  tone?: 'muted' | 'error'
  className?: string
  id?: string
}) {
  return (
    <p
      id={id}
      className={cn(
        'text-xs',
        tone === 'error' ? 'text-[var(--destructive)]' : 'text-[var(--muted-foreground)]',
        className,
      )}
    >
      {children}
    </p>
  )
}
