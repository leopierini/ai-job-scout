import { ApiError } from "@/api/client"
import type { EventOut, RunOut, RunStatus, RunWithEvents, StepName, StepState } from "@/api/types"

/** In run order. */
export const STEP_ORDER: StepName[] = ["cleanup", "search", "filter", "score", "save"]

const PENDING: StepState = { status: "pending", message: "", done: null, total: null, elapsed_s: 0, data: {} }

/** The events known so far plus newly fetched ones, each seq once, in seq order. */
export function mergeEvents(known: EventOut[], incoming: EventOut[]): EventOut[] {
  const bySeq = new Map<number, EventOut>()
  for (const e of known) bySeq.set(e.seq, e)
  for (const e of incoming) bySeq.set(e.seq, e)
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq)
}

/** Where each step stands: its last event, or pending when it has none yet. */
export function stepStates(events: EventOut[]): Record<StepName, StepState> {
  const states = Object.fromEntries(STEP_ORDER.map((s) => [s, PENDING])) as Record<StepName, StepState>
  for (const e of [...events].sort((a, b) => a.seq - b.seq)) {
    states[e.step] = {
      status: e.status,
      message: e.message,
      done: e.done,
      total: e.total,
      elapsed_s: e.elapsed_s,
      data: e.data,
    }
  }
  return states
}

/** What the Stop button reads once pressed: a run stops between searches or between scoring batches. */
export function stoppingText(states: Record<StepName, StepState>): string {
  if (states.search.status === "running") return "Stopping after the current search…"
  if (states.score.status === "running") return "Stopping after the current batch…"
  return "Stopping…"
}

/** "14:02" from a local ISO timestamp such as "2026-11-01T14:02:31". */
export function clockTime(iso: string): string {
  return /T(\d{2}:\d{2})/.exec(iso)?.[1] ?? ""
}

/** A run as the screen should treat it: "interrupted" is a run still marked running that the server says isn't alive. */
export type EffectiveStatus = RunStatus | "interrupted"

/**
 * The run's status, checked against GET /api/runs/active (which applies the server's 20-minute rule). A run whose
 * process died (terminal closed, laptop asleep) stays "running" in the database until the next run starts.
 */
export function effectiveStatus(run: RunOut, activeRunId: number | null): EffectiveStatus {
  return run.status === "running" && activeRunId !== run.id ? "interrupted" : run.status
}

/** An interrupted run's steps: the step that was running ended in an error. */
export function markInterrupted(states: Record<StepName, StepState>): Record<StepName, StepState> {
  const out = { ...states }
  for (const step of STEP_ORDER) {
    if (out[step].status === "running") out[step] = { ...out[step], status: "error" }
  }
  return out
}

export type PollEnd = "ended" | "interrupted" | "gone"

export interface PollOptions {
  runId: number
  /** GET /api/runs/{id}?after=… */
  fetchRun: (after: number) => Promise<RunWithEvents>
  /** GET /api/runs/active: the id of the live run, or null. */
  fetchActive: () => Promise<number | null>
  /** The last event seq already seen. */
  lastSeq: () => number
  onData: (res: RunWithEvents) => void
  /** Called once, when polling stops by itself. */
  onEnd: (how: PollEnd) => void
  /** A failed poll (offline for a moment): polling goes on. */
  onError: (err: unknown) => void
  everyMs?: number
  /** Check that the run is still alive on every Nth poll. */
  activeEvery?: number
}

/** Polls a running run until it ends, is found dead, or disappears. Returns a function that stops polling. */
export function pollRun(o: PollOptions): () => void {
  const everyMs = o.everyMs ?? 1000
  const activeEvery = o.activeEvery ?? 5
  let stopped = false
  let polls = 0
  let timer: ReturnType<typeof setTimeout> | undefined

  const end = (how: PollEnd) => {
    stopped = true
    o.onEnd(how)
  }

  const tick = async () => {
    polls += 1
    try {
      const res = await o.fetchRun(o.lastSeq())
      if (stopped) return
      o.onData(res)
      if (res.run.status !== "running") return end("ended")
      if (polls % activeEvery === 0) {
        const active = await o.fetchActive()
        if (stopped) return
        if (active !== o.runId) {
          // It may simply have ended between the two reads: read it once more before calling it dead.
          const again = await o.fetchRun(o.lastSeq())
          if (stopped) return
          o.onData(again)
          return end(again.run.status === "running" ? "interrupted" : "ended")
        }
      }
    } catch (err) {
      if (stopped) return
      if (err instanceof ApiError && err.status === 404) return end("gone")
      o.onError(err)
    }
    if (!stopped) timer = setTimeout(tick, everyMs)
  }

  timer = setTimeout(tick, everyMs)
  return () => {
    stopped = true
    clearTimeout(timer)
  }
}
