import { HistoryIcon, SettingsIcon } from "lucide-react"

import { AppBar, BarLink } from "@/components/AppBar"
import { EmptyState } from "@/components/EmptyState"

/** Stand-in for a screen that isn't built yet. Each one is replaced by its own screen. */
export function Placeholder({ title, body, current }: { title: string; body: string; current?: "history" | "settings" }) {
  return (
    <>
      <AppBar>
        <BarLink href="#/history" icon={HistoryIcon} current={current === "history"}>
          History
        </BarLink>
        <BarLink href="#/settings/profile" icon={SettingsIcon} current={current === "settings"}>
          Settings
        </BarLink>
      </AppBar>
      <main className="mx-auto w-full max-w-3xl px-4 py-6 md:px-5">
        <h1 className="text-xl font-semibold">{title}</h1>
        <EmptyState title="Coming next" body={body} />
      </main>
    </>
  )
}
