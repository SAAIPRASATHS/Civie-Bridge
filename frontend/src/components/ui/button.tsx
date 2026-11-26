import { cva, type VariantProps } from 'class-variance-authority'
import { cloneElement, isValidElement } from 'react'
import type { ButtonHTMLAttributes, ReactElement } from 'react'

import { cn } from '@/lib/utils'

/**
 * Button styles.
 *
 * `default` is the single primary action on a screen; `outline` and `ghost` are
 * for everything secondary so a page never presents two competing primary
 * buttons. `destructive` is reserved for irreversible actions and always sits
 * behind a confirmation dialog.
 */
const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)] disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        default: 'bg-[var(--primary)] text-[var(--primary-foreground)] hover:bg-[#1a45bd]',
        secondary: 'bg-[var(--secondary)] text-[var(--secondary-foreground)] hover:bg-[#e2e8f1]',
        outline: 'border border-[var(--input)] bg-white text-[var(--foreground)] hover:bg-[var(--secondary)]',
        ghost: 'text-[var(--foreground)] hover:bg-[var(--secondary)]',
        destructive: 'bg-[var(--destructive)] text-[var(--destructive-foreground)] hover:bg-[#a91f1f]',
        success: 'bg-[var(--success)] text-white hover:bg-[#12692f]',
        subtle: 'bg-[var(--primary-subtle)] text-[var(--accent-foreground)] hover:bg-[#d8e5fb]',
        link: 'text-[var(--primary)] underline-offset-4 hover:underline',
      },
      size: {
        default: 'h-10 px-4 py-2',
        sm: 'h-8 rounded-md px-3 text-xs',
        lg: 'h-11 rounded-md px-6',
        icon: 'h-9 w-9',
      },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  },
)

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  /** Render the button's styling onto its single child element instead. */
  asChild?: boolean
}

export function Button({ className, variant, size, asChild = false, children, ...props }: ButtonProps) {
  const classes = cn(buttonVariants({ variant, size }), className)

  if (asChild && isValidElement(children)) {
    const child = children as ReactElement<{ className?: string }>
    return cloneElement(child, { className: cn(classes, child.props.className) })
  }

  return (
    <button className={classes} {...props}>
      {children}
    </button>
  )
}

export { buttonVariants }
