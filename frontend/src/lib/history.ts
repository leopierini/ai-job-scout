import type { RunOut } from "@/api/types"
import { elapsed, relativeDay } from "@/lib/format"
import { clockTime, type EffectiveStatus } from "@/lib/run"

/**
 * Who started a run, in words. Triggers are "app", "app:rescore", "cli" and "cli:rescore"; anything else
 * (a trigger a later version adds) is shown as it is.
 */
export function triggerLabel(trigger: string): string {
  const [source, kind, ...rest] = trigger.split(":")
  const who = source === "app" ? "App" : source === "cli" ? "Command line" : null
  if (who === null || rest.length > 0 || (kind !== undefined && kind !== "rescore")) return trigger
  return kind === "rescore" ? `${who} re-score` : who
}

/** Searches billed across both sources. */
export function billedCount(counts: RunOut["counts"]): number {
  return (counts.billed_linkedin ?? 0) + (counts.billed_indeed ?? 0)
}

/** Whole seconds from start to finish (both are local timestamps); null for a run that hasn't finished. */
export function runSeconds(run: Pick<RunOut, "started_at" | "finished_at">): number | null {
  if (!run.finished_at) return null
  const start = new Date(run.started_at).getTime()
  const end = new Date(run.finished_at).getTime()
  if (Number.isNaN(start) || Number.isNaN(end)) return null
  return Math.max(0, Math.round((end - start) / 1000))
}

/** "2:31" for a finished run, "Running" for the live one, "Interrupted" for one whose process died. */
export function durationText(run: Pick<RunOut, "started_at" | "finished_at">, status: EffectiveStatus): string {
  if (status === "running") return "Running"
  if (status === "interrupted") return "Interrupted"
  const seconds = runSeconds(run)
  return seconds === null ? "" : elapsed(seconds)
}

/** "today 14:02" or "12 Oct 14:02": when a run started. */
export function startedText(iso: string): string {
  return `${relativeDay(iso)} ${clockTime(iso)}`.trim()
}
