import type { JobOut } from "@/api/types"

export type SortKey = "score" | "posted" | "starts"
export type SortDir = "asc" | "desc"

export interface JobFilter {
  /** Matched against the title, company and location, case-insensitive. */
  text: string
  /** "all", or a job's source such as "linkedin" or "indeed". */
  source: string
  /** Jobs scoring below this (or unscored) are hidden; null shows every job. */
  minScore: number | null
}

function sortValue(job: JobOut, key: SortKey): number | string | null {
  if (key === "score") return job.score
  const value = key === "posted" ? job.posted : job.start_date
  return value ? value : null
}

/** A sorted copy. Jobs without the value (no score, no date) always come last, in either direction. */
export function sortJobs(jobs: JobOut[], key: SortKey, dir: SortDir): JobOut[] {
  const sign = dir === "asc" ? 1 : -1
  return [...jobs].sort((a, b) => {
    const va = sortValue(a, key)
    const vb = sortValue(b, key)
    if (va === null || vb === null) return va === vb ? 0 : va === null ? 1 : -1
    if (va < vb) return -sign
    if (va > vb) return sign
    return 0
  })
}

export function filterJobs(jobs: JobOut[], { text, source, minScore }: JobFilter): JobOut[] {
  const needle = text.trim().toLowerCase()
  return jobs.filter((j) => {
    if (source !== "all" && j.source !== source) return false
    if (minScore !== null && (j.score === null || j.score < minScore)) return false
    if (!needle) return true
    return [j.title, j.company, j.location].some((field) => field.toLowerCase().includes(needle))
  })
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

/** "2027-09-01" → "Sep 2027"; "" when there is no date. */
export function monthYear(iso: string): string {
  const match = /^(\d{4})-(\d{2})/.exec(iso)
  const month = match ? MONTHS[Number(match[2]) - 1] : undefined
  return match && month ? `${month} ${match[1]}` : ""
}

/** "2026-11-14" → "14 Nov 2026"; "" when there is no date. */
export function fullDay(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso)
  const month = match ? MONTHS[Number(match[2]) - 1] : undefined
  return match && month ? `${Number(match[3])} ${month} ${match[1]}` : ""
}

const SOURCE_LABELS: Record<string, string> = { linkedin: "LinkedIn", indeed: "Indeed", sample: "Sample" }

export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source
}
