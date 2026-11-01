import { cn } from "@/lib/utils"
import { STEP_STATUS_LABELS } from "@/lib/format"
import type { RunStatus, StepStatus } from "@/api/types"

type PillStatus = StepStatus | RunStatus

const LABELS: Record<PillStatus, string> = { ...STEP_STATUS_LABELS, cancelled: "Cancelled" }

const STYLES: Record<PillStatus, string> = {
  pending: "bg-neutral-soft text-muted-foreground",
  running: "bg-primary text-primary-foreground",
  done: "bg-accent-soft text-primary",
  error: "border-destructive text-destructive",
  skipped: "border-border text-muted-foreground",
  cancelled: "border-border text-muted-foreground",
}

/** A step's or a run's status, in words: Pending, Running, Done, Error, Skipped (or Cancelled for a run). */
export function StatusPill({
  status,
  label,
  className,
}: {
  status: PillStatus
  /** Replaces the status word, e.g. "Interrupted" on an error pill. */
  label?: string
  className?: string
}) {
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center rounded-sm border border-transparent px-2 text-xs font-semibold whitespace-nowrap",
        STYLES[status],
        className,
      )}
    >
      {label ?? LABELS[status]}
    </span>
  )
}
