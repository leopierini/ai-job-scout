import type { ReactNode } from "react"

import type { StepName, StepState } from "@/api/types"

const MAX_REASONS = 10

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1)
}

interface Area {
  label: string
  postings: number
  billed: number
  complete: boolean
  error: string | null
}

function asList<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : []
}

/** The details a step opens to, or null when it has none worth opening. */
export function stepDetails(step: StepName, state: StepState): ReactNode {
  if (step === "search") {
    const areas = asList<Area>(state.data.areas)
    if (!areas.length) return null
    return (
      <ul className="flex flex-col gap-1">
        {areas.map((a) => (
          <li key={a.label} className="break-words">
            <span className="font-medium text-foreground">{a.label}</span>: <span className="num">{a.postings}</span>{" "}
            postings, <span className="num">{a.billed}</span> billed,{" "}
            {a.complete ? "complete" : <span className="text-foreground">{a.error ?? "not complete"}</span>}
          </li>
        ))}
      </ul>
    )
  }
  if (step === "cleanup") {
    const archived = asList<{ reason: string }>(state.data.archived)
    if (!archived.length) return null
    const more = archived.length - MAX_REASONS
    return (
      <>
        <p>
          <span className="num">{archived.length}</span> archived:
        </p>
        <ul className="mt-1 flex list-disc flex-col gap-0.5 pl-4">
          {archived.slice(0, MAX_REASONS).map((a, i) => (
            <li key={i}>{capitalise(a.reason)}</li>
          ))}
        </ul>
        {more > 0 && (
          <p className="mt-1">
            and <span className="num">{more}</span> more
          </p>
        )}
      </>
    )
  }
  if (step === "score") {
    const errors = asList<string>(state.data.errors)
    if (!errors.length) return null
    return (
      <ul className="flex list-disc flex-col gap-0.5 pl-4 text-foreground">
        {errors.map((e, i) => (
          <li key={i} className="break-words">
            {e}
          </li>
        ))}
      </ul>
    )
  }
  return null
}
