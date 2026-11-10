import { describe, expect, test } from "vitest"

import type { AppState, Keys, Settings } from "@/api/types"
import {
  PROFILE_TEMPLATE,
  aiReady,
  aiSignature,
  applyAi,
  applyOutputs,
  applySearch,
  applyYou,
  canLeaveSearch,
  canLeaveYou,
  draftAnswers,
  fieldMessage,
  notionConnected,
  notionSignature,
  notReadyQuestion,
  notReadyOutputs,
  progressLine,
  seedWizard,
  stepFrom,
  wizardHref,
} from "./wizard"
import { readiness } from "@/lib/readiness"

function settings(): Settings {
  return {
    linkedin: { enabled: true, actor: "a", keywords: [], regions: [], cap_per_keyword: 25, sort_by: "DD", scrape_company: true, price_per_result: 0.002 },
    indeed: { enabled: false, actor: "b", countries: [], queries: [], job_types: ["internship", "fulltime"], max_rows_per_query: 20, price_per_result: 0.005 },
    windows: { default_days: 15, overlap_minutes: 60 },
    filters: { excluded_countries: [], excluded_cities: [], excluded_title_words: [], languages: [] },
    scoring: { provider: "anthropic", model: "claude-sonnet-5-5", min_score: 70, send_below_threshold: false, reasoning_language: "English", batch_size: 10, max_jobs_per_run: null, bands: "", dealbreakers: [], extra_instructions: "" },
    cleanup: { enabled: true, deadline_passed: true, start_too_early: true, too_old: true, excluded_place: true, earliest_start: null, max_age_days: 60, interested_exempt_from_age: true },
    outputs: { csv: { enabled: false, path: "", auto_export: true }, webhook: { enabled: false, url: "" }, notion: { enabled: false, parent_page_id: "", database_id: "" } },
    ui: { setup_complete: false, headline: "", columns: ["score", "role"], default_sort: "score" },
  }
}

const noKeys: Keys = {
  apify_token: { set: false, hint: "" },
  anthropic_api_key: { set: false, hint: "" },
  openai_api_key: { set: false, hint: "" },
  gemini_api_key: { set: false, hint: "" },
  openrouter_api_key: { set: false, hint: "" },
  notion_token: { set: false, hint: "" },
}

function appState(claude = false): AppState {
  return {
    mode: "real",
    setup_complete: false,
    headline: "",
    claude_code_found: claude,
    paths: { app_dir: "/home/ana/.jobscout", database: "", settings: "", profile: "" },
  }
}

describe("steps", () => {
  test("the step comes from ?step=N and falls back to 1", () => {
    expect(stepFrom("3")).toBe(3)
    expect(stepFrom(null)).toBe(1)
    expect(stepFrom("0")).toBe(1)
    expect(stepFrom("9")).toBe(1)
    expect(stepFrom("two")).toBe(1)
  })

  test("the progress line names the step", () => {
    expect(progressLine(2)).toBe("Step 2 of 5 · You")
    expect(progressLine(4)).toBe("Step 4 of 5 · Results go to")
  })
})

