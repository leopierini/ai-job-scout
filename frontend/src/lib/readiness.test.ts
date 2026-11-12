import { describe, expect, test } from "vitest"

import type { Keys, Settings } from "@/api/types"
import { OFF_STARTUP_SOURCES } from "@/lib/test-settings"
import { headlineFor } from "@/screens/main/TopBar"
import { profileHasText, readiness } from "./readiness"
import { queriesFor, sourcesInUse, usesApify } from "./sources"

function settings(): Settings {
  return {
    linkedin: { enabled: true, actor: "a", keywords: [], regions: [], cap_per_keyword: 25, sort_by: "DD", scrape_company: true, price_per_result: 0.002 },
    indeed: { enabled: false, actor: "b", countries: [], queries: [], job_types: ["internship"], max_rows_per_query: 20, price_per_result: 0.005 },
    ...OFF_STARTUP_SOURCES,
    windows: { default_days: 15, overlap_minutes: 60 },
    filters: { excluded_countries: [], excluded_cities: [], excluded_title_words: [], languages: [] },
    scoring: { provider: "claude_code", model: "sonnet", min_score: 70, send_below_threshold: false, reasoning_language: "English", batch_size: 10, max_jobs_per_run: null, bands: "", dealbreakers: [], extra_instructions: "" },
    cleanup: { enabled: true, deadline_passed: true, start_too_early: true, too_old: true, excluded_place: true, earliest_start: null, max_age_days: 60, interested_exempt_from_age: true },
    outputs: { csv: { enabled: false, path: "", auto_export: true }, webhook: { enabled: false, url: "" }, notion: { enabled: false, parent_page_id: "", database_id: "" } },
    ui: { setup_complete: true, headline: "", columns: ["score", "role"], default_sort: "score" },
  }
}

function keys(set: Partial<Record<keyof Keys, boolean>> = {}): Keys {
  const k = (name: keyof Keys) => ({ set: Boolean(set[name]), hint: set[name] ? "…abcd" : "" })
  return {
    apify_token: k("apify_token"),
    anthropic_api_key: k("anthropic_api_key"),
    openai_api_key: k("openai_api_key"),
    gemini_api_key: k("gemini_api_key"),
    openrouter_api_key: k("openrouter_api_key"),
    notion_token: k("notion_token"),
  }
}

const REGION = { name: "Netherlands", geo_id: "102890719" }

function ready(): Settings {
  const s = settings()
  return { ...s, linkedin: { ...s.linkedin, keywords: ["Software Engineer"], regions: [REGION] } }
}

