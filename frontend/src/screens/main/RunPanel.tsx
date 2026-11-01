import { useEffect, useMemo, useState } from "react"

import type { RunOut, StepName, StepState } from "@/api/types"
import { ErrorNote } from "@/components/ErrorNote"
import { ReadinessLine } from "@/components/ReadinessLine"
import { stepDetails } from "@/components/StepDetails"
import { StepItem } from "@/components/StepItem"
import { Button } from "@/components/ui/button"
import { money, relativeDay, STEP_TITLES } from "@/lib/format"
import type { Missing } from "@/lib/readiness"
import { clockTime, markInterrupted, STEP_ORDER, stepStates, stoppingText, type EffectiveStatus } from "@/lib/run"
import type { RunControl } from "./useRun"

const SETTINGS_HINT = "Fix it in Settings, then press Find jobs again."

function hintFor(error: string): string | undefined {
  return /settings/i.test(error) ? SETTINGS_HINT : undefined
}

/** Seconds since a local timestamp such as "2026-11-01T14:02:31" (the server runs on this machine). */
function secondsSince(iso: string, now: number): number {
  const t = new Date(iso).getTime()
  return Number.isNaN(t) ? 0 : (now - t) / 1000
}

/** Ticks once a second while `active`. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [active])
  return now
}

function header(run: RunOut | null, running: boolean): string {
  if (!run) return "No runs yet"
  const time = clockTime(run.started_at)
  if (running) return `Run started ${time}`
  return `Last run · ${relativeDay(run.started_at)} ${time}`
}

const INTERRUPTED_TEXT = "the app stopped before this run finished. Press Find jobs to start a new one."

function Summary({ run, status }: { run: RunOut; status: EffectiveStatus }) {
  if (status === "interrupted") {
    return (
      <p className="text-13 text-muted-foreground" aria-live="polite">
        <span className="font-medium text-foreground">Interrupted</span> · {INTERRUPTED_TEXT}
      </p>
    )
  }
  if (run.status === "error") {
    const message = run.error ?? "The run stopped with an error. Check the terminal window for details."
    return <ErrorNote message={message} hint={hintFor(message)} />
  }
  const word = run.status === "cancelled" ? "Stopped" : "Done"
  return (
    <p className="text-13 text-muted-foreground" aria-live="polite">
      <span className="font-medium text-foreground">{word}</span> · <span className="num">{run.counts.kept ?? 0}</span>{" "}
      kept · <span className="num">{money(run.cost_usd)}</span>
    </p>
  )
}

/** The run on the main screen. `missing` (lib/readiness) is listed under a refused start that was a setup problem. */
export function RunPanel({ control, missing }: { control: RunControl; missing: Missing[] }) {
  const { run, status, events, running, stopping, startError, startErrorIsConfig, pollError } = control
  const interrupted = status === "interrupted"
  const states = useMemo(() => {
    const s = stepStates(events)
    return interrupted ? markInterrupted(s) : s
  }, [events, interrupted])
  const [open, setOpen] = useState<Partial<Record<StepName, boolean>>>({})
  const now = useNow(running)

  // The running step's time ticks here: the run's start, less the time the finished steps took.
  const finishedSeconds = STEP_ORDER.filter((s) => states[s].status !== "running").reduce(
    (sum, s) => sum + states[s].elapsed_s,
    0,
  )
  const live = (state: StepState): StepState => {
    if (!running || state.status !== "running" || !run) return state
    return { ...state, elapsed_s: Math.max(state.elapsed_s, secondsSince(run.started_at, now) - finishedSeconds) }
  }

  return (
    <section aria-labelledby="run-heading" className="flex flex-col gap-3">
      <h2 id="run-heading" className="text-13 font-semibold text-muted-foreground">
        {header(run, running)}
      </h2>
      <ol aria-label="Run steps">
        {STEP_ORDER.map((step, i) => {
          const details = stepDetails(step, states[step])
          return (
            <StepItem
              key={step}
              index={i + 1}
              title={STEP_TITLES[step]}
              state={live(states[step])}
              expanded={Boolean(open[step])}
              onToggle={details ? () => setOpen((o) => ({ ...o, [step]: !o[step] })) : undefined}
            >
              {details}
            </StepItem>
          )
        })}
      </ol>
      {pollError && running && (
        <p className="text-13 text-muted-foreground" role="status">
          {pollError}
        </p>
      )}
      {running && (
        <Button
          variant="outline"
          size="sm"
          className="self-start bg-card"
          disabled={stopping}
          onClick={() => void control.stop()}
        >
          {stopping ? stoppingText(states) : "Stop run"}
        </Button>
      )}
      {!running && startError && (
        <ErrorNote
          message={startError}
          hint={startErrorIsConfig && missing.length > 0 ? undefined : hintFor(startError)}
        >
          {startErrorIsConfig && <ReadinessLine missing={missing} lead="Fix these in Settings:" className="text-sm text-muted-foreground" />}
        </ErrorNote>
      )}
      {!running && !startError && run && status && <Summary run={run} status={status} />}
    </section>
  )
}
