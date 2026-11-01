import { describe, expect, test } from "vitest"

import { keyStatus, rescoreLine } from "./labels"

describe("keyStatus", () => {
  test("a saved key shows only its hint, never its value", () => {
    expect(keyStatus({ set: true, hint: "…wxyz" })).toBe("Set (…wxyz)")
    expect(keyStatus({ set: true, hint: "" })).toBe("Set")
    expect(keyStatus({ set: false, hint: "" })).toBe("Not set")
  })
})

describe("rescoreLine", () => {
  test("says how many jobs, about how many tokens, and that Apify isn't used", () => {
    expect(rescoreLine({ count: 12, approx_tokens: 48000 })).toBe(
      "12 jobs will be scored again (about 48,000 tokens). Apify isn't used.",
    )
    expect(rescoreLine({ count: 1, approx_tokens: 900 })).toBe("1 job will be scored again (about 900 tokens). Apify isn't used.")
  })
})
