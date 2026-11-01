import { describe, expect, test } from "vitest"

// The Python settings, as text: DEFAULT_BANDS must say the same thing on both sides.
import settingsPy from "../../../src/jobscout/settings.py?raw"
import { DEFAULT_BANDS, draftReducer, fieldError, initDraft, isEqual, mergePaths, numberOrNull, setIn } from "./form"

interface Shape {
  scoring: { min_score: number; dealbreakers: { text: string; consequence: string }[] }
  outputs: { csv: { path: string; enabled: boolean } }
  profile: string
}

const loaded: Shape = {
  scoring: { min_score: 70, dealbreakers: [{ text: "Needs a visa", consequence: "below 40" }] },
  outputs: { csv: { path: "/tmp/jobs.csv", enabled: false } },
  profile: "## About me",
}

describe("useDraft's state (draftReducer)", () => {
  test("a new draft is clean and holds the loaded value", () => {
    const d = initDraft(loaded)
    expect(d.value).toEqual(loaded)
    expect(d.dirty).toBe(false)
  })

  test("a nested set changes only that path and marks the draft dirty", () => {
    const d = draftReducer(initDraft(loaded), { type: "set", path: "scoring.min_score", value: 0 })
    expect(d.value.scoring.min_score).toBe(0)
    expect(d.value.outputs).toBe(loaded.outputs) // untouched branches are shared, not copied
    expect(loaded.scoring.min_score).toBe(70) // the loaded value is never mutated
    expect(d.dirty).toBe(true)
  })

  test("a set into an array item by index", () => {
    const d = draftReducer(initDraft(loaded), { type: "set", path: "scoring.dealbreakers.0.consequence", value: "0" })
    expect(d.value.scoring.dealbreakers).toEqual([{ text: "Needs a visa", consequence: "0" }])
    expect(Array.isArray(d.value.scoring.dealbreakers)).toBe(true)
    expect(d.dirty).toBe(true)
  })

  test("setting a value back to what was loaded makes the draft clean again", () => {
    let d = draftReducer(initDraft(loaded), { type: "set", path: "outputs.csv.enabled", value: true })
    expect(d.dirty).toBe(true)
    d = draftReducer(d, { type: "set", path: "outputs.csv.enabled", value: false })
    expect(d.dirty).toBe(false)
  })

  test("reset clears every change", () => {
    let d = draftReducer(initDraft(loaded), { type: "set", path: "scoring.min_score", value: 5 })
    d = draftReducer(d, { type: "set", path: "profile", value: "changed" })
    d = draftReducer(d, { type: "reset" })
    expect(d.value).toEqual(loaded)
    expect(d.dirty).toBe(false)
  })

  test("reset to a newly saved value makes that the clean state", () => {
    const saved = setIn(loaded, "scoring.min_score", 80)
    let d = draftReducer(initDraft(loaded), { type: "set", path: "scoring.min_score", value: 80 })
    d = draftReducer(d, { type: "reset", next: saved })
    expect(d.value).toEqual(saved)
    expect(d.dirty).toBe(false)
  })

  test("rebase takes a new saved value and keeps the edits made on top of the old one", () => {
    let d = draftReducer(initDraft(loaded), { type: "set", path: "profile", value: "edited" })
    const server = setIn(loaded, "outputs.csv.path", "/srv/new.csv")
    d = draftReducer(d, { type: "rebase", base: server, paths: ["outputs.csv"] })
    expect(d.value.outputs.csv.path).toBe("/srv/new.csv")
    expect(d.value.profile).toBe("edited")
    expect(d.dirty).toBe(true)
    d = draftReducer(d, { type: "set", path: "profile", value: "## About me" })
    expect(d.dirty).toBe(false) // the new saved value is the clean state
  })
})

describe("fieldError", () => {
  const fields = {
    "scoring.min_score": "Input should be less than or equal to 100",
    "scoring.dealbreakers.1.text": "Field required",
  }

  test("finds the server's message for a path", () => {
    expect(fieldError(fields, "scoring.min_score")).toBe("Input should be less than or equal to 100")
  })

  test("finds a message under a path, for an input that edits a whole list", () => {
    expect(fieldError(fields, "scoring.dealbreakers")).toBe("Field required")
  })

  test("doesn't match a path that only starts with the same letters", () => {
    expect(fieldError({ "scoring.min_score_x": "no" }, "scoring.min_score")).toBeUndefined()
  })

  test("nothing when there are no field errors", () => {
    expect(fieldError(null, "scoring.min_score")).toBeUndefined()
    expect(fieldError(fields, "scoring.batch_size")).toBeUndefined()
  })
})

describe("mergePaths", () => {
  test("copies only the given paths from the draft onto the fresh settings", () => {
    const fresh = { a: { x: 1, y: 2 }, b: { z: 3 }, c: 9 }
    const draft = { a: { x: 10, y: 20 }, b: { z: 30 }, c: 90 }
    expect(mergePaths(fresh, draft, ["a.x", "b"])).toEqual({ a: { x: 10, y: 2 }, b: { z: 30 }, c: 9 })
    expect(fresh).toEqual({ a: { x: 1, y: 2 }, b: { z: 3 }, c: 9 })
  })
})

describe("isEqual", () => {
  test("compares nested objects and arrays by value", () => {
    expect(isEqual({ a: [1, { b: "c" }], d: null }, { a: [1, { b: "c" }], d: null })).toBe(true)
    expect(isEqual({ a: [1, 2] }, { a: [2, 1] })).toBe(false)
    expect(isEqual({ a: 1 }, { a: 1, b: undefined })).toBe(false)
    expect(isEqual([], {})).toBe(false)
  })
})

describe("numberOrNull", () => {
  test("an empty box is null; anything else is the number typed", () => {
    expect(numberOrNull("")).toBeNull()
    expect(numberOrNull("  ")).toBeNull()
    expect(numberOrNull("25")).toBe(25)
    expect(numberOrNull("0.002")).toBe(0.002)
  })
})

describe("DEFAULT_BANDS", () => {
  test("matches the default in settings.py, so Reset to default restores the same text", () => {
    const block = /DEFAULT_BANDS = "\\n"\.join\(\s*\[([\s\S]*?)\]\s*\)/.exec(settingsPy)
    expect(block).not.toBeNull()
    const lines = [...block![1].matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((m) => m[1])
    expect(lines.length).toBeGreaterThan(0)
    expect(DEFAULT_BANDS).toBe(lines.join("\n"))
  })
})
