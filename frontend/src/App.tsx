import { useCallback, useEffect, useState } from "react"

import { api, ApiError } from "@/api/client"
import type { AppState } from "@/api/types"
import { AppBar } from "@/components/AppBar"
import { ErrorNote } from "@/components/ErrorNote"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { navigate, useRoute, type Route } from "@/lib/route"
import { History } from "@/screens/History"
import { Main } from "@/screens/Main"
import { Placeholder } from "@/screens/Placeholder"
import { Settings } from "@/screens/Settings"
import { Setup } from "@/screens/Setup"

type Load = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; state: AppState }

/** What every screen gets: the app's state and a way to load it again after a change (mode, setup). */
export interface ScreenProps {
  state: AppState
  reloadState: () => Promise<void>
}

function useAppState() {
  const [load, setLoad] = useState<Load>({ kind: "loading" })
  const reload = useCallback(async () => {
    try {
      setLoad({ kind: "ready", state: await api.get<AppState>("/api/state") })
    } catch (err) {
      // A corrupt settings file is a 400 whose message says which file and what to do.
      setLoad({ kind: "error", message: err instanceof ApiError ? err.message : String(err) })
    }
  }, [])
  useEffect(() => {
    void reload()
  }, [reload])
  const retry = () => {
    setLoad({ kind: "loading" })
    void reload()
  }
  return { load, reload, retry }
}

function Screen({ route, ...props }: ScreenProps & { route: Route }) {
  const [section, sub] = route.params
  switch (section) {
    case undefined:
      return <Main {...props} />
    case "setup":
      return <Setup {...props} />
    case "settings":
      return <Settings {...props} />
    case "history":
      return <History id={sub} sample={props.state.mode === "sample"} />
    default:
      return <Placeholder title="Page not found" body="This address isn't part of AI Job Scout. Go back to your jobs." />
  }
}

export default function App() {
  const route = useRoute()
  const { load, reload, retry } = useAppState()

  // In real mode, nothing works before setup: send the user there first.
  const needsSetup = load.kind === "ready" && load.state.mode === "real" && !load.state.setup_complete
  const onSetup = route.params[0] === "setup"
  useEffect(() => {
    if (needsSetup && !onSetup) navigate("/setup")
  }, [needsSetup, onSetup])

  return (
    <TooltipProvider>
      <div className="flex min-h-svh flex-col">
        {load.kind === "loading" && (
          <>
            <AppBar />
            <p className="px-4 py-6 text-muted-foreground md:px-5" aria-live="polite">
              Loading…
            </p>
          </>
        )}
        {load.kind === "error" && (
          <>
            <AppBar />
            <main className="mx-auto w-full max-w-2xl px-4 py-12">
              <h1 className="mb-4 text-xl font-semibold">The app couldn't load</h1>
              <ErrorNote message={load.message} onRetry={retry} retryLabel="Try again" />
            </main>
          </>
        )}
        {load.kind === "ready" && (!needsSetup || onSetup) && (
          <Screen route={route} state={load.state} reloadState={reload} />
        )}
      </div>
      <Toaster position="bottom-right" />
    </TooltipProvider>
  )
}
