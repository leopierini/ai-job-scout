import type { Status, StepName, StepStatus } from "@/api/types"

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 })

/** money(1.8) → "$1.80" */
export function money(amount: number): string {
  return usd.format(amount)
}

/** elapsed(151) → "2:31"; over an hour, "1:02:05". */
export function elapsed(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = String(total % 60).padStart(2, "0")
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`
}

/** A finished step's time: nothing for one that took under a second, otherwise elapsed(). */
export function elapsedLabel(seconds: number): string {
  return seconds < 1 ? "" : elapsed(seconds)
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
const DAY_MS = 86_400_000

/** The calendar day of an ISO date or timestamp, as a UTC day number; null when it can't be read. */
function dayNumber(iso: string): { n: number; y: number; m: number; d: number } | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso)
  if (!match) return null
  const [y, m, d] = [Number(match[1]), Number(match[2]), Number(match[3])]
  const t = Date.UTC(y, m - 1, d)
  return Number.isNaN(t) ? null : { n: t / DAY_MS, y, m, d }
}

/** The local date today, as "YYYY-MM-DD". */
export function todayIso(now: Date = new Date()): string {
  const pad = (v: number) => String(v).padStart(2, "0")
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

/**
 * "today", "yesterday" or "3 days ago" within a week; otherwise "12 Oct" (or "12 Oct 2025" in
 * another year). Both arguments are ISO dates or timestamps; only their calendar day counts.
 */
export function relativeDay(iso: string, today: string = todayIso()): string {
  const day = dayNumber(iso)
  const now = dayNumber(today)
  if (!day || !now) return ""
  const diff = now.n - day.n
  if (diff === 0) return "today"
  if (diff === 1) return "yesterday"
  if (diff > 1 && diff < 7) return `${diff} days ago`
  const label = `${day.d} ${MONTHS[day.m - 1]}`
  return day.y === now.y ? label : `${label} ${day.y}`
}

export const STATUS_LABELS: Record<Status, string> = {
  to_review: "To review",
  interested: "Interested",
  not_interested: "Not interested",
  applied: "Applied",
  interviewing: "Interviewing",
  offer: "Offer",
  rejected: "Rejected",
}

/** In run order. */
export const STEP_TITLES: Record<StepName, string> = {
  cleanup: "Clean up old jobs",
  search: "Search for jobs",
  filter: "Remove duplicates",
  score: "Score against your profile",
  save: "Save results",
}

export const STEP_STATUS_LABELS: Record<StepStatus, string> = {
  pending: "Pending",
  running: "Running",
  done: "Done",
  error: "Error",
  skipped: "Skipped",
}
