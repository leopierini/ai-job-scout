import { TriangleAlertIcon } from "lucide-react"

import { money } from "@/lib/format"
import type { LiveEstimate } from "@/lib/estimate"

export const ESTIMATE_FAILED = "Couldn't work out the cost. Check your keys and try again."
const COST_FAILED = "Couldn't work out the cost."
const RETRY_CLASS =
  "rounded-sm font-medium text-foreground underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"

/** "Couldn't work out the cost. Try again" for a saved-settings estimate that failed. */
export function CostFailed({ onRetry, className }: { onRetry: () => void; className?: string }) {
  return (
    <p className={className}>
      {COST_FAILED}{" "}
      <button type="button" className={RETRY_CLASS} onClick={onRetry}>
        Try again
      </button>
    </p>
  )
}
const ESTIMATE_INVALID = "Fix the highlighted values to see what a run would cost."

interface EstimateNoteProps {
  live: LiveEstimate
  /** The words before the amount, e.g. "Your first run can cost up to". */
  lead: string
  /** Apify credit left, when known (a token test can know it before the estimate does). */
  credit: number | null
  /** Sample data: runs cost nothing, so there's no amount to show. */
  sample?: boolean
}

/** "… can cost up to $1.80 (12 searches).", a warning when that's more than the Apify credit left, or why not. */
export function EstimateNote({ live, lead, credit, sample = false }: EstimateNoteProps) {
  if (sample) return <p className="text-muted-foreground">Sample runs are free.</p>
  if (live.kind === "loading") return <p className="text-muted-foreground">Working out the cost…</p>
  if (live.kind === "failed") return <p className="text-muted-foreground">{live.invalid ? ESTIMATE_INVALID : ESTIMATE_FAILED}</p>
  const { estimate } = live
  return (
    <div className="flex flex-col gap-2">
      <p>
        {lead} <span className="num font-medium">{money(estimate.max_usd)}</span> (
        <span className="num">{estimate.searches}</span> {estimate.searches === 1 ? "search" : "searches"}).
      </p>
      {credit !== null && estimate.max_usd > credit && (
        <p className="flex items-start gap-1.5 text-warn">
          <TriangleAlertIcon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span>
            That's more than the <span className="num">{money(credit)}</span> left on your Apify account. Use fewer
            keywords or regions, or add credit on Apify.
          </span>
        </p>
      )}
    </div>
  )
}
