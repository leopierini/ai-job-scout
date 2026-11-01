import { HistoryIcon, SettingsIcon } from "lucide-react"

import { AppBar, BarLink } from "@/components/AppBar"
import { SampleStrip } from "@/components/SampleStrip"
import { StatusPill } from "@/components/StatusPill"
import type { EffectiveStatus } from "@/lib/run"

/** The top bar every history screen shares, under the sample-data line while the sample data is shown. */
export function HistoryBar({ sample }: { sample: boolean }) {
  return (
    <>
      {sample && <SampleStrip />}
      <AppBar>
        <BarLink href="#/history" icon={HistoryIcon} current>
          History
        </BarLink>
        <BarLink href="#/settings/profile" icon={SettingsIcon}>
          Settings
        </BarLink>
      </AppBar>
    </>
  )
}

/** A run's pill: its status, or "Interrupted" for one still marked running whose process is gone. */
export function RunStatusPill({ status }: { status: EffectiveStatus }) {
  return status === "interrupted" ? <StatusPill status="error" label="Interrupted" /> : <StatusPill status={status} />
}
