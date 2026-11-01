import { useEffect, useState, type ReactNode } from "react"

import { api, ApiError } from "@/api/client"
import type { RunOut } from "@/api/types"
import { EmptyState } from "@/components/EmptyState"
import { ErrorNote } from "@/components/ErrorNote"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { money } from "@/lib/format"
import { billedCount, durationText, startedText, triggerLabel } from "@/lib/history"
import { effectiveStatus } from "@/lib/run"
import { cn } from "@/lib/utils"
import { RunDetail } from "./history/RunDetail"
import { HistoryBar, RunStatusPill } from "./history/parts"

const LIMIT = 100
/** Counts and duration are on the run's own page on phones. */
const WIDE_ONLY = "hidden md:table-cell"

type Load =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; runs: RunOut[]; activeId: number | null }

function useRuns() {
  const [load, setLoad] = useState<Load>({ kind: "loading" })
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const [runs, active] = await Promise.all([
          api.get<RunOut[]>(`/api/runs?limit=${LIMIT}`),
          api.get<{ run_id: number | null }>("/api/runs/active"),
        ])
        if (!cancelled) setLoad({ kind: "ready", runs, activeId: active.run_id })
      } catch (err) {
        if (!cancelled) setLoad({ kind: "error", message: err instanceof ApiError ? err.message : String(err) })
      }
    })()
    return () => {
      cancelled = true
    }
  }, [attempt])
  const retry = () => {
    setLoad({ kind: "loading" })
    setAttempt((a) => a + 1)
  }
  return { load, retry }
}

function Num({ children, className }: { children: ReactNode; className?: string }) {
  return <TableCell className={cn("num text-right", className)}>{children}</TableCell>
}

function RunsTable({ runs, activeId }: { runs: RunOut[]; activeId: number | null }) {
  return (
    <Table>
      <caption className="sr-only">Runs, newest first. Open a run to see its steps.</caption>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead>Started</TableHead>
          <TableHead>Trigger</TableHead>
          <TableHead>Status</TableHead>
          <TableHead className={cn("text-right", WIDE_ONLY)}>Duration</TableHead>
          <TableHead className={cn("text-right", WIDE_ONLY)}>Searched</TableHead>
          <TableHead className={cn("text-right", WIDE_ONLY)}>Billed</TableHead>
          <TableHead className={cn("text-right", WIDE_ONLY)}>New</TableHead>
          <TableHead className={cn("text-right", WIDE_ONLY)}>Scored</TableHead>
          <TableHead className="text-right">Kept</TableHead>
          <TableHead className="text-right">Cost</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {runs.map((run) => {
          const status = effectiveStatus(run, activeId)
          const c = run.counts
          return (
            <TableRow key={run.id} className="relative">
              <TableCell>
                <a
                  href={`#/history/${run.id}`}
                  className="rounded-sm font-medium whitespace-nowrap underline-offset-2 outline-none after:absolute after:inset-0 hover:underline focus-visible:ring-3 focus-visible:ring-ring"
                >
                  {startedText(run.started_at)}
                </a>
              </TableCell>
              {/* A non-breaking hyphen, so a narrow phone wraps before "re-score" and not inside it. */}
              <TableCell className="whitespace-normal">{triggerLabel(run.trigger).replace("re-score", "re\u2011score")}</TableCell>
              <TableCell>
                <RunStatusPill status={status} />
              </TableCell>
              <Num className={WIDE_ONLY}>{durationText(run, status)}</Num>
              <Num className={WIDE_ONLY}>{c.searched ?? 0}</Num>
              <Num className={WIDE_ONLY}>{billedCount(c)}</Num>
              <Num className={WIDE_ONLY}>{c.new ?? 0}</Num>
              <Num className={WIDE_ONLY}>{c.scored ?? 0}</Num>
              <Num>{c.kept ?? 0}</Num>
              <Num>{money(run.cost_usd)}</Num>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}

/** Every past run, newest first (#/history), or one run's steps (#/history/<id>). */
export function History({ id, sample }: { id?: string; sample: boolean }) {
  if (id !== undefined) return <RunDetail id={id} sample={sample} />
  return <RunList sample={sample} />
}

function RunList({ sample }: { sample: boolean }) {
  const { load, retry } = useRuns()
  return (
    <>
      <HistoryBar sample={sample} />
      <main className="mx-auto w-full max-w-3xl px-4 py-6 md:px-5">
        <h1 className="text-xl font-semibold">Run history</h1>
        {load.kind === "loading" && (
          <p className="mt-4 text-muted-foreground" aria-live="polite">
            Loading runs…
          </p>
        )}
        {load.kind === "error" && <ErrorNote className="mt-4" message={load.message} onRetry={retry} />}
        {load.kind === "ready" && load.runs.length === 0 && (
          <EmptyState title="No runs yet." body="Go back to your jobs and press Find jobs. Each run is listed here." />
        )}
        {load.kind === "ready" && load.runs.length > 0 && (
          <div className="mt-4 rounded-lg border bg-card">
            <RunsTable runs={load.runs} activeId={load.activeId} />
          </div>
        )}
      </main>
    </>
  )
}
