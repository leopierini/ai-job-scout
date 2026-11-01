import { describe, expect, test } from "vitest"

import { indeedCountryName, splitPlaces } from "./places"

describe("splitPlaces", () => {
  const regions = ["United Kingdom", "Switzerland", "European Economic Area"]

  test("a LinkedIn region name is a country, anything else a city", () => {
    expect(splitPlaces(["Switzerland", "Paris", "Milan"], regions)).toEqual({
      countries: ["Switzerland"],
      cities: ["Paris", "Milan"],
    })
  })

  test("matching ignores case and spaces, and keeps the region's own spelling", () => {
    expect(splitPlaces(["  united kingdom "], regions)).toEqual({ countries: ["United Kingdom"], cities: [] })
  })

  test("empty names are dropped", () => {
    expect(splitPlaces(["", "  "], regions)).toEqual({ countries: [], cities: [] })
  })

  test("with no region list, everything is a city", () => {
    expect(splitPlaces(["Switzerland"], [])).toEqual({ countries: [], cities: ["Switzerland"] })
  })
})

describe("indeedCountryName", () => {
  test("shows a country code's English name", () => {
    expect(indeedCountryName("de")).toBe("Germany")
  })

  test("Indeed's uk is the United Kingdom", () => {
    expect(indeedCountryName("uk")).toBe("United Kingdom")
  })

  test("an unknown code is shown upper-cased", () => {
    expect(indeedCountryName("xq")).toBe("XQ")
    expect(indeedCountryName("1")).toBe("1")
  })
})
