import { useCallback, useEffect, useState } from "react"

import { api, ApiError } from "@/api/client"
import type { Keys, Settings } from "@/api/types"
import type { ScreenProps } from "@/App"
import { EmptyState } from "@/components/EmptyState"
import { ErrorNote } from "@/components/ErrorNote"
import { Button } from "@/components/ui/button"
import { canFindJobs, useSavedEstimate } from "@/lib/estimate"
import { money } from "@/lib/format"
import { readiness, type Missing } from "@/lib/readiness"
import { sourcesText } from "@/lib/sources"
import { ResultsTable } from "./main/ResultsTable"
import { RunPanel } from "./main/RunPanel"
import { TopBar, WINDOWS, type WindowValue } from "./main/TopBar"
import { useJobs } from "./main/useJobs"
import { useRun } from "./main/useRun"

type SettingsLoad = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; settings: Settings }

function useSettings() {
  const [load, setLoad] = useState<SettingsLoad>({ kind: "loading" })
  const reload = useCallback(async () => {
    setLoad({ kind: "loading" })
    try {
      setLoad({ kind: "ready", settings: await api.get<Settings>("/api/settings") })
    } catch (err) {
      setLoad({ kind: "error", message: err instanceof ApiError ? err.message : String(err) })
    }
  }, [])
  useEffect(() => {
    void reload()
  }, [reload])
  return { load, reload }
}

/**
 * What a real search still needs (lib/readiness), or null while it loads; `reload` reads the settings, keys and
 * profile again. Sample data needs nothing. If they can't be read, nothing is listed: starting a run then says
 * what's wrong.
 */
function useReadiness(sample: boolean) {
  const [load, setLoad] = useState<Missing[] | "failed" | null>(null)
  const reload = useCallback(async () => {
    try {
      const [settings, keys, profile] = await Promise.all([
        api.get<Settings>("/api/settings"),
        api.get<Keys>("/api/keys"),
        api.get<{ text: string }>("/api/profile"),
      ])
      setLoad(readiness(settings, keys, profile.text, "real"))
    } catch {
      setLoad("failed")
    }
  }, [])
  useEffect(() => {
    if (!sample) void reload()
  }, [sample, reload])
  const missing: Missing[] | null = sample || load === "failed" ? [] : load
  return { missing, reload }
}


/** The main screen (layout A): the run on the left, the results table as the main area. */
export function Main({ state }: ScreenProps) {
  const settings = useSettings()
  const { load: estimateLoad, reload: reloadEstimate } = useSavedEstimate()
  const estimate = estimateLoad.kind === "ready" ? estimateLoad.estimate : null
  const sample = state.mode === "sample"
  const jobs = useJobs()
  const { refresh } = jobs
  const onRunEnd = useCallback(() => {
    void refresh()
    void reloadEstimate()
  }, [refresh, reloadEstimate])
  const run = useRun(onRunEnd)
  const [searchWindow, setSearchWindow] = useState<WindowValue>("last")

  const s = settings.load.kind === "ready" ? settings.load.settings : null
  const { missing, reload: reloadReadiness } = useReadiness(sample)
  // Nothing missing (and known): only then is a cost worth showing, or a run worth starting.
  const ready = missing !== null && missing.length === 0
  const busy = run.running || run.starting
  const canFind = canFindJobs(sample, estimateLoad, busy) && ready

  // The server refused the start because of the setup: read it again, so the run panel can say what to fix.
  const { startError, startErrorIsConfig } = run
  useEffect(() => {
    if (startError && startErrorIsConfig) void reloadReadiness()
  }, [startError, startErrorIsConfig, reloadReadiness])
  const findJobs = () => {
    if (!canFind) return
    const days = WINDOWS.find((w) => w.value === searchWindow)?.days ?? null
    void run.start(days)
  }

  const total = jobs.counts ? Object.values(jobs.counts).reduce((a, b) => a + b, 0) : null
  const firstUse = run.load === "ready" && run.run === null && total === 0

  const bootError =
    settings.load.kind === "error"
      ? settings.load.message
      : run.load === "error"
        ? run.loadError
        : jobs.countsError && !jobs.counts
          ? jobs.countsError
          : null
  const retryBoot = () => {
    if (settings.load.kind === "error") void settings.reload()
    if (run.load === "error") void run.retryLoad()
    if (jobs.countsError) jobs.retry()
  }

  return (
    <>
      <TopBar
        settings={s}
        estimate={estimateLoad}
        onRetryEstimate={() => void reloadEstimate()}
        sample={sample}
        searchWindow={searchWindow}
        onWindowChange={setSearchWindow}
        busy={busy}
        canFind={canFind}
        onFindJobs={findJobs}
        missing={missing}
        firstRun={run.load === "ready" && run.run === null}
      />
      <h1 className="sr-only">Jobs</h1>
      {bootError ? (
        <div className="mx-auto w-full max-w-2xl px-4 py-12">
          <ErrorNote message={bootError} onRetry={retryBoot} retryLabel="Try again" />
        </div>
      ) : (
        <div className="flex flex-1 flex-col md:grid md:grid-cols-[300px_minmax(0,1fr)]">
          <aside className="border-b bg-card px-4 py-4 md:border-r md:border-b-0 md:px-5">
            {run.load === "loading" ? (
              <p className="text-muted-foreground" aria-live="polite">
                Loading the last run…
              </p>
            ) : (
              <RunPanel control={run} missing={missing ?? []} />
            )}
          </aside>
          <main className={`min-w-0 px-4 py-4 md:px-5${s && firstUse ? " flex flex-col" : ""}`}>
            {!s ? (
              <p className="py-6 text-muted-foreground" aria-live="polite">
                Loading jobs…
              </p>
            ) : firstUse ? (
              <EmptyState
                centered
                title="No jobs yet"
                body={
                  <>
                    Press Find jobs to search {sourcesText(s)}.
                    {sample ? (
                      " Sample runs are free."
                    ) : ready && estimate && (
                      <>
                        {" "}
                        A run can cost up to <span className="num">{money(estimate.max_usd)}</span> on Apify.
                      </>
                    )}
                  </>
                }
                action={
                  <Button variant="outline" className="bg-card" onClick={findJobs} disabled={!canFind}>
                    {busy ? "Running…" : "Find jobs"}
                  </Button>
                }
              />
            ) : (
              <ResultsTable
                view={jobs.view}
                onViewChange={jobs.setView}
                counts={jobs.counts}
                load={jobs.load}
                onRetry={jobs.retry}
                columns={s.ui.columns}
                defaultSort={s.ui.default_sort}
                onMove={(job, to) => void jobs.move(job, to)}
                onRestore={(job) => void jobs.restore(job)}
                onKeep={(job) => void jobs.keep(job)}
              />
            )}
          </main>
        </div>
      )}
    </>
  )
}
