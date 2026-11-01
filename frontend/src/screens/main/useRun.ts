import { useCallback, useEffect, useRef, useState } from "react"
import { toast } from "sonner"

import { api, ApiError } from "@/api/client"
import type { EventOut, RunOut, RunWithEvents, StartRunBody } from "@/api/types"
import { effectiveStatus, mergeEvents, pollRun, type EffectiveStatus } from "@/lib/run"

export const RUN_IN_PROGRESS = "A run is already in progress."

export type RunLoad = "loading" | "error" | "ready"

function messageOf(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err)
}

async function activeRunId(): Promise<number | null> {
  return (await api.get<{ run_id: number | null }>("/api/runs/active")).run_id
}

const latestRun = () => api.get<RunWithEvents | null>("/api/runs/latest")

/**
 * The latest run, and whether it is a dead one still marked running. A mismatch is read twice, so a run that
 * just ended (or a new one that just started) isn't taken for a crashed one.
 */
async function latestWithLiveness(): Promise<{ res: RunWithEvents | null; dead: boolean }> {
  const res = await latestRun()
  if (!res || res.run.status !== "running") return { res, dead: false }
  const active = await activeRunId()
  if (effectiveStatus(res.run, active) === "running") return { res, dead: false }
  const again = await latestRun()
  return { res: again, dead: again !== null && effectiveStatus(again.run, active) === "interrupted" }
}

/**
 * The latest run and its events: loaded once, then polled every second while it is running. A run still marked
 * running that the server says isn't alive (its process died) is shown as interrupted, and polling stops. `onEnd`
 * is called when a run this page watched (or started) has ended, so the screen can refresh its jobs.
 */
export function useRun(onEnd: () => void) {
  const [load, setLoad] = useState<RunLoad>("loading")
  const [loadError, setLoadError] = useState("")
  const [run, setRun] = useState<RunOut | null>(null)
  const [events, setEvents] = useState<EventOut[]>([])
  /** The id of a run found dead while marked running. */
  const [deadRunId, setDeadRunId] = useState<number | null>(null)
  const [starting, setStarting] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)
  /** The server refused the start because of the setup (a 400: a missing key, token or profile). */
  const [startErrorIsConfig, setStartErrorIsConfig] = useState(false)
  const [pollError, setPollError] = useState<string | null>(null)

  const eventsRef = useRef<EventOut[]>([])
  const onEndRef = useRef(onEnd)
  useEffect(() => {
    onEndRef.current = onEnd
  }, [onEnd])

  const apply = useCallback((res: RunWithEvents | null, reset: boolean) => {
    const merged = res ? mergeEvents(reset ? [] : eventsRef.current, res.events) : []
    eventsRef.current = merged
    setEvents(merged)
    setRun(res ? res.run : null)
  }, [])

  const loadLatest = useCallback(async () => {
    setLoad("loading")
    try {
      const { res, dead } = await latestWithLiveness()
      apply(res, true) // null: no runs (or they were all deleted)
      setDeadRunId(res && dead ? res.run.id : null)
      setStopping(false)
      setLoad("ready")
    } catch (err) {
      setLoadError(messageOf(err))
      setLoad("error")
    }
  }, [apply])

  useEffect(() => {
    void loadLatest()
  }, [loadLatest])

  /** Show one run from its first event: the one just started, or the one already going. */
  const follow = useCallback(
    async (runId: number) => {
      const res = await api.get<RunWithEvents>(`/api/runs/${runId}?after=0`)
      apply(res, true)
      setDeadRunId(null)
      setStopping(false)
      if (res.run.status !== "running") onEndRef.current() // a sample run can end before the first poll
    },
    [apply],
  )

  const runId = run?.id
  const status: EffectiveStatus | null = run ? (run.id === deadRunId ? "interrupted" : run.status) : null
  const running = status === "running"

  useEffect(() => {
    if (runId === undefined || !running) return
    return pollRun({
      runId,
      fetchRun: (after) => api.get<RunWithEvents>(`/api/runs/${runId}?after=${after}`),
      fetchActive: activeRunId,
      lastSeq: () => eventsRef.current.at(-1)?.seq ?? 0,
      onData: (res) => {
        setPollError(null)
        apply(res, false)
      },
      onEnd: (how) => {
        setStopping(false)
        if (how === "gone") {
          void loadLatest() // the run was deleted (a reset): show whatever is latest now
          return
        }
        if (how === "interrupted") setDeadRunId(runId)
        onEndRef.current()
      },
      onError: (err) => setPollError(messageOf(err)), // offline for a moment: keep trying
    })
  }, [runId, running, apply, loadLatest])

  const start = useCallback(
    async (windowDays: number | null) => {
      setStartError(null)
      setStartErrorIsConfig(false)
      setStarting(true)
      try {
        const body: StartRunBody = { kind: "run", window_days: windowDays }
        const { run_id } = await api.send<{ run_id: number }>("POST", "/api/runs", body)
        await follow(run_id)
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          toast(RUN_IN_PROGRESS)
          try {
            const active = await activeRunId()
            if (active !== null) await follow(active)
          } catch (inner) {
            setStartError(messageOf(inner))
          }
        } else {
          setStartError(messageOf(err))
          setStartErrorIsConfig(err instanceof ApiError && err.status === 400)
        }
      } finally {
        setStarting(false)
      }
    },
    [follow],
  )

  const stop = useCallback(async () => {
    if (runId === undefined) return
    setStopping(true)
    try {
      await api.send("POST", `/api/runs/${runId}/cancel`)
      // Cancelling a run nobody is running is a no-op on the server. If it isn't alive any more, read it again:
      // either it has just ended, or its process was gone and it was interrupted.
      if ((await activeRunId()) !== runId) {
        const again = await api.get<RunWithEvents>(`/api/runs/${runId}?after=0`)
        apply(again, true)
        if (again.run.status === "running") setDeadRunId(runId)
        setStopping(false)
        onEndRef.current()
      }
    } catch (err) {
      setStopping(false)
      toast.error(messageOf(err))
    }
  }, [runId, apply])

  return {
    load,
    loadError,
    retryLoad: loadLatest,
    run,
    events,
    /** The run's status, with "interrupted" for a run marked running whose process is gone; null with no run. */
    status,
    running,
    starting,
    stopping,
    startError,
    startErrorIsConfig,
    pollError,
    start,
    stop,
  }
}

export type RunControl = ReturnType<typeof useRun>
