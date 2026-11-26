import { Sparkles } from 'lucide-react'

import { Alert } from '@/components/ui/alert'

/**
 * The standing reminder that everything it wraps is advisory.
 *
 * The wording comes from the server - `suggestion_notice` on the complaint
 * payloads - so the disclosure cannot drift from what the backend actually
 * guarantees. The component only supplies the presentation.
 */
export function AdvisoryNotice({
  notice,
  provenance,
  title = 'AI-generated advisory output',
}: {
  notice: string
  provenance?: string
  title?: string
}) {
  return (
    <Alert tone="warning" title={title} role="note">
      <p>{notice}</p>
      {provenance ? (
        <p className="mt-2 flex items-start gap-2 text-xs text-[#7a3c07]/90">
          <Sparkles className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>{provenance}</span>
        </p>
      ) : null}
    </Alert>
  )
}
