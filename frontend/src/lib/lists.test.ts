import { describe, expect, test } from "vitest"

import { addUnique, splitList } from "./lists"

describe("splitList", () => {
  test("splits on commas and newlines and trims each item", () => {
    expect(splitList("a, b\nc")).toEqual(["a", "b", "c"])
  })

  test("drops empty items, including Windows line ends", () => {
    expect(splitList(" ,a,,\r\nb ,\n")).toEqual(["a", "b"])
    expect(splitList("")).toEqual([])
  })

  test("text without a separator is one item", () => {
    expect(splitList("  Product Manager ")).toEqual(["Product Manager"])
  })
})

describe("addUnique", () => {
  test("appends new items in order", () => {
    expect(addUnique(["a"], ["b", "c"])).toEqual(["a", "b", "c"])
  })

  test("skips items already there, ignoring case, and repeats inside the new ones", () => {
    expect(addUnique(["Product Manager"], ["product manager", "APM", "apm"])).toEqual(["Product Manager", "APM"])
  })
})