describe("readiness", () => {
  test("readiness lists what is missing, in order", () => {
    const missing = readiness(settings(), keys(), "", "real")
    expect(missing).toEqual([
      { id: "profile", what: "Write your profile", tab: "profile" },
      { id: "keyword", what: "Add a keyword to search for", tab: "search" },
      { id: "region", what: "Choose where to search", tab: "search" },
      { id: "apify_token", what: "Add your Apify token", tab: "keys" },
    ])
    expect(missing[0].what).toBe("Write your profile")
  })

  test("readiness is empty in sample mode", () => {
    expect(readiness(settings(), keys(), "", "sample")).toEqual([])
  })

  test("nothing is missing once everything is in place", () => {
    expect(readiness(ready(), keys({ apify_token: true }), "Rosa Achterberg, software engineer.", "real")).toEqual([])
  })

  test("Indeed searches count as keywords, and regions only matter with LinkedIn on", () => {
    const s = settings()
    const indeedOnly: Settings = {
      ...s,
      linkedin: { ...s.linkedin, enabled: false },
      indeed: { ...s.indeed, enabled: true, queries: ["software engineer"] },
    }
    expect(readiness(indeedOnly, keys({ apify_token: true }), "Some text", "real")).toEqual([])
  })

  test("free sources alone need no keyword, region or Apify token", () => {
    const s = settings()
    const careersOnly: Settings = {
      ...s,
      linkedin: { ...s.linkedin, enabled: false },
      careers: { ...s.careers, enabled: true, pages: ["https://jobs.ashbyhq.com/example-labs"] },
    }
    expect(readiness(careersOnly, keys({ apify_token: false }), "Some text", "real")).toEqual([])
  })

  test("LinkedIn left on with nothing to search doesn't ask for the Apify token", () => {
    const s = settings() // LinkedIn on, no keywords or regions
    const careers: Settings = { ...s, careers: { ...s.careers, enabled: true, pages: ["https://jobs.ashbyhq.com/example-labs"] } }
    expect(readiness(careers, keys({ apify_token: false }), "Some text", "real")).toEqual([])
  })

  test("LinkedIn keywords without a region still ask for one when another source runs", () => {
    const s = settings()
    const both: Settings = {
      ...s,
      linkedin: { ...s.linkedin, keywords: ["AI engineer"] },
      careers: { ...s.careers, enabled: true, pages: ["https://jobs.ashbyhq.com/example-labs"] },
    }
    expect(readiness(both, keys({ apify_token: true }), "Some text", "real").map((m) => m.id)).toEqual(["region"])
  })

  test("an Apify board needs the Apify token", () => {
    const s = settings()
    const wttj: Settings = {
      ...s,
      linkedin: { ...s.linkedin, enabled: false },
      wttj: { ...s.wttj, enabled: true, queries: ["AI engineer"] },
    }
    expect(readiness(wttj, keys({ apify_token: false }), "Some text", "real").map((m) => m.id)).toEqual(["apify_token"])
  })

  test("a provider that needs a key asks for it by name", () => {
    const s = ready()
    const gemini: Settings = { ...s, scoring: { ...s.scoring, provider: "gemini", model: "gemini-3.8-flash" } }
    expect(readiness(gemini, keys({ apify_token: true }), "Text", "real")).toEqual([
      { id: "ai_key", what: "Add your Google Gemini API key", tab: "keys" },
    ])
    expect(readiness(gemini, keys({ apify_token: true, gemini_api_key: true }), "Text", "real")).toEqual([])
  })
})

describe("the shared startup searches", () => {
  function startups(change: (s: Settings) => Settings): Settings {
    const s = settings()
    return change({ ...s, linkedin: { ...s.linkedin, enabled: false }, startup_queries: ["AI engineer"] })
  }

  test("a source that is on with no searches of its own uses the shared ones", () => {
    const s = startups((s) => ({ ...s, jobgether: { ...s.jobgether, enabled: true } }))
    expect(queriesFor(s, "jobgether")).toEqual(["AI engineer"])
    expect(sourcesInUse(s)).toEqual(["jobgether"])
    expect(usesApify(s)).toBe(false)
    expect(readiness(s, keys(), "Some text", "real")).toEqual([])
  })

  test("a source's own searches win over the shared ones", () => {
    const s = startups((s) => ({ ...s, yc: { ...s.yc, enabled: true, queries: ["founding engineer"] } }))
    expect(queriesFor(s, "yc")).toEqual(["founding engineer"])
    expect(headlineFor(s)).toBe("founding engineer")
  })

  test("an Apify board with only the shared searches needs the Apify token", () => {
    const s = startups((s) => ({ ...s, wttj: { ...s.wttj, enabled: true } }))
    expect(sourcesInUse(s)).toEqual(["wttj"])
    expect(usesApify(s)).toBe(true)
    expect(readiness(s, keys(), "Some text", "real").map((m) => m.id)).toEqual(["apify_token"])
    expect(headlineFor(s)).toBe("AI engineer")
  })

  test("a source that is off, or no shared searches, searches nothing", () => {
    const off = startups((s) => s)
    expect(sourcesInUse(off)).toEqual([])
    const none = startups((s) => ({ ...s, startup_queries: [], wttj: { ...s.wttj, enabled: true } }))
    expect(sourcesInUse(none)).toEqual([])
    expect(usesApify(none)).toBe(false)
  })
})

describe("profileHasText", () => {
  test("profileHasText ignores headings-only templates", () => {
    expect(profileHasText("## About me\n\n## What I want")).toBe(false)
    expect(profileHasText("")).toBe(false)
    expect(profileHasText("   \n\n")).toBe(false)
    expect(profileHasText("## About me\nI design payment apps.")).toBe(true)
    expect(profileHasText("#hashtag is not a heading")).toBe(true)
  })
})
