import { describe, expect, test } from "vitest"

import { STATUS_LABELS, STEP_STATUS_LABELS, STEP_TITLES, elapsed, elapsedLabel, money, relativeDay } from "./format"

describe("money", () => {
  test("two decimals with a dollar sign", () => {
    expect(money(1.8)).toBe("$1.80")
    expect(money(0)).toBe("$0.00")
    expect(money(1234.5)).toBe("$1,234.50")
  })
})

describe("elapsed", () => {
  test("minutes and zero-padded seconds", () => {
    expect(elapsed(151)).toBe("2:31")
    expect(elapsed(4)).toBe("0:04")
    expect(elapsed(59.9)).toBe("0:59")
  })
  test("hours when a run is long", () => {
    expect(elapsed(3725)).toBe("1:02:05")
  })
})

describe("relativeDay", () => {
  const today = "2026-11-01"
  test("recent days in words", () => {
    expect(relativeDay("2026-11-01", today)).toBe("today")
    expect(relativeDay("2026-10-31", today)).toBe("yesterday")
    expect(relativeDay("2026-10-30", today)).toBe("2 days ago")
    expect(relativeDay("2026-10-26", today)).toBe("6 days ago")
  })
  test("a week or more ago, and future dates, as day and month", () => {
    expect(relativeDay("2026-10-12", today)).toBe("12 Oct")
    expect(relativeDay("2026-11-20", today)).toBe("20 Nov")
  })
  test("a full timestamp counts by its calendar day", () => {
    expect(relativeDay("2026-10-31T23:50:00", "2026-11-01T00:10:00")).toBe("yesterday")
  })
  test("another year shows the year", () => {
    expect(relativeDay("2025-10-12", today)).toBe("12 Oct 2025")
  })
  test("empty or unreadable dates give an empty string", () => {
    expect(relativeDay("", today)).toBe("")
    expect(relativeDay("soon", today)).toBe("")
  })
})

describe("labels", () => {
  test("step titles are the ones the UI shows", () => {
    expect(STEP_TITLES.search).toBe("Search for jobs")
    expect(Object.values(STEP_TITLES)).toEqual([
      "Clean up old jobs",
      "Search for jobs",
      "Remove duplicates",
      "Score against your profile",
      "Save results",
    ])
  })
  test("status and step status labels", () => {
    expect(STATUS_LABELS.to_review).toBe("To review")
    expect(STATUS_LABELS.not_interested).toBe("Not interested")
    expect(Object.values(STEP_STATUS_LABELS)).toEqual(["Pending", "Running", "Done", "Error", "Skipped"])
  })
})

describe("elapsedLabel", () => {
  test("elapsedLabel hides sub-second steps", () => {
    expect(elapsedLabel(0.4)).toBe("")
    expect(elapsedLabel(0)).toBe("")
    expect(elapsedLabel(1)).toBe("0:01")
    expect(elapsedLabel(151)).toBe("2:31")
  })
})
