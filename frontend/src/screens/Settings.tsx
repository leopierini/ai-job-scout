import { useCallback, useEffect, useRef, useState, type ReactNode } from "react"
import { HistoryIcon, SettingsIcon } from "lucide-react"

import type { ScreenProps } from "@/App"
import { AppBar, BarLink } from "@/components/AppBar"
import { SampleStrip } from "@/components/SampleStrip"
import { ScrollStrip } from "@/components/ScrollStrip"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import type { SettingsTab } from "@/lib/readiness"
import { navigate, setLeaveGuard, useRoute } from "@/lib/route"
import { cn } from "@/lib/utils"
import { AITab } from "./settings/AITab"
import { DataTab } from "./settings/DataTab"
import { KeysTab } from "./settings/KeysTab"
import { OutputsTab } from "./settings/OutputsTab"
import { ProfileTab } from "./settings/ProfileTab"
import { SearchTab } from "./settings/SearchTab"
import { StartupsTab } from "./settings/StartupsTab"
import type { TabProps } from "./settings/shared"

const SETTINGS_TABS = [
  { id: "profile", label: "Profile & rules", Tab: ProfileTab },
  { id: "search", label: "Search", Tab: SearchTab },
  { id: "startups", label: "Startups", Tab: StartupsTab },
  { id: "ai", label: "AI", Tab: AITab },
  { id: "outputs", label: "Outputs", Tab: OutputsTab },
  { id: "keys", label: "Keys", Tab: KeysTab },
  { id: "data", label: "Data", Tab: DataTab },
] as const satisfies readonly { id: string; label: string; Tab: (p: TabProps) => ReactNode }[]

/** Fails to compile when a tab that lib/readiness links to (#/settings/<tab>) is renamed or removed above. */
type MustBeTrue<T extends true> = T
export type ReadinessTabsExist = MustBeTrue<[SettingsTab] extends [(typeof SETTINGS_TABS)[number]["id"]] ? true : false>

/** Settings, one tab per address (#/settings/<tab>). Leaving a tab with unsaved changes asks first. */
export function Settings({ state, reloadState }: ScreenProps) {
  const route = useRoute()
  const current = SETTINGS_TABS.find((t) => t.id === route.params[1])
  const tab = current ?? SETTINGS_TABS[0]
  const readOnly = state.mode === "sample"
  const [dirty, setDirty] = useState(false)
  const [leaving, setLeaving] = useState<string | null>(null) // the address the user tried to go to
  const release = useRef<(() => void) | null>(null)

  // #/settings or an unknown tab: show the first one, at its own address.
  useEffect(() => {
    if (!current) navigate(`/settings/${SETTINGS_TABS[0].id}`)
  }, [current])

  useEffect(() => {
    if (!dirty) return
    release.current = setLeaveGuard((target) => {
      setLeaving(target)
      return false
    })
    const onUnload = (e: BeforeUnloadEvent) => e.preventDefault() // the browser asks before closing the page
    window.addEventListener("beforeunload", onUnload)
    return () => {
      release.current?.()
      release.current = null
      window.removeEventListener("beforeunload", onUnload)
    }
  }, [dirty])

  const onDirtyChange = useCallback((d: boolean) => setDirty(d), [])

  const discardAndLeave = () => {
    const target = leaving
    setLeaving(null)
    release.current?.()
    release.current = null
    setDirty(false)
    if (target !== null) window.location.hash = target
  }

  const { Tab } = tab
  return (
    <>
      {readOnly && <SampleStrip note="Settings can't be changed here." />}
      <AppBar>
        <BarLink href="#/history" icon={HistoryIcon}>
          History
        </BarLink>
        <BarLink href="#/settings/profile" icon={SettingsIcon} current>
          Settings
        </BarLink>
      </AppBar>
      <main className="mx-auto w-full max-w-3xl px-4 pt-6 md:px-5">
        <h1 className="text-xl font-semibold">Settings</h1>
        <nav aria-label="Settings sections" className="mt-4 border-b">
          <ScrollStrip>
            <ul className="flex gap-4 sm:gap-5">
              {SETTINGS_TABS.map((t) => (
                <li key={t.id} className="shrink-0">
                  <a
                    href={`#/settings/${t.id}`}
                    aria-current={t.id === tab.id ? "page" : undefined}
                    className={cn(
                      "inline-flex h-10 items-center border-b-2 border-transparent text-13 font-medium whitespace-nowrap text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring sm:text-sm",
                      t.id === tab.id && "border-foreground text-foreground",
                    )}
                  >
                    {t.label}
                  </a>
                </li>
              ))}
            </ul>
          </ScrollStrip>
        </nav>
        <div className={cn("pt-8", tab.id === "data" || readOnly ? "pb-8" : "pb-0")}>
          <Tab key={tab.id} state={state} reloadState={reloadState} readOnly={readOnly} onDirtyChange={onDirtyChange} />
        </div>
      </main>

      <AlertDialog open={leaving !== null} onOpenChange={(o) => !o && setLeaving(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Discard unsaved changes?</AlertDialogTitle>
            <AlertDialogDescription>The changes on this tab haven't been saved. They'll be lost if you leave.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="bg-card">Keep editing</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={discardAndLeave}>
              Discard changes
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