describe("seedWizard", () => {
  test("Claude Code is preselected when it was found and nothing else was chosen", () => {
    const w = seedWizard(settings(), "", noKeys, appState(true))
    expect(w.ai.provider).toBe("claude_code")
    expect(w.ai.models.claude_code).toBe("sonnet")
  })

  test("a saved choice wins over Claude Code", () => {
    const s = settings()
    s.scoring.provider = "openai"
    s.scoring.model = "my-model"
    const w = seedWizard(s, "", noKeys, appState(true))
    expect(w.ai.provider).toBe("openai")
    expect(w.ai.models.openai).toBe("my-model")
  })

  test("saved places, rules and profile come back, and the review is open when there's a profile", () => {
    const s = settings()
    s.filters = { ...s.filters, excluded_countries: ["Switzerland"], excluded_cities: ["Paris"] }
    s.scoring.dealbreakers = [{ text: "Needs a visa", consequence: "below 40" }]
    s.linkedin.keywords = ["Product Manager"]
    s.cleanup.earliest_start = "2027-01-01"
    s.outputs.csv.path = ""
    const w = seedWizard(s, "## About me", noKeys, appState())
    expect(w.you.avoid).toEqual(["Switzerland", "Paris"])
    expect(w.you.dealbreakers).toEqual([{ text: "Needs a visa", consequence: "below 40" }])
    expect(w.you.reviewOpen).toBe(true)
    expect(w.you.earliestStart).toBe("2027-01-01")
    expect(w.search.keywords).toEqual(["Product Manager"])
    expect(w.outputs.csv.path).toBe("/home/ana/.jobscout/jobs.csv")
  })

  test("Notion counts as connected only with a database and a saved token", () => {
    const s = settings()
    s.outputs.notion = { enabled: true, parent_page_id: "p", database_id: "d" }
    expect(notionConnected(seedWizard(s, "", noKeys, appState()).outputs.notion)).toBe(false)
    const keys = { ...noKeys, notion_token: { set: true, hint: "…abcd" } }
    const seeded = seedWizard(s, "", keys, appState()).outputs.notion
    expect(notionConnected(seeded)).toBe(true)
    expect(seeded.hasDatabase).toBe(true)
  })

  test("a Notion connection counts only for the token and page it was made with", () => {
    const w = seedWizard(settings(), "", noKeys, appState())
    const notion = { ...w.outputs.notion, enabled: true, parent: "https://www.notion.so/Jobs-1", connectedSig: notionSignature("", "https://www.notion.so/Jobs-1") }
    expect(notionConnected(notion)).toBe(true)
    expect(notionConnected({ ...notion, parent: "https://www.notion.so/Other-2" })).toBe(false)
    expect(notionConnected({ ...notion, token: "ntn-new" })).toBe(false)
    expect(notReadyOutputs({ ...w.outputs, notion: { ...notion, parent: "https://www.notion.so/Other-2" } })).toEqual(["notion"])
  })
})

describe("the AI step", () => {
  test("Next needs a passing test of exactly what is on screen", () => {
    const w = seedWizard(settings(), "", noKeys, appState())
    expect(aiReady(w.ai)).toBe(false)
    const tested = { ...w.ai, test: { sig: aiSignature(w.ai), ok: true, message: "Connected." } }
    expect(aiReady(tested)).toBe(true)
    const changed = { ...tested, keys: { anthropic_api_key: "sk-ant-new" } }
    expect(aiReady(changed)).toBe(false)
    const failed = { ...w.ai, test: { sig: aiSignature(w.ai), ok: false, message: "No." } }
    expect(aiReady(failed)).toBe(false)
  })

  test("saves the provider and the trimmed model", () => {
    const w = seedWizard(settings(), "", noKeys, appState())
    const ai = { ...w.ai, provider: "ollama" as const, models: { ...w.ai.models, ollama: " llama3.1 " } }
    const s = applyAi(settings(), ai)
    expect(s.scoring.provider).toBe("ollama")
    expect(s.scoring.model).toBe("llama3.1")
  })
})

