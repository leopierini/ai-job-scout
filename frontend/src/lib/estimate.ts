import { useCallback, useEffect, useState } from "react"

import { api, ApiError } from "@/api/client"
import type { Estimate, Settings } from "@/api/types"

const ESTIMATE_DELAY_MS = 400

export type LiveEstimate =
  | { kind: "loading" }
  | { kind: "ready"; estimate: Estimate }
  /** `invalid`: the server refused the values on screen (a 422), so there's nothing to estimate yet. */
  | { kind: "failed"; invalid: boolean }

/** The cost of a run with `settings` (saved or not), worked out again shortly after each change. */
export function useLiveEstimate(settings: Settings): LiveEstimate {
  const [state, setState] = useState<LiveEstimate>({ kind: "loading" })
  const body = JSON.stringify({ settings })
  useEffect(() => {
    let live = true
    const timer = setTimeout(() => {
      api.send<Estimate>("POST", "/api/estimate", JSON.parse(body)).then(
        (estimate) => live && setState({ kind: "ready", estimate }),
        (err: unknown) => live && setState({ kind: "failed", invalid: err instanceof ApiError && err.status === 422 }),
      )
    }, ESTIMATE_DELAY_MS)
    return () => {
      live = false
      clearTimeout(timer)
    }
  }, [body])
  return state
}

/** The cost of the next run with the saved settings. */
export type EstimateLoad = { kind: "loading" } | { kind: "ready"; estimate: Estimate } | { kind: "failed" }

/** Find jobs needs the cost on screen first with real data (it can spend money); sample data costs nothing. */
export function canFindJobs(sample: boolean, load: EstimateLoad, busy: boolean): boolean {
  return !busy && (sample || load.kind === "ready")
}

/** The cost of the next run with the saved settings, and `reload` to work it out again (after a run, or to retry
 * a failure). A known cost stays on screen while it is worked out again. */
export function useSavedEstimate() {
  const [load, setLoad] = useState<EstimateLoad>({ kind: "loading" })
  const reload = useCallback(async () => {
    setLoad((l) => (l.kind === "ready" ? l : { kind: "loading" }))
    try {
      setLoad({ kind: "ready", estimate: await api.send<Estimate>("POST", "/api/estimate", { settings: null }) })
    } catch {
      setLoad({ kind: "failed" })
    }
  }, [])
  useEffect(() => {
    void reload()
  }, [reload])
  return { load, reload }
}
