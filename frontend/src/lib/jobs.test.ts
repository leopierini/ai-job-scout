import { describe, expect, test } from "vitest"

import type { JobOut } from "@/api/types"
import { filterJobs, fullDay, monthYear, sortJobs } from "./jobs"

function job(pk: string, extra: Partial<JobOut> = {}): JobOut {
  return {
    pk,
    source: "linkedin",
    job_id: pk,
    title: "Associate Product Manager",
    company: "Fjord Labs",
    location: "Oslo, Norway",
    url: "https://example.com/jobs/" + pk,
    posted: "",
    keyword: "Associate product manager",
    area: "Europe",
    recruiter: "",
    recruiter_title: "",
    recruiter_url: "",
    employment_type: "",
    salary: "",
    state: "kept",
    status: "to_review",
    score: null,
    reason: "",
    flags: [],
    start_date: "",
    deadline: "",
    archived_reason: null,
    first_seen_at: "2026-11-01T10:00:00",
    status_updated_at: "2026-11-01T10:00:00",
    restored: false,
    ...extra,
  }
}

const pks = (jobs: JobOut[]) => jobs.map((j) => j.pk)

describe("sortJobs", () => {
  test("sortJobs puts empty dates last in both directions", () => {
    const jobs = [
      job("a", { posted: "2026-10-20" }),
      job("b", { posted: "" }),
      job("c", { posted: "2026-10-30" }),
      job("d", { posted: "2026-10-25" }),
    ]
    expect(pks(sortJobs(jobs, "posted", "desc"))).toEqual(["c", "d", "a", "b"])
    expect(pks(sortJobs(jobs, "posted", "asc"))).toEqual(["a", "d", "c", "b"])

    const starts = [job("x", { start_date: "" }), job("y", { start_date: "2027-09-01" }), job("z", { start_date: "2027-01-01" })]
    expect(pks(sortJobs(starts, "starts", "asc"))).toEqual(["z", "y", "x"])
    expect(pks(sortJobs(starts, "starts", "desc"))).toEqual(["y", "z", "x"])
  })

  test("unscored jobs come last whatever the direction", () => {
    const jobs = [job("a", { score: 72 }), job("b", { score: null }), job("c", { score: 91 })]
    expect(pks(sortJobs(jobs, "score", "desc"))).toEqual(["c", "a", "b"])
    expect(pks(sortJobs(jobs, "score", "asc"))).toEqual(["a", "c", "b"])
  })

  test("does not change the list it was given", () => {
    const jobs = [job("a", { score: 1 }), job("b", { score: 2 })]
    sortJobs(jobs, "score", "desc")
    expect(pks(jobs)).toEqual(["a", "b"])
  })
})

describe("filterJobs", () => {
  const jobs = [
    job("a", { title: "Associate Product Manager", company: "Fjord Labs", location: "Oslo, Norway", score: 91 }),
    job("b", { title: "Founder's Associate", company: "Lumen Pay", location: "Berlin, Germany", source: "indeed", score: 72 }),
    job("c", { title: "Product Analyst", company: "Kestrel Payments", location: "London, UK", score: null }),
  ]

  test("filterJobs matches company case-insensitively", () => {
    expect(pks(filterJobs(jobs, { text: "lumen PAY", source: "all", minScore: null }))).toEqual(["b"])
  })

  test("text also matches the title and the place", () => {
    expect(pks(filterJobs(jobs, { text: "analyst", source: "all", minScore: null }))).toEqual(["c"])
    expect(pks(filterJobs(jobs, { text: "berlin", source: "all", minScore: null }))).toEqual(["b"])
    expect(pks(filterJobs(jobs, { text: "  ", source: "all", minScore: null }))).toEqual(["a", "b", "c"])
  })

  test("source and minimum score narrow the list; unscored jobs fail a minimum", () => {
    expect(pks(filterJobs(jobs, { text: "", source: "indeed", minScore: null }))).toEqual(["b"])
    expect(pks(filterJobs(jobs, { text: "", source: "all", minScore: 80 }))).toEqual(["a"])
  })
})

describe("dates in the table", () => {
  test("month and year for a start date, day month year for a deadline, empty when missing", () => {
    expect(monthYear("2027-09-01")).toBe("Sep 2027")
    expect(fullDay("2026-11-04")).toBe("4 Nov 2026")
    expect(monthYear("")).toBe("")
    expect(fullDay("soon")).toBe("")
  })
})