describe("the You step", () => {
  test("splits places, keeps filled rules and sets the start date", () => {
    const w = seedWizard(settings(), "", noKeys, appState())
    const you = {
      ...w.you,
      regions: [{ name: "United Kingdom", geo_id: "101165590" }],
      avoid: ["switzerland", "Paris"],
      earliestStart: "2027-02-01",
      headline: "  Product Manager, early career ",
      dealbreakers: [
        { text: "Needs a UK visa", consequence: "below 40" },
        { text: "  ", consequence: "max 25" },
      ],
    }
    const s = applyYou(settings(), you, ["Switzerland", "United Kingdom"])
    expect(s.filters).toEqual({
      excluded_countries: ["Switzerland"],
      excluded_cities: ["Paris"],
      excluded_title_words: [],
      languages: [],
    })
    expect(s.linkedin.regions).toEqual([{ name: "United Kingdom", geo_id: "101165590" }])
    expect(s.cleanup.earliest_start).toBe("2027-02-01")
    expect(s.ui.headline).toBe("Product Manager, early career")
    expect(s.scoring.dealbreakers).toEqual([{ text: "Needs a UK visa", consequence: "below 40" }])
  })

  test("no start date saves null", () => {
    const w = seedWizard(settings(), "", noKeys, appState())
    expect(applyYou(settings(), { ...w.you, earliestStart: "" }, []).cleanup.earliest_start).toBeNull()
  })

  test("the draft answers carry region names and a null start date", () => {
    const w = seedWizard(settings(), "", noKeys, appState())
    const answers = draftAnswers({ ...w.you, roles: ["APM"], regions: [{ name: "Switzerland", geo_id: "1" }] })
    expect(answers).toMatchObject({ target_roles: ["APM"], regions: ["Switzerland"], earliest_start: null })
  })

  test("step you requires role and profile text", () => {
    const w = seedWizard(settings(), "", noKeys, appState())
    expect(canLeaveYou(w.you)).toBe("Add at least one target role")
    const withRole = { ...w.you, roles: ["Product Designer"] }
    expect(canLeaveYou(withRole)).toBe("Draft your profile, or choose Write it myself")
    const template = { ...withRole, reviewOpen: true, profile: PROFILE_TEMPLATE }
    expect(canLeaveYou(template)).toBe("Write a few lines about yourself in your profile")
    expect(canLeaveYou({ ...template, profile: PROFILE_TEMPLATE + "I design payment apps.\n" })).toBeNull()
  })

  test("a new wizard hasn't drafted yet", () => {
    expect(seedWizard(settings(), "", noKeys, appState()).you.drafted).toBe(false)
  })

  test("the write-it-myself template has the four headings", () => {
    for (const h of ["About me", "What I want", "Strengths", "Constraints"]) expect(PROFILE_TEMPLATE).toContain(`## ${h}`)
  })
})

describe("the Search step", () => {
  test("saves sources, keywords and Indeed's choices without touching the rest", () => {
    const w = seedWizard(settings(), "", noKeys, appState())
    const search = {
      ...w.search,
      keywords: ["Product Manager"],
      linkedin: true,
      indeed: true,
      indeedCountries: ["de"],
      indeedQueries: ["product manager"],
      indeedJobTypes: ["fulltime" as const],
    }
    const s = applySearch(settings(), search)
    expect(s.linkedin.keywords).toEqual(["Product Manager"])
    expect(s.indeed).toMatchObject({ enabled: true, countries: ["de"], queries: ["product manager"], job_types: ["fulltime"] })
    expect(s.linkedin.cap_per_keyword).toBe(25)
  })
})

describe("the Search step's gate", () => {
  const REGION = { name: "Netherlands", geo_id: "102890719" }
  const savedToken = { ...noKeys, apify_token: { set: true, hint: "…abcd" } }

  test("step search requires keyword and region", () => {
    const w = seedWizard(settings(), "", savedToken, appState())
    expect(canLeaveSearch({ search: w.search, regions: [REGION], tokenSaved: true })).toBe("Add a keyword to search for")
    const withKeyword = { ...w.search, keywords: ["Product Designer"] }
    expect(canLeaveSearch({ search: withKeyword, regions: [], tokenSaved: true })).toBe("Choose where to search")
    expect(canLeaveSearch({ search: { ...withKeyword, linkedin: false, indeed: true }, regions: [], tokenSaved: true })).toBeNull()
    expect(canLeaveSearch({ search: withKeyword, regions: [REGION], tokenSaved: true })).toBeNull()
  })

  test("the token counts once saved, or once a test of the typed one passed", () => {
    const w = seedWizard(settings(), "", noKeys, appState())
    const search = { ...w.search, keywords: ["Product Designer"] }
    const gate = (s: typeof search, tokenSaved = false) => canLeaveSearch({ search: s, regions: [REGION], tokenSaved })
    expect(gate(search)).toBe("Add your Apify token to continue")
    const typed = { ...search, apifyToken: " apify_api_FAKE " }
    expect(gate(typed)).toBe("Test your Apify token to continue")
    const passed = { ...typed, apifyTest: { sig: "apify_api_FAKE", ok: true, message: "Connected.", credit: 5 } }
    expect(gate(passed)).toBeNull()
    expect(gate({ ...passed, apifyToken: "apify_api_OTHER" })).toBe("Test your Apify token to continue")
  })

  test("a failed test blocks Next until it's tested again or cleared", () => {
    const w = seedWizard(settings(), "", savedToken, appState())
    const failed = {
      ...w.search,
      keywords: ["Product Designer"],
      apifyToken: "apify_api_BAD",
      apifyTest: { sig: "apify_api_BAD", ok: false, message: "Apify refused the token.", credit: null },
    }
    const gate = (s: typeof failed) => canLeaveSearch({ search: s, regions: [REGION], tokenSaved: true })
    expect(gate(failed)).toBe("Add your Apify token to continue")
    expect(gate({ ...failed, apifyToken: "" })).toBeNull()
    const savedFailed = { ...failed, apifyToken: "", apifyTest: { ...failed.apifyTest, sig: "" } }
    expect(gate(savedFailed)).toBe("Add your Apify token to continue")
  })
})

