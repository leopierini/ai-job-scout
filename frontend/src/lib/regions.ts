import { useCallback, useEffect, useState } from "react"

import { api, ApiError } from "@/api/client"
import type { Regions } from "@/api/types"

let cached: Promise<Regions> | null = null

/** GET /api/regions once per page load; a failure isn't remembered, so the next call tries again. */
export function loadRegions(): Promise<Regions> {
  if (!cached) {
    cached = api.get<Regions>("/api/regions").catch((err: unknown) => {
      cached = null
      throw err
    })
  }
  return cached
}

/** LinkedIn's regions and Indeed's countries, bundled with the app. */
export function useRegions() {
  const [regions, setRegions] = useState<Regions | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(() => {
    setError(null)
    loadRegions().then(setRegions, (err: unknown) =>
      setError(err instanceof ApiError ? err.message : "The list of places couldn't be loaded."),
    )
  }, [])
  useEffect(load, [load])
  return { regions, error, retry: load }
}
