/**
 * The CivicBridge AI wordmark.
 *
 * The mark is a stylised arch - a bridge between a citizen and a service -
 * drawn inline so it needs no asset request and inherits the theme colour.
 */
export function Logo({ className, size = 28 }: { className?: string; size?: number }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden
      role="presentation"
    >
      <rect width="32" height="32" rx="8" fill="var(--primary)" />
      <path
        d="M6 22V14.5C6 10.91 8.91 8 12.5 8h7C23.09 8 26 10.91 26 14.5V22"
        stroke="white"
        strokeWidth="2.25"
        strokeLinecap="round"
      />
      <path d="M10 22v-6M16 22v-9M22 22v-6" stroke="white" strokeWidth="2.25" strokeLinecap="round" />
      <path d="M4.5 22h23" stroke="white" strokeWidth="2.25" strokeLinecap="round" />
    </svg>
  )
}

export function BrandLockup({ subtitle }: { subtitle?: string }) {
  return (
    <span className="flex items-center gap-2.5">
      <Logo />
      <span className="flex flex-col leading-none">
        <span className="text-[15px] font-semibold tracking-tight text-[var(--foreground)]">
          CivicBridge <span className="text-[var(--primary)]">AI</span>
        </span>
        {subtitle ? (
          <span className="mt-0.5 text-[11px] font-medium uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
            {subtitle}
          </span>
        ) : null}
      </span>
    </span>
  )
}
