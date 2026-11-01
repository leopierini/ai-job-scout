import { describe, expect, test } from "vitest"

import { billedCount, durationText, runSeconds, startedText, triggerLabel } from "./history"

describe("triggerLabel", () => {
  test("names the four triggers a run is started with", () => {
    expect(triggerLabel("app")).toBe("App")
    expect(triggerLabel("app:rescore")).toBe("App re-score")
    expect(triggerLabel("cli")).toBe("Command line")
    expect(triggerLabel("cli:rescore")).toBe("Command line re-score")
  })

  test("shows any other trigger as it is", () => {
    expect(triggerLabel("scheduler")).toBe("scheduler")
    expect(triggerLabel("app:nightly")).toBe("app:nightly")
    expect(triggerLabel("cli:rescore:extra")).toBe("cli:rescore:extra")
    expect(triggerLabel("")).toBe("")
  })
})

describe("billedCount", () => {
  test("adds both sources and treats a missing key as zero", () => {
    expect(billedCount({ billed_linkedin: 12, billed_indeed: 5 })).toBe(17)
    expect(billedCount({ billed_indeed: 3 })).toBe(3)
    expect(billedCount({})).toBe(0)
  })
})

describe("runSeconds and durationText", () => {
  const run = { started_at: "2026-11-01T14:02:00", finished_at: "2026-11-01T14:04:31" }

  test("the time between start and finish", () => {
    expect(runSeconds(run)).toBe(151)
    expect(durationText(run, "done")).toBe("2:31")
    expect(durationText(run, "error")).toBe("2:31")
    expect(durationText(run, "cancelled")).toBe("2:31")
  })

  test("fractional seconds are rounded and a run over an hour shows hours", () => {
    expect(runSeconds({ started_at: "2026-11-01T14:02:00.400000", finished_at: "2026-11-01T14:02:05.100000" })).toBe(5)
    expect(durationText({ started_at: "2026-11-01T10:00:00", finished_at: "2026-11-01T11:02:05" }, "done")).toBe("1:02:05")
  })

  test("a run that hasn't finished says Running, or Interrupted when its process is gone", () => {
    const open = { started_at: "2026-11-01T14:02:00", finished_at: null }
    expect(runSeconds(open)).toBeNull()
    expect(durationText(open, "running")).toBe("Running")
    expect(durationText(open, "interrupted")).toBe("Interrupted")
  })

  test("a finished run with unreadable times has no duration", () => {
    expect(durationText({ started_at: "soon", finished_at: "later" }, "done")).toBe("")
  })
})

describe("startedText", () => {
  test("a day word or date, then the clock time", () => {
    expect(startedText("2020-01-05T14:02:31")).toBe("5 Jan 2020 14:02")
    expect(startedText("not a time")).toBe("")
  })
})
