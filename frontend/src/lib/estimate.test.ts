import { describe, expect, test } from "vitest"

import type { Estimate } from "@/api/types"
import { canFindJobs, type EstimateLoad } from "./estimate"

const estimate: Estimate = { max_usd: 1.2, searches: 4, max_results: 100, credit_usd: 5 }
const ready: EstimateLoad = { kind: "ready", estimate }

describe("Find jobs and the cost", () => {
  test("with real data, a run waits until its cost is on screen", () => {
    expect(canFindJobs(false, { kind: "loading" }, false)).toBe(false)
    expect(canFindJobs(false, { kind: "failed" }, false)).toBe(false)
    expect(canFindJobs(false, ready, false)).toBe(true)
  })

  test("sample data costs nothing, so it never waits for the cost", () => {
    expect(canFindJobs(true, { kind: "loading" }, false)).toBe(true)
    expect(canFindJobs(true, { kind: "failed" }, false)).toBe(true)
  })

  test("nothing starts while a run is going", () => {
    expect(canFindJobs(false, ready, true)).toBe(false)
    expect(canFindJobs(true, ready, true)).toBe(false)
  })
})
