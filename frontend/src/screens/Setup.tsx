import { useCallback, useEffect, useRef, useState } from "react"
import { toast } from "sonner"

import { api } from "@/api/client"
import type { Keys, KeysUpdate, Settings } from "@/api/types"
import type { ScreenProps } from "@/App"
import { AppBar } from "@/components/AppBar"
import { ErrorNote } from "@/components/ErrorNote"
import { useRegions } from "@/lib/regions"
import { navigate, useRoute } from "@/lib/route"
import type { StepProps } from "./setup/parts"
import { StepAI } from "./setup/StepAI"
import { StepDone } from "./setup/StepDone"
import { StepOutputs } from "./setup/StepOutputs"
import { StepSearch } from "./setup/StepSearch"
import { StepYou } from "./setup/StepYou"
import {
  STEP_COUNT,
  defaultCsvPath,
  messageOf,
  progressLine,
  seedWizard,
  stepFrom,
  type WizardState,
} from "./setup/wizard"

const STEP_TEXT: Record<number, { title: string; intro: string }> = {
  1: {
    title: "Choose an AI",
    intro: "It drafts your profile now, and later reads each job and scores it against that profile.",
  },
  2: {
    title: "Tell it about you",
    intro: "Your CV and a few answers. The AI drafts a profile from them, and you check it before it's saved.",
  },
  3: {
    title: "Choose what to search",
    intro: "Pick the job titles to look for, and connect Apify, which runs the searches.",
  },
  4: {
    title: "Choose where results go",
    intro: "Your jobs always appear here. You can also send them to Notion, a CSV file or a webhook.",
  },
  5: {
    title: "You're ready",
    intro: "This is your search. You can change any of it later in Settings.",
  },
}

type Boot =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; saved: Settings; keys: Keys; wizard: WizardState }

/**
 * The first-launch wizard: AI, You, Search, Results go to, Done. The step is in the address
 * (#/setup?step=N), everything typed lives here so Back loses nothing, and each Next saves its step.
 */