describe("the Done step's readiness links", () => {
  test("each missing item links to the wizard step that fixes it", () => {
    const missing = readiness(settings(), noKeys, "", "real")
    expect(missing.map((m) => [m.id, wizardHref(m)])).toEqual([
      ["profile", "#/setup?step=2"],
      ["keyword", "#/setup?step=3"],
      ["region", "#/setup?step=2"],
      ["apify_token", "#/setup?step=3"],
      ["ai_key", "#/setup?step=1"],
    ])
  })
})

describe("the Results step", () => {
  test("an empty CSV path saves the default, and Notion keeps what the server set", () => {
    const base = settings()
    base.outputs.notion = { enabled: true, parent_page_id: "p", database_id: "d" }
    const w = seedWizard(settings(), "", noKeys, appState())
    const outputs = { ...w.outputs, csv: { ...w.outputs.csv, enabled: true, path: " " } }
    const s = applyOutputs(base, outputs, "/x/jobs.csv")
    expect(s.outputs.csv).toEqual({ enabled: true, path: "/x/jobs.csv", auto_export: true })
    expect(s.outputs.notion).toEqual({ enabled: false, parent_page_id: "p", database_id: "d" })
  })

  test("lists the outputs that are on but not tested, and asks about them", () => {
    const w = seedWizard(settings(), "", noKeys, appState())
    const outputs = {
      ...w.outputs,
      notion: { ...w.outputs.notion, enabled: true },
      webhook: { ...w.outputs.webhook, enabled: true, url: "https://hooks.example.com/x" },
    }
    expect(notReadyOutputs(outputs)).toEqual(["notion", "webhook"])
    expect(notReadyQuestion(["notion"])).toBe("Notion isn't connected yet. Continue without it?")
    expect(notReadyQuestion(["notion", "webhook"])).toBe("Notion and the webhook aren't connected yet. Continue without them?")
  })

  test("a passing test counts only for the URL or path it tested", () => {
    const w = seedWizard(settings(), "", noKeys, appState())
    const url = "https://hooks.example.com/x"
    const ok = { ...w.outputs, webhook: { enabled: true, url, test: { sig: url, ok: true, message: "Sent." } } }
    expect(notReadyOutputs(ok)).toEqual([])
    expect(notReadyOutputs({ ...ok, webhook: { ...ok.webhook, url: url + "y" } })).toEqual(["webhook"])
  })
})

describe("fieldMessage", () => {
  test("finds the message for a field or anything under it", () => {
    const fields = { "ui.headline": "Too long", "scoring.dealbreakers.0.text": "Required" }
    expect(fieldMessage(fields, "ui.headline")).toBe("Too long")
    expect(fieldMessage(fields, "scoring.dealbreakers")).toBe("Required")
    expect(fieldMessage(fields, "ui")).toBe("Too long")
    expect(fieldMessage(fields, "cleanup.earliest_start")).toBeUndefined()
    expect(fieldMessage(null, "ui")).toBeUndefined()
  })

  test("does not match a field that only shares a prefix", () => {
    expect(fieldMessage({ "ui.headline_x": "No" }, "ui.headline")).toBeUndefined()
  })
})
