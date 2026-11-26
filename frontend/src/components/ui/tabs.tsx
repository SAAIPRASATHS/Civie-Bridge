import { createContext, useContext, useState } from 'react'
import type { ButtonHTMLAttributes, HTMLAttributes, KeyboardEvent, ReactNode } from 'react'

import { cn } from '@/lib/utils'

/**
 * A small, dependency-free tab set.
 *
 * Implemented with the ARIA tab pattern (`role="tablist"`, `role="tab"`,
 * `aria-selected`, `role="tabpanel"`) and roving arrow-key navigation, which is
 * what the officer detail page needs to switch between the analysis panels.
 */

interface TabsContextValue {
  value: string
  setValue: (value: string) => void
  baseId: string
}

const TabsContext = createContext<TabsContextValue | null>(null)

function useTabs(): TabsContextValue {
  const context = useContext(TabsContext)
  if (context === null) {
    throw new Error('Tabs sub-components must be used within <Tabs>')
  }
  return context
}

export function Tabs({
  value,
  onValueChange,
  defaultValue = '',
  children,
  className,
  id,
}: {
  value?: string
  onValueChange?: (value: string) => void
  defaultValue?: string
  children: ReactNode
  className?: string
  id?: string
}) {
  const [internal, setInternal] = useState(defaultValue)
  const current = value ?? internal
  const setValue = (next: string) => {
    if (value === undefined) setInternal(next)
    onValueChange?.(next)
  }
  return (
    <TabsContext.Provider value={{ value: current, setValue, baseId: id ?? 'tabs' }}>
      <div className={className}>{children}</div>
    </TabsContext.Provider>
  )
}

export function TabsList({ className, children, ...props }: HTMLAttributes<HTMLDivElement>) {
  const { setValue } = useTabs()

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const triggers = Array.from(
      event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'),
    )
    const index = triggers.findIndex((trigger) => trigger === document.activeElement)
    if (index === -1) return
    let next = index
    if (event.key === 'ArrowRight') next = (index + 1) % triggers.length
    else if (event.key === 'ArrowLeft') next = (index - 1 + triggers.length) % triggers.length
    else if (event.key === 'Home') next = 0
    else if (event.key === 'End') next = triggers.length - 1
    else return
    event.preventDefault()
    triggers[next].focus()
    const target = triggers[next].dataset.value
    if (target) setValue(target)
  }

  return (
    <div
      role="tablist"
      onKeyDown={onKeyDown}
      className={cn(
        'flex flex-wrap items-center gap-1 rounded-lg border border-[var(--border)] bg-[var(--secondary)] p-1',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  )
}

export function TabsTrigger({
  value,
  className,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { value: string }) {
  const tabs = useTabs()
  const selected = tabs.value === value
  return (
    <button
      type="button"
      role="tab"
      id={`${tabs.baseId}-tab-${value}`}
      aria-controls={`${tabs.baseId}-panel-${value}`}
      aria-selected={selected}
      tabIndex={selected ? 0 : -1}
      data-value={value}
      data-state={selected ? 'active' : 'inactive'}
      onClick={() => tabs.setValue(value)}
      className={cn(
        'rounded-md px-3 py-1.5 text-sm font-medium text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)] data-[state=active]:bg-white data-[state=active]:text-[var(--primary)] data-[state=active]:shadow-sm',
        className,
      )}
      {...props}
    >
      {children}
    </button>
  )
}

export function TabsContent({
  value,
  className,
  children,
  ...props
}: HTMLAttributes<HTMLDivElement> & { value: string }) {
  const tabs = useTabs()
  if (tabs.value !== value) return null
  return (
    <div
      role="tabpanel"
      id={`${tabs.baseId}-panel-${value}`}
      aria-labelledby={`${tabs.baseId}-tab-${value}`}
      tabIndex={0}
      className={cn('mt-4 focus:outline-none', className)}
      {...props}
    >
      {children}
    </div>
  )
}
