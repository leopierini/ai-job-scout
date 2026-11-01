import { describe, expect, test } from "vitest"

import { parseHash } from "./route"

describe("parseHash", () => {
  test("an empty hash is the main screen", () => {
    expect(parseHash("")).toMatchObject({ path: "/", params: [] })
    expect(parseHash("#/")).toMatchObject({ path: "/", params: [] })
  })

  test("segments become params", () => {
    expect(parseHash("#/settings/profile")).toMatchObject({ path: "/settings/profile", params: ["settings", "profile"] })
    expect(parseHash("#/history/12/")).toMatchObject({ path: "/history/12", params: ["history", "12"] })
  })

  test("the part after ? is the query", () => {
    const r = parseHash("#/setup?step=3")
    expect(r.path).toBe("/setup")
    expect(r.params).toEqual(["setup"])
    expect(r.query.get("step")).toBe("3")
  })
})
