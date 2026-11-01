import { useEffect, useMemo, useState, type ReactNode } from "react"
import { ArrowLeftIcon } from "lucide-react"

import { api, ApiError } from "@/api/client"
import type { CountKey, RunWithEvents, StepName } from "@/api/types"
import { EmptyState } from "@/components/EmptyState"
import { ErrorNote } from "@/components/ErrorNote"
import { stepDetails } from "@/components/StepDetails"
import { StepItem } from "@/components/StepItem"
import { money, STEP_TITLES } from "@/lib/format"
import { billedCount, durationText, startedText, triggerLabel } from "@/lib/history"
import { effectiveStatus, markInterrupted, STEP_ORDER, stepStates } from "@/lib/run"
import { cn } from "@/lib/utils"
import { HistoryBar, RunStatusPill } from "./parts"

const POLL_MS = 2000

type Load =
  | { kind: "loading" }
  | { kind: "gone" }
  | { kind: "error"; message: string }
  | { kind: "ready"; res: RunWithEvents; activeId: number | null }

/** One run, read from the server; read again every couple of seconds while it is still going. */
function useRunDetail(id: number | null) {
  const [load, setLoad] = useState<Load>({ kind: "loading" })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (id === null) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const read = async () => {
      try {
        const res = await api.get<RunWithEvents>(`/api/runs/${id}?after=0`)
        const activeId =
          res.run.status === "running" ? (await api.get<{ run_id: number | null }>("/api/runs/active")).run_id : null
        if (cancelled) return
        setLoad({ kind: "ready", res, activeId })
        if (effectiveStatus(res.run, activeId) === "running") timer = setTimeout(read, POLL_MS)
      } catch (err) {
        if (cancelled) return
        if (err instanceof ApiError && err.status === 404) setLoad({ kind: "gone" })
        else setLoad({ kind: "error", message: err instanceof ApiError ? err.message : String(err) })
      }
    }
    void read()
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [id, attempt])

  const retry = () => {
    setLoad({ kind: "loading" })
    setAttempt((a) => a + 1)
  }
  return { load, retry }
}

/** Shown only when the run had some. */
const WHEN_NONZERO: [CountKey, string][] = [
  ["archived", "Archived"],
  ["duplicates", "Duplicates"],
  ["below", "Below threshold"],
  ["failed_scoring", "Failed to score"],
]

function Fact({ term, mono = true, children }: { term: string; mono?: boolean; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-muted-foreground">{term}</dt>
      <dd className={cn("text-sm", mono && "num")}>{children}</dd>
    </div>
  )
}

function Detail({ res, activeId }: { res: RunWithEvents; activeId: number | null }) {
  const { run, events } = res
  const status = effectiveStatus(run, activeId)
  const states = useMemo(() => {
    const s = stepStates(events)
    return status === "interrupted" ? markInterrupted(s) : s
  }, [events, status])
  const [open, setOpen] = useState<Partial<Record<StepName, boolean>>>({})
  const c = run.counts
  const counts: [string, number][] = [
    ["Searched", c.searched ?? 0],
    ["Billed", billedCount(c)],
    ["New", c.new ?? 0],
    ["Scored", c.scored ?? 0],
    ["Kept", c.kept ?? 0],
    ...WHEN_NONZERO.filter(([key]) => (c[key] ?? 0) > 0).map(([key, label]): [string, number] => [label, c[key] ?? 0]),
  ]

  return (
    <>
      <div className="mt-3 flex flex-col gap-1">
        <h1 className="text-xl font-semibold">Run {run.id}</h1>
        <p className="text-muted-foreground">Started {startedText(run.started_at)}</p>
      </div>

      <dl className="mt-5 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
        <Fact term="Trigger" mono={false}>
          {triggerLabel(run.trigger)}
        </Fact>
        <Fact term="Status" mono={false}>
          <RunStatusPill status={status} />
        </Fact>
        <Fact term="Duration">{durationText(run, status) || "Unknown"}</Fact>
        <Fact term="Cost">{money(run.cost_usd)}</Fact>
      </dl>

      <section aria-labelledby="counts-heading" className="mt-6">
        <h2 id="counts-heading" className="text-13 font-semibold text-muted-foreground">
          Counts
        </h2>
        <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
          {counts.map(([label, n]) => (
            <Fact key={label} term={label}>
              {n}
            </Fact>
          ))}
        </dl>
      </section>

      {status === "interrupted" ? (
        <p className="mt-6 text-13 text-muted-foreground">
          <span className="font-medium text-foreground">Interrupted:</span> the app stopped before this run finished.
        </p>
      ) : (
        run.error && <ErrorNote className="mt-6" message={run.error} />
      )}

      <section aria-labelledby="steps-heading" className="mt-6">
        <h2 id="steps-heading" className="text-13 font-semibold text-muted-foreground">
          Steps
        </h2>
        <ol aria-label="Run steps" className="mt-2">
          {STEP_ORDER.map((step, i) => {
            const details = stepDetails(step, states[step])
            return (
              <StepItem
                key={step}
                index={i + 1}
                title={STEP_TITLES[step]}
                state={states[step]}
                expanded={Boolean(open[step])}
                onToggle={details ? () => setOpen((o) => ({ ...o, [step]: !o[step] })) : undefined}
              >
                {details}
              </StepItem>
            )
          })}
        </ol>
      </section>
    </>
  )
}

/** A run's five-step log, read-only (#/history/<id>). */
export function RunDetail({ id, sample }: { id: string; sample: boolean }) {
  const runId = /^\d+$/.test(id) ? Number(id) : null
  const { load, retry } = useRunDetail(runId)

  return (
    <>
      <HistoryBar sample={sample} />
      <main className="mx-auto w-full max-w-3xl px-4 py-6 md:px-5">
        <a
          href="#/history"
          className="inline-flex items-center gap-1 rounded-sm text-13 font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring"
        >
          <ArrowLeftIcon className="size-4" aria-hidden />
          All runs
        </a>
        {runId === null || load.kind === "gone" ? (
          <EmptyState
            title="That run doesn't exist."
            body="It may have been deleted when the app's data was reset. Pick another run from the list."
          />
        ) : load.kind === "loading" ? (
          <p className="mt-4 text-muted-foreground" aria-live="polite">
            Loading run…
          </p>
        ) : load.kind === "error" ? (
          <ErrorNote className="mt-4" message={load.message} onRetry={retry} />
        ) : (
          <Detail res={load.res} activeId={load.activeId} />
        )}
      </main>
    </>
  )
}
