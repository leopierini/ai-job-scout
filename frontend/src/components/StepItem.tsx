import { useId, type ReactNode } from "react"
import { ChevronRightIcon } from "lucide-react"

import { StatusPill } from "@/components/StatusPill"
import { Progress } from "@/components/ui/progress"
import { elapsed, elapsedLabel } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { StepState } from "@/api/types"

interface StepItemProps {
  /** 1 to 5. */
  index: number
  title: string
  state: StepState
  /** Whether the details below the step are open. Without `onToggle` the step can't be opened. */
  expanded?: boolean
  onToggle?: () => void
  /** The details shown when expanded: per-area results, archived reasons, errors. */
  children?: ReactNode
}

/** One step of a run: number, title, progress text, status pill and elapsed time. Renders an <li>: put it in an <ol>. */
export function StepItem({ index, title, state, expanded = false, onToggle, children }: StepItemProps) {
  const detailsId = useId()
  const { status, message, done, total, elapsed_s } = state
  const showBar = status === "running" && total !== null && total > 0
  const percent = showBar ? Math.min(100, Math.round(((done ?? 0) / total) * 100)) : 0
  const canOpen = Boolean(onToggle && children)
  // A running step ticks from 0:00; a finished one that took under a second shows no time.
  const time = status === "running" ? elapsed(elapsed_s) : status === "done" || status === "error" ? elapsedLabel(elapsed_s) : ""

  const chevron = canOpen && (
    <ChevronRightIcon
      aria-hidden="true"
      className={cn(
        "ml-1 inline size-3.5 align-middle text-muted-foreground transition-transform",
        expanded && "rotate-90",
      )}
    />
  )

  // Only the title is the disclosure button, so its name stays the title and the progress bar keeps its
  // role. Its ::after stretches over the row, so the whole row is still the click target.
  return (
    <li className="border-b last:border-b-0">
      <div
        className={cn(
          "relative grid grid-cols-[16px_1fr_auto] items-start gap-2 rounded-md px-2 py-2",
          canOpen && "hover:bg-neutral-soft has-[button:focus-visible]:ring-3 has-[button:focus-visible]:ring-ring",
        )}
      >
        <span className="num pt-px text-xs text-muted-foreground">{index}</span>
        <div className="flex min-w-0 flex-col gap-0.5">
          {canOpen ? (
            <button
              type="button"
              className="text-left font-medium outline-none after:absolute after:inset-0 after:rounded-md"
              aria-expanded={expanded}
              aria-controls={expanded ? detailsId : undefined}
              onClick={onToggle}
            >
              {title}
              {chevron}
            </button>
          ) : (
            <span className="font-medium">{title}</span>
          )}
          {message && (
            <span className={cn("text-xs break-words", status === "error" ? "text-foreground" : "text-muted-foreground")}>
              {message}
            </span>
          )}
          {showBar && (
            <Progress
              value={percent}
              aria-label={`${title}: ${done ?? 0} of ${total}`}
              className="mt-1 h-1 bg-neutral-soft"
            />
          )}
        </div>
        <div className="flex flex-col items-end gap-1">
          <StatusPill status={status} />
          {time && <span className="num text-xs text-muted-foreground">{time}</span>}
        </div>
      </div>
      {canOpen && expanded && (
        <div id={detailsId} className="pr-2 pb-3 pl-8 text-13 text-muted-foreground">
          {children}
        </div>
      )}
    </li>
  )
}
