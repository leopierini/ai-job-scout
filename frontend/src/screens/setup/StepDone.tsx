import { useEffect, useState, type ReactNode } from "react"

import { api } from "@/api/client"
import type { Keys, Settings } from "@/api/types"
import { CostFailed } from "@/components/EstimateNote"
import { ReadinessLine } from "@/components/ReadinessLine"
import { Button } from "@/components/ui/button"
import { useSavedEstimate } from "@/lib/estimate"
import { money } from "@/lib/format"
import { navigate } from "@/lib/route"
import { providerInfo } from "@/lib/providers"
import { readiness } from "@/lib/readiness"
import { StepFooter, type StepProps } from "./parts"
import { useSave } from "./useSave"
import { wizardHref } from "./wizard"

interface StepDoneProps extends StepProps {
  /** The settings as saved by the steps before. */
  saved: Settings
  keys: Keys
  /** The profile as the You step saved it. */
  profile: string
  reloadState: () => Promise<void>
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

/** "LinkedIn in 3 regions with 5 keywords" and/or "Indeed in 2 countries with 1 search". */
function searchText(s: Settings): ReactNode {
  const parts: string[] = []
  if (s.linkedin.enabled)
    parts.push(
      `LinkedIn in ${plural(s.linkedin.regions.length, "region", "regions")} with ${plural(s.linkedin.keywords.length, "keyword", "keywords")}`,
    )
  if (s.indeed.enabled)
    parts.push(
      `Indeed in ${plural(s.indeed.countries.length, "country", "countries")} with ${plural(s.indeed.queries.length, "search", "searches")}`,
    )
  return parts.length ? parts.join(", and ") : "No source is on. Go back to turn on LinkedIn or Indeed."
}

function outputsText(s: Settings): string {
  const names = [
    "AI Job Scout",
    s.outputs.notion.enabled && "Notion",
    s.outputs.csv.enabled && "a CSV file",
    s.outputs.webhook.enabled && "a webhook",
  ].filter((n): n is string => Boolean(n))
  return names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}` : names[0]
}

export function StepDone({ saved, keys, profile, reloadState, ...nav }: StepDoneProps) {
  const [settings, setSettings] = useState(saved)
  // Worked out once the screen is up: "Create database" and the steps before saved on the server.
  const { load: estimateLoad, reload: reloadEstimate } = useSavedEstimate()
  const estimate = estimateLoad.kind === "ready" ? estimateLoad.estimate : null
  const [running, setRunning] = useState<"run" | "later" | null>(null)
  const save = useSave()

  // Read the settings again too: "Create database" and the steps before saved on the server.
  useEffect(() => {
    let live = true
    api.get<Settings>("/api/settings").then((s) => live && setSettings(s), () => {})
    return () => {
      live = false
    }
  }, [])

  const finish = async (run: boolean) => {
    setRunning(run ? "run" : "later")
    const ok = await save.run(async () => {
      await nav.saveSettings((s) => ({ ...s, ui: { ...s.ui, setup_complete: true } }))
      await api.send("PUT", "/api/mode", { mode: "real" })
      if (run) await api.send("POST", "/api/runs", { kind: "run" })
      await reloadState()
    })
    setRunning(null)
    if (ok) navigate("/")
  }

  const nothingToSearch = estimate !== null && estimate.searches === 0
  // Finishing switches to real mode, so that's what has to be ready.
  const missing = readiness(settings, keys, profile, "real")
  const p = providerInfo(settings.scoring.provider)
  const rows: [string, ReactNode][] = [
    ["AI", `${p.label}, model ${settings.scoring.model}`],
    ["Search", searchText(settings)],
    ["Results go to", outputsText(settings)],
    [
      "First run",
      // No cost for a search that can't run yet: "up to $0.00" reads like a free run.
      missing.length > 0 ? (
        <span key="not-set-up" className="font-normal text-muted-foreground">Shown once your search is set up</span>
      ) : estimate ? (
        <>
          Up to <span className="num">{money(estimate.max_usd)}</span> on Apify (<span className="num">{estimate.searches}</span>{" "}
          {estimate.searches === 1 ? "search" : "searches"})
        </>
      ) : estimateLoad.kind === "failed" ? (
        <CostFailed onRetry={() => void reloadEstimate()} className="font-normal" />
      ) : (
        "Working it out…"
      ),
    ],
  ]

  return (
    <>
      <dl className="grid gap-x-6 gap-y-4 rounded-lg border bg-card p-4 sm:grid-cols-[10rem_minmax(0,1fr)]">
        {rows.map(([term, detail]) => (
          <div key={term} className="flex flex-col gap-1 sm:contents">
            <dt className="text-muted-foreground">{term}</dt>
            <dd className="font-medium">{detail}</dd>
          </div>
        ))}
      </dl>
      <StepFooter
        {...nav}
        onNext={() => void finish(true)}
        nextLabel={running === "run" ? "Starting…" : "Find jobs"}
        nextDisabled={running !== null || missing.length > 0 || nothingToSearch || estimate === null}
        note={
          missing.length > 0 ? (
            <ReadinessLine missing={missing} href={wizardHref} />
          ) : nothingToSearch
            ? "Nothing to search yet. Go back to add regions and keywords."
            : estimateLoad.kind === "failed"
              ? "Find jobs needs the cost first."
              : undefined
        }
        error={save.error?.message ?? null}
        extra={
          <Button
            type="button"
            variant="outline"
            className="bg-card"
            onClick={() => void finish(false)}
            disabled={running !== null}
          >
            {running === "later" ? "Saving…" : "Not now"}
          </Button>
        }
      />
    </>
  )
}
