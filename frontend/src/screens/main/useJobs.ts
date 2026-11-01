import { useCallback, useEffect, useRef, useState } from "react"
import { toast } from "sonner"

import { api, ApiError } from "@/api/client"
import type { JobOut, Status, View, ViewCounts } from "@/api/types"
import { STATUS_LABELS } from "@/lib/format"

export type JobsLoad = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; jobs: JobOut[] }

function messageOf(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err)
}

function jobPath(pk: string): string {
  return `/api/jobs/${encodeURIComponent(pk)}`
}

/** The jobs of one view plus the count of every view, with optimistic status changes. */
export function useJobs() {
  const [view, setViewState] = useState<View>("to_review")
  const [counts, setCounts] = useState<ViewCounts | null>(null)
  const [countsError, setCountsError] = useState<string | null>(null)
  const [load, setLoad] = useState<JobsLoad>({ kind: "loading" })
  const viewRef = useRef<View>(view)
  const request = useRef(0)

  const loadCounts = useCallback(async () => {
    try {
      setCounts(await api.get<ViewCounts>("/api/jobs/counts"))
      setCountsError(null)
    } catch (err) {
      setCountsError(messageOf(err))
    }
  }, [])

  const loadView = useCallback(async (v: View, quiet = false) => {
    const id = ++request.current
    if (!quiet) setLoad({ kind: "loading" })
    try {
      const jobs = await api.get<JobOut[]>(`/api/jobs?view=${v}`)
      if (id === request.current) setLoad({ kind: "ready", jobs })
    } catch (err) {
      if (id === request.current) setLoad({ kind: "error", message: messageOf(err) })
    }
  }, [])

  useEffect(() => {
    void loadCounts()
  }, [loadCounts])

  useEffect(() => {
    viewRef.current = view
    void loadView(view)
  }, [view, loadView])

  /** After a run: new counts and the current list, without blanking the table. */
  const refresh = useCallback(async () => {
    await Promise.all([loadCounts(), loadView(viewRef.current, true)])
  }, [loadCounts, loadView])

  const setView = useCallback((v: View) => setViewState(v), [])

  const removeRow = (pk: string) =>
    setLoad((l) => (l.kind === "ready" ? { kind: "ready", jobs: l.jobs.filter((j) => j.pk !== pk) } : l))
  const addRow = (job: JobOut, inView: View) => {
    if (viewRef.current !== inView) return
    setLoad((l) =>
      l.kind === "ready" && !l.jobs.some((j) => j.pk === job.pk) ? { kind: "ready", jobs: [...l.jobs, job] } : l,
    )
  }
  const shift = (from: View, to: View) =>
    setCounts((c) => (c ? { ...c, [from]: Math.max(0, c[from] - 1), [to]: c[to] + 1 } : c))

  /** Move a job out of `from` into `to` right away; roll back if the server refuses. */
  const optimistic = async (job: JobOut, from: View, to: View, send: () => Promise<JobOut>): Promise<boolean> => {
    removeRow(job.pk)
    shift(from, to)
    try {
      await send()
      return true
    } catch (err) {
      shift(to, from)
      addRow(job, from)
      toast.error(messageOf(err))
      return false
    }
  }

  const move = async (job: JobOut, to: Status) => {
    const from = job.status
    const ok = await optimistic(job, from, to, () => api.send<JobOut>("PATCH", jobPath(job.pk), { status: to }))
    if (!ok) return
    toast(`Moved to ${STATUS_LABELS[to]}`, {
      action: {
        label: "Undo",
        onClick: () => {
          void optimistic({ ...job, status: to }, to, from, () =>
            api.send<JobOut>("PATCH", jobPath(job.pk), { status: from }),
          ).then((back) => {
            if (back) addRow(job, from)
          })
        },
      },
    })
  }

  const restore = async (job: JobOut) => {
    const ok = await optimistic(job, "archived", job.status, () =>
      api.send<JobOut>("POST", `${jobPath(job.pk)}/restore`),
    )
    if (ok) toast(`Restored to ${STATUS_LABELS[job.status]}`)
  }

  const keep = async (job: JobOut) => {
    const ok = await optimistic(job, "below", "interested", () => api.send<JobOut>("POST", `${jobPath(job.pk)}/keep`))
    if (ok) toast(`Moved to ${STATUS_LABELS.interested}`)
  }

  return {
    view,
    setView,
    counts,
    countsError,
    load,
    retry: () => void Promise.all([loadCounts(), loadView(viewRef.current)]),
    refresh,
    move,
    restore,
    keep,
  }
}

export type JobsControl = ReturnType<typeof useJobs>
