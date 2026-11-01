import { afterEach, describe, expect, test, vi } from "vitest"

import { ApiError, OFFLINE_MESSAGE, api } from "./client"

function stubFetch(response: Response | Error) {
  const fn = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
    if (response instanceof Error) throw response
    return response
  })
  vi.stubGlobal("fetch", fn)
  return fn
}

afterEach(() => vi.unstubAllGlobals())

describe("api", () => {
  test("every request carries the app header; a body is sent as JSON", async () => {
    const fetch = stubFetch(Response.json({ run_id: 7 }))
    const out = await api.send<{ run_id: number }>("POST", "/api/runs", { kind: "run" })

    expect(out).toEqual({ run_id: 7 })
    const [path, init] = fetch.mock.calls[0]
    expect(path).toBe("/api/runs")
    expect(init?.method).toBe("POST")
    expect(init?.headers).toMatchObject({ "X-Requested-By": "ai-job-scout", "Content-Type": "application/json" })
    expect(init?.body).toBe('{"kind":"run"}')
  })

  test("a GET carries the header too, and can return null", async () => {
    const fetch = stubFetch(Response.json(null))
    expect(await api.get("/api/runs/latest")).toBeNull()
    expect(fetch.mock.calls[0][1]?.headers).toMatchObject({ "X-Requested-By": "ai-job-scout" })
  })

  test("a non-2xx response throws ApiError with the server's message and fields", async () => {
    stubFetch(
      Response.json(
        { error: "Some values are not valid.", fields: { "scoring.min_score": "Too high" } },
        { status: 422 },
      ),
    )
    const err = await api.send("PUT", "/api/settings", {}).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({
      status: 422,
      message: "Some values are not valid.",
      fields: { "scoring.min_score": "Too high" },
    })
  })

  test("an error without a JSON body still gets a plain message", async () => {
    stubFetch(new Response("<html>bad gateway</html>", { status: 502 }))
    const err = await api.get("/api/state").catch((e: unknown) => e)
    expect(err).toMatchObject({ status: 502, fields: null })
    expect((err as ApiError).message).toMatch(/terminal window/)
  })

  test("no server at all is status 0 with what to do next", async () => {
    stubFetch(new TypeError("Failed to fetch"))
    const err = await api.get("/api/state").catch((e: unknown) => e)
    expect(err).toMatchObject({ status: 0, message: OFFLINE_MESSAGE })
  })
})
