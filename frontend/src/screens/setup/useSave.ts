import { useCallback, useState } from "react"

import { ApiError } from "@/api/client"
import { messageOf, type FieldErrors } from "./wizard"

export interface SaveError {
  message: string
  /** The server's per-field messages on a 422, shown next to the matching inputs. */
  fields: FieldErrors
}

/** A step's save: busy while it runs, the error if it fails. `run` resolves to whether it worked. */
export function useSave() {
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<SaveError | null>(null)
  const run = useCallback(async (work: () => Promise<void>): Promise<boolean> => {
    setSaving(true)
    setError(null)
    try {
      await work()
      return true
    } catch (err) {
      setError({ message: messageOf(err), fields: err instanceof ApiError ? err.fields : null })
      return false
    } finally {
      setSaving(false)
    }
  }, [])
  return { saving, error, setError, run }
}