export function Setup({ state, reloadState }: ScreenProps) {
  const route = useRoute()
  const step = stepFrom(route.query.get("step"))
  const [boot, setBoot] = useState<Boot>({ kind: "loading" })
  const [sampleBusy, setSampleBusy] = useState(false)
  const { regions } = useRegions()
  const headingRef = useRef<HTMLHeadingElement>(null)
  const firstStep = useRef(true)
  const leaving = useRef(false) // going to the sample data: don't switch back to real mode on the way out
  const sampleMode = state.mode === "sample"

  const load = useCallback(async () => {
    if (leaving.current) return
    setBoot({ kind: "loading" })
    try {
      // The wizard sets up the user's own search: leave the sample data first.
      if (sampleMode) {
        await api.send("PUT", "/api/mode", { mode: "real" })
        await reloadState()
        return // the state changes to real mode, and this runs again
      }
      const [saved, profile, keys] = await Promise.all([
        api.get<Settings>("/api/settings"),
        api.get<{ text: string }>("/api/profile"),
        api.get<Keys>("/api/keys"),
      ])
      setBoot({ kind: "ready", saved, keys, wizard: seedWizard(saved, profile.text, keys, state) })
    } catch (err) {
      setBoot({ kind: "error", message: messageOf(err) })
    }
    // `state` seeds the wizard once; reloading it later (after a save) must not reset what was typed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sampleMode, reloadState])

  useEffect(() => {
    void load()
  }, [load])

  // A new step: start at its top, with focus on its title so a screen reader reads where it is.
  useEffect(() => {
    if (firstStep.current) {
      firstStep.current = false
      return
    }
    window.scrollTo(0, 0)
    headingRef.current?.focus()
  }, [step])

  const update = useCallback((patch: (w: WizardState) => WizardState) => {
    setBoot((b) => (b.kind === "ready" ? { ...b, wizard: patch(b.wizard) } : b))
  }, [])

  const saveSettings = useCallback(async (patch: (s: Settings) => Settings) => {
    const fresh = await api.get<Settings>("/api/settings")
    const saved = await api.send<Settings>("PUT", "/api/settings", patch(fresh))
    setBoot((b) => (b.kind === "ready" ? { ...b, saved } : b))
    return saved
  }, [])

  const saveKeys = useCallback(async (keysUpdate: KeysUpdate) => {
    const keys = await api.send<Keys>("PUT", "/api/keys", keysUpdate)
    setBoot((b) => (b.kind === "ready" ? { ...b, keys } : b))
    return keys
  }, [])

  const trySample = async () => {
    setSampleBusy(true)
    leaving.current = true
    try {
      await api.send("PUT", "/api/mode", { mode: "sample" })
      await reloadState()
      navigate("/")
    } catch (err) {
      leaving.current = false
      toast.error(messageOf(err))
    } finally {
      setSampleBusy(false)
    }
  }

  const nav: StepProps = {
    back: step > 1 ? () => navigate(`/setup?step=${step - 1}`) : null,
    next: () => navigate(`/setup?step=${Math.min(step + 1, STEP_COUNT)}`),
    sample: () => void trySample(),
    sampleBusy,
    saveSettings,
    saveKeys,
  }

  const text = STEP_TEXT[step]
  return (
    <>
      <AppBar />
      <main className="mx-auto w-full max-w-3xl px-4 pt-6 pb-12 md:px-5">
        <p className="text-13 text-muted-foreground">{progressLine(step)}</p>
        <h1 ref={headingRef} tabIndex={-1} className="mt-1 text-xl font-semibold outline-none">
          {text.title}
        </h1>
        <p className="mt-1 text-muted-foreground">{text.intro}</p>
        <div className="mt-8">
          {boot.kind === "loading" && (
            <p className="text-muted-foreground" aria-live="polite">
              {sampleMode ? "Leaving the sample data…" : "Loading your setup…"}
            </p>
          )}
          {boot.kind === "error" && <ErrorNote message={boot.message} onRetry={() => void load()} retryLabel="Try again" />}
          {boot.kind === "ready" && (
            <StepBody
              step={step}
              boot={boot}
              update={update}
              nav={nav}
              regionNames={(regions?.linkedin ?? []).map((r) => r.name)}
              claudeCodeFound={state.claude_code_found}
              csvDefault={defaultCsvPath(state)}
              reloadState={reloadState}
            />
          )}
        </div>
      </main>
    </>
  )
}

interface StepBodyProps {
  step: number
  boot: Extract<Boot, { kind: "ready" }>
  update: (patch: (w: WizardState) => WizardState) => void
  nav: StepProps
  regionNames: string[]
  claudeCodeFound: boolean
  csvDefault: string
  reloadState: () => Promise<void>
}

function StepBody({ step, boot, update, nav, regionNames, claudeCodeFound, csvDefault, reloadState }: StepBodyProps) {
  const { wizard, keys, saved } = boot
  switch (step) {
    case 1:
      return (
        <StepAI
          {...nav}
          ai={wizard.ai}
          onChange={(patch) => update((w) => ({ ...w, ai: { ...w.ai, ...patch } }))}
          keys={keys}
          claudeCodeFound={claudeCodeFound}
        />
      )
    case 2:
      return (
        <StepYou
          {...nav}
          you={wizard.you}
          onChange={(patch) => update((w) => ({ ...w, you: { ...w.you, ...patch } }))}
          search={wizard.search}
          onSearchChange={(patch) => update((w) => ({ ...w, search: { ...w.search, ...patch } }))}
          regionNames={regionNames}
        />
      )
    case 3:
      return (
        <StepSearch
          {...nav}
          search={wizard.search}
          onChange={(patch) => update((w) => ({ ...w, search: { ...w.search, ...patch } }))}
          keys={keys}
          saved={saved}
          toRegions={() => navigate("/setup?step=2")}
        />
      )
    case 4:
      return (
        <StepOutputs
          {...nav}
          outputs={wizard.outputs}
          onChange={(patch) => update((w) => ({ ...w, outputs: patch(w.outputs) }))}
          keys={keys}
          csvDefault={csvDefault}
        />
      )
    default:
      return <StepDone {...nav} saved={saved} keys={keys} profile={wizard.you.profile} reloadState={reloadState} />
  }
}
