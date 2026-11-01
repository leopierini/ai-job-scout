import { HistoryIcon, SettingsIcon } from "lucide-react"

import type { Settings } from "@/api/types"
import { AppBar, BarLink } from "@/components/AppBar"
import { CostFailed } from "@/components/EstimateNote"
import { ReadinessLine } from "@/components/ReadinessLine"
import { SampleStrip } from "@/components/SampleStrip"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import type { EstimateLoad } from "@/lib/estimate"
import { money } from "@/lib/format"
import type { Missing } from "@/lib/readiness"

/** The window select's values: "last" is "since the last run" (window_days null). */
export const WINDOWS = [
  { value: "last", label: "Since last run", days: null },
  { value: "1", label: "Last 24 hours", days: 1 },
  { value: "7", label: "Last 7 days", days: 7 },
  { value: "30", label: "Last 30 days", days: 30 },
] as const
export type WindowValue = (typeof WINDOWS)[number]["value"]

/** The saved headline, or the first two keywords and the places searched. */
export function headlineFor(settings: Settings): string {
  if (settings.ui.headline.trim()) return settings.ui.headline.trim()
  const { linkedin, indeed } = settings
  const keywords = linkedin.enabled ? linkedin.keywords : indeed.queries
  const places = [
    ...(linkedin.enabled ? linkedin.regions.map((r) => r.name) : []),
    ...(indeed.enabled ? indeed.countries.map((c) => c.toUpperCase()) : []),
  ]
  return [...keywords.slice(0, 2), ...new Set(places)].join(" · ")
}

interface TopBarProps {
  settings: Settings | null
  estimate: EstimateLoad
  onRetryEstimate: () => void
  sample: boolean
  searchWindow: WindowValue
  onWindowChange: (value: WindowValue) => void
  busy: boolean
  /** Find jobs can start a run (nothing running, and with real data the cost is known). */
  canFind: boolean
  onFindJobs: () => void
  /** What a real search still needs (null while it loads): listed under the bar; the cost waits for it. */
  missing: Missing[] | null
  /** No run yet: the list reads "Before your first search". */
  firstRun: boolean
}

export function TopBar(props: TopBarProps) {
  const { settings, estimate: load, onRetryEstimate, sample, searchWindow, onWindowChange, busy, canFind, onFindJobs } = props
  const { missing, firstRun } = props
  const headline = settings ? headlineFor(settings) : ""
  // A cost is only shown for a search that can run: "up to $0.00" for an empty setup reads like a free run.
  const showCost = !sample && missing !== null && missing.length === 0
  const estimate = showCost && load.kind === "ready" ? load.estimate : null
  return (
    <>
      {sample && <SampleStrip />}
      <AppBar
        lead={
          headline && (
            <p className="min-w-0 text-13 text-muted-foreground md:max-w-md md:truncate lg:max-w-xl" title={headline}>
              {headline}
            </p>
          )
        }
      >
        {showCost && load.kind === "loading" && (
          <p className="text-13 leading-tight text-muted-foreground md:text-right">Working out the cost…</p>
        )}
        {showCost && load.kind === "failed" && (
          <CostFailed onRetry={onRetryEstimate} className="text-13 leading-tight text-muted-foreground md:text-right" />
        )}
        {sample && <p className="text-13 leading-tight text-muted-foreground md:text-right">Sample runs are free</p>}
        {estimate && (
          <p className="text-13 leading-tight text-muted-foreground md:text-right">
            Next run up to <span className="num text-foreground">{money(estimate.max_usd)}</span>
            {estimate.credit_usd !== null && (
              <>
                <br />
                Apify credit left <span className="num text-foreground">{money(estimate.credit_usd)}</span>
              </>
            )}
          </p>
        )}
        <BarLink href="#/history" icon={HistoryIcon}>
          History
        </BarLink>
        <BarLink href="#/settings/profile" icon={SettingsIcon}>
          Settings
        </BarLink>
        <div className="flex items-center gap-2">
          <Select value={searchWindow} onValueChange={(v) => onWindowChange(v as WindowValue)} disabled={busy}>
            <SelectTrigger size="sm" aria-label="Search window" className="bg-card">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {WINDOWS.map((w) => (
                <SelectItem key={w.value} value={w.value}>
                  {w.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button onClick={onFindJobs} disabled={!canFind}>
            {busy ? "Running…" : "Find jobs"}
          </Button>
        </div>
      </AppBar>
      {missing && missing.length > 0 && (
        <div className="border-b bg-neutral-soft px-4 py-2 md:px-5">
          <ReadinessLine missing={missing} lead={firstRun ? "Before your first search:" : "Before your next search:"} />
        </div>
      )}
    </>
  )
}
