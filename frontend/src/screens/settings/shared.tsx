import { useCallback, useEffect, useId, useRef, useState, type ReactNode, type Ref } from "react"
import { CircleAlertIcon } from "lucide-react"

import type { AppState } from "@/api/types"
import { ErrorNote } from "@/components/ErrorNote"
import { describedBy, Field } from "@/components/Field"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { numberOrNull, useDraft } from "@/lib/form"
import { cn } from "@/lib/utils"
import { messageOf } from "@/screens/setup/wizard"
import { useSave } from "@/screens/setup/useSave"

/** What Settings gives every tab. */
export interface TabProps {
  state: AppState
  reloadState: () => Promise<void>
  /** Sample mode: everything is shown, nothing can be changed. */
  readOnly: boolean
  /** The tab has unsaved changes (Settings asks before leaving it). */
  onDirtyChange: (dirty: boolean) => void
}

// --- loading -----------------------------------------------------------------

type Load<T> = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; data: T }

/** Loads a tab's data, then renders it; a failure is an ErrorNote with Try again. */
export function Loaded<T>({ load, children }: { load: () => Promise<T>; children: (data: T) => ReactNode }) {
  const [state, setState] = useState<Load<T>>({ kind: "loading" })
  const run = useCallback(() => {
    setState({ kind: "loading" })
    load().then(
      (data) => setState({ kind: "ready", data }),
      (err: unknown) => setState({ kind: "error", message: messageOf(err) }),
    )
  }, [load])
  useEffect(run, [run])

  if (state.kind === "loading")
    return (
      <p className="text-muted-foreground" aria-live="polite">
        Loading your settings…
      </p>
    )
  if (state.kind === "error") return <ErrorNote message={state.message} onRetry={run} retryLabel="Try again" />
  return <>{children(state.data)}</>
}

// --- the form ------------------------------------------------------------------

/**
 * A tab's draft and its save: tells Settings when there are unsaved changes, and after a refused save moves focus
 * to the first highlighted field.
 */
export function useTabForm<T>(loaded: T, onDirtyChange: (dirty: boolean) => void) {
  const draft = useDraft(loaded)
  const save = useSave()
  const root = useRef<HTMLDivElement>(null)

  useEffect(() => {
    onDirtyChange(draft.dirty)
  }, [draft.dirty, onDirtyChange])
  useEffect(() => () => onDirtyChange(false), [onDirtyChange])

  useEffect(() => {
    if (!save.error?.fields) return
    const first = root.current?.querySelector<HTMLElement>('[aria-invalid="true"]')
    if (!first) return
    first.focus({ preventScroll: true })
    first.scrollIntoView({ block: "center" }) // clear of the save bar at the bottom
  }, [save.error])

  const fields = save.error?.fields ?? null
  return { draft, save, root, fields }
}

interface SaveBarProps {
  dirty: boolean
  saving: boolean
  /** Something else is in flight (a test, connecting Notion): saving now could undo what it saves. */
  busy?: boolean
  error: { message: string; fields: Record<string, string> | null } | null
  onSave: () => void
  onDiscard: () => void
}

/** The bar at the bottom of a tab: Save changes and Discard, and why a save failed. */
export function SaveBar({ dirty, saving, busy = false, error, onSave, onDiscard }: SaveBarProps) {
  const summary = error ? (error.fields ? "Fix the highlighted fields." : error.message) : null
  return (
    <div
      className={cn(
        "bottom-0 z-10 -mx-4 mt-10 flex flex-col gap-3 border-t bg-background px-4 py-3 md:-mx-5 md:px-5",
        // Stays in view while there's something to save (or a reason it wasn't saved).
        (dirty || error) && "sticky",
      )}
    >
      {summary && (
        <p role="alert" className="flex items-start gap-1.5 text-13 font-medium text-destructive">
          <CircleAlertIcon className="mt-px size-4 shrink-0" aria-hidden="true" />
          {summary}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-13 text-muted-foreground" aria-live="polite">
          {saving ? "Saving…" : dirty ? "You have unsaved changes." : ""}
        </p>
        <div className="ml-auto flex gap-2">
          <Button type="button" variant="outline" className="bg-card" onClick={onDiscard} disabled={!dirty || saving || busy}>
            Discard
          </Button>
          <Button type="button" onClick={onSave} disabled={!dirty || saving || busy}>
            Save changes
          </Button>
        </div>
      </div>
    </div>
  )
}

/** A tab's body: read-only (every input disabled) in sample mode. */
export function TabBody({ readOnly, children, root }: { readOnly: boolean; children: ReactNode; root?: Ref<HTMLDivElement> }) {
  return (
    <div ref={root}>
      <fieldset disabled={readOnly} className="flex min-w-0 flex-col gap-10">
        {children}
      </fieldset>
    </div>
  )
}

// --- inputs ------------------------------------------------------------------

interface SwitchRowProps {
  label: string
  /** What it does, and what it costs when it costs something. */
  hint: ReactNode
  checked: boolean
  onCheckedChange: (on: boolean) => void
  disabled?: boolean
  className?: string
}

/** A switch with its label and a one-line explanation. */
export function SwitchRow({ label, hint, checked, onCheckedChange, disabled, className }: SwitchRowProps) {
  const id = useId()
  return (
    <div className={cn("grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-3 gap-y-1", className)}>
      <Switch
        id={id}
        checked={checked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
        aria-describedby={`${id}-hint`}
      />
      <Label htmlFor={id} className="leading-snug">
        {label}
      </Label>
      <p id={`${id}-hint`} className="col-start-2 text-13 text-muted-foreground">
        {hint}
      </p>
    </div>
  )
}

interface NumberFieldProps {
  label: string
  hint: ReactNode
  value: number | null
  onChange: (value: number | null) => void
  error?: string
  min?: number
  max?: number
  step?: number
  disabled?: boolean
  placeholder?: string
}

/** A number box. Empty is null; the server says whether that's allowed. */
export function NumberField({ label, hint, value, onChange, error, min, max, step = 1, disabled, placeholder }: NumberFieldProps) {
  const id = useId()
  return (
    <Field id={id} label={label} hint={hint} error={error}>
      <Input
        id={id}
        type="number"
        inputMode={step < 1 ? "decimal" : "numeric"}
        value={value === null || Number.isNaN(value) ? "" : value}
        onChange={(e) => onChange(numberOrNull(e.target.value))}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        placeholder={placeholder}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(id, hint, error)}
        className="num w-36 bg-card placeholder:font-sans"
      />
    </Field>
  )
}
