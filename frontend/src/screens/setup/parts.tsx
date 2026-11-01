import { useId, type ReactNode } from "react"

import type { Keys, KeysUpdate, Settings } from "@/api/types"
import { ErrorNote } from "@/components/ErrorNote"
import { Button } from "@/components/ui/button"

/** How a step moves on, and how it saves. Setup gives every step the same ones. */
export interface StepProps {
  /** Go to the previous step; null on the first one. */
  back: (() => void) | null
  next: () => void
  sample: () => void
  sampleBusy: boolean
  /** PUT /api/settings with this step's fields over a fresh GET, so nothing saved elsewhere is overwritten. */
  saveSettings: (patch: (s: Settings) => Settings) => Promise<Settings>
  /** PUT /api/keys with only the keys given. */
  saveKeys: (update: KeysUpdate) => Promise<Keys>
}

export { Section } from "@/components/Section"
export { TestLine } from "@/components/TestLine"

interface StepFooterProps extends Pick<StepProps, "back" | "sample" | "sampleBusy"> {
  onNext: () => void
  nextLabel?: string
  nextDisabled?: boolean
  /** Next is the step's main action (the accent button); otherwise it's an outline button. */
  nextPrimary?: boolean
  saving?: boolean
  /** Why Next is disabled, or what it needs. */
  note?: ReactNode
  /** A save that failed, not tied to one field. */
  error?: string | null
  /** Extra buttons before Next (the last step's "Not now"). */
  extra?: ReactNode
}

/** Back, Next, the reason Next is waiting, a failed save, and the way out to sample data. */
export function StepFooter({
  back,
  sample,
  sampleBusy,
  onNext,
  nextLabel = "Next",
  nextDisabled,
  nextPrimary = true,
  saving,
  note,
  error,
  extra,
}: StepFooterProps) {
  const noteId = useId()
  return (
    <div className="mt-8 flex flex-col gap-4 border-t pt-6">
      {error && <ErrorNote message={error} />}
      <div className="flex flex-wrap items-center gap-3">
        {back && (
          <Button type="button" variant="outline" className="bg-card" onClick={back} disabled={saving}>
            Back
          </Button>
        )}
        <div className="ml-auto flex flex-wrap items-center justify-end gap-3">
          {/* Always rendered (empty when there is no note): a live region must exist before its text changes. */}
          <div id={noteId} role="status" aria-live="polite" className="text-13 text-muted-foreground">
            {note}
          </div>
          {extra}
          <Button
            type="button"
            variant={nextPrimary ? "default" : "outline"}
            className={nextPrimary ? undefined : "bg-card"}
            onClick={onNext}
            disabled={nextDisabled || saving}
            aria-describedby={note ? noteId : undefined}
          >
            {saving ? "Saving…" : nextLabel}
          </Button>
        </div>
      </div>
      <p className="text-13 text-muted-foreground">
        Not ready to add your own keys?{" "}
        <button
          type="button"
          onClick={sample}
          disabled={sampleBusy}
          className="rounded-sm font-medium text-foreground underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring disabled:opacity-50"
        >
          {sampleBusy ? "Opening sample data…" : "Try it with sample data instead"}
        </button>
      </p>
    </div>
  )
}
