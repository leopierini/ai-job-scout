import { afterEach, beforeEach, describe, expect, test, vi } from "vitest"

import { ApiError } from "@/api/client"
import type { EventOut, RunOut, RunStatus, RunWithEvents, StepName, StepStatus } from "@/api/types"
import {
  clockTime,
  effectiveStatus,
  markInterrupted,
  mergeEvents,
  pollRun,
  stepStates,
  stoppingText,
  type PollEnd,
} from "./run"

function ev(seq: number, step: StepName, status: StepStatus = "running", extra: Partial<EventOut> = {}): EventOut {
  return { seq, step, status, message: `event ${seq}`, done: null, total: null, elapsed_s: 0, data: {}, ...extra }
}

describe("mergeEvents", () => {
  test("mergeEvents dedupes by seq and keeps order", () => {
    const merged = mergeEvents([ev(1, "cleanup"), ev(2, "cleanup", "done")], [ev(2, "cleanup", "done"), ev(3, "search")])
    expect(merged.map((e) => e.seq)).toEqual([1, 2, 3])
  })

  test("sorts events that arrive out of order", () => {
    expect(mergeEvents([ev(3, "search")], [ev(1, "cleanup"), ev(2, "cleanup")]).map((e) => e.seq)).toEqual([1, 2, 3])
  })
})

describe("stepStates", () => {
  test("stepStates takes the last event per step, pending otherwise", () => {
    const states = stepStates([
      ev(1, "cleanup"),
      ev(2, "cleanup", "done", { message: "3 archived", elapsed_s: 4, data: { archived: [] } }),
      ev(3, "search", "running", { message: "UK: 1 of 2 searches", done: 1, total: 4 }),
    ])
    expect(states.cleanup).toEqual({
      status: "done",
      message: "3 archived",
      done: null,
      total: null,
      elapsed_s: 4,
      data: { archived: [] },
    })
    expect(states.search.status).toBe("running")
    expect(states.search.done).toBe(1)
    expect(states.search.total).toBe(4)
    for (const step of ["filter", "score", "save"] as const) {
      expect(states[step]).toEqual({ status: "pending", message: "", done: null, total: null, elapsed_s: 0, data: {} })
    }
  })
})

describe("stoppingText", () => {
  test("stoppingText follows the running step", () => {
    expect(stoppingText(stepStates([ev(1, "cleanup", "done"), ev(2, "search")]))).toBe(
      "Stopping after the current search…",
    )
    expect(stoppingText(stepStates([ev(1, "search", "done"), ev(2, "score")]))).toBe(
      "Stopping after the current batch…",
    )
    expect(stoppingText(stepStates([ev(1, "cleanup")]))).toBe("Stopping…")
    expect(stoppingText(stepStates([]))).toBe("Stopping…")
  })
})

describe("clockTime", () => {
  test("hours and minutes of a local timestamp", () => {
    expect(clockTime("2026-11-01T14:02:31")).toBe("14:02")
    expect(clockTime("")).toBe("")
  })
})

function runOut(id: number, status: RunStatus): RunOut {
  return {
    id,
    trigger: "app",
    started_at: "2026-11-01T14:02:00",
    finished_at: null,
    status,
    counts: {},
    cost_usd: 0,
    error: null,
  }
}

describe("effectiveStatus", () => {
  test("a running run the server says is alive is running", () => {
    expect(effectiveStatus(runOut(7, "running"), 7)).toBe("running")
  })

  test("a running run that isn't the active one crashed: interrupted", () => {
    expect(effectiveStatus(runOut(7, "running"), null)).toBe("interrupted")
    expect(effectiveStatus(runOut(7, "running"), 8)).toBe("interrupted")
  })

  test("an ended run keeps its status", () => {
    expect(effectiveStatus(runOut(7, "done"), null)).toBe("done")
    expect(effectiveStatus(runOut(7, "error"), null)).toBe("error")
  })
})

describe("markInterrupted", () => {
  test("the running step becomes an error; the others stay as they were", () => {
    const states = markInterrupted(stepStates([ev(1, "cleanup", "done"), ev(2, "search", "running")]))
    expect(states.cleanup.status).toBe("done")
    expect(states.search.status).toBe("error")
    expect(states.filter.status).toBe("pending")
  })
})

describe("pollRun", () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  function poll(statuses: RunStatus[], active: number | null, activeEvery = 5) {
    const ends: PollEnd[] = []
    let i = 0
    const fetchRun = vi.fn(
      async (): Promise<RunWithEvents> => ({ run: runOut(7, statuses[Math.min(i++, statuses.length - 1)]), events: [] }),
    )
    const fetchActive = vi.fn(async () => active)
    pollRun({
      runId: 7,
      fetchRun,
      fetchActive,
      lastSeq: () => 0,
      onData: () => {},
      onEnd: (how) => ends.push(how),
      onError: () => {},
      activeEvery,
    })
    return { ends, fetchRun, fetchActive }
  }

  test("stops polling once the run has ended", async () => {
    const { ends, fetchRun } = poll(["running", "running", "done"], 7)
    await vi.advanceTimersByTimeAsync(3000)
    expect(fetchRun).toHaveBeenCalledTimes(3)
    expect(ends).toEqual(["ended"])
    await vi.advanceTimersByTimeAsync(10_000)
    expect(fetchRun).toHaveBeenCalledTimes(3)
  })

  test("stops polling a run that is still marked running but no longer alive", async () => {
    const { ends, fetchRun, fetchActive } = poll(["running"], null, 2)
    await vi.advanceTimersByTimeAsync(2000)
    expect(fetchActive).toHaveBeenCalledTimes(1)
    expect(ends).toEqual(["interrupted"])
    await vi.advanceTimersByTimeAsync(10_000)
    expect(fetchRun).toHaveBeenCalledTimes(3) // two polls, then one re-read before calling it dead
  })

  test("a run that ends between the two reads counts as ended, not interrupted", async () => {
    const { ends } = poll(["running", "running", "done"], null, 2)
    await vi.advanceTimersByTimeAsync(10_000)
    expect(ends).toEqual(["ended"])
  })

  test("keeps polling a live run, and stops when the run is gone", async () => {
    const ends: PollEnd[] = []
    let calls = 0
    pollRun({
      runId: 7,
      fetchRun: async () => {
        calls += 1
        if (calls === 3) throw new ApiError(404, "That run no longer exists.")
        return { run: runOut(7, "running"), events: [] }
      },
      fetchActive: async () => 7,
      lastSeq: () => 0,
      onData: () => {},
      onEnd: (how) => ends.push(how),
      onError: () => {},
      activeEvery: 1,
    })
    await vi.advanceTimersByTimeAsync(10_000)
    expect(calls).toBe(3)
    expect(ends).toEqual(["gone"])
  })
})
