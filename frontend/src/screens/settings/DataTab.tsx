import { useCallback, useEffect, useId, useState } from "react"
import { DownloadIcon } from "lucide-react"
import { toast } from "sonner"

import { api } from "@/api/client"
import type { RescorePreview, StartRunBody } from "@/api/types"
import { describedBy, Field } from "@/components/Field"
import { Section } from "@/components/Section"
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
import { Button, buttonVariants } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { navigate } from "@/lib/route"
import { cn } from "@/lib/utils"
import { messageOf } from "@/screens/setup/wizard"
import { rescoreLine } from "./labels"
import type { TabProps } from "./shared"

const CONFIRM_WORD = "delete"

type PreviewLoad = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; preview: RescorePreview }

/** Tools for the job data: re-score, export, where the files are, and delete everything. Nothing here is a draft. */
export function DataTab({ state, readOnly }: TabProps) {
  const [preview, setPreview] = useState<PreviewLoad>({ kind: "loading" })
  const loadPreview = useCallback(async () => {
    setPreview({ kind: "loading" })
    try {
      setPreview({ kind: "ready", preview: await api.get<RescorePreview>("/api/rescore/preview") })
    } catch (e) {
      setPreview({ kind: "error", message: messageOf(e) })
    }
  }, [])
  useEffect(() => {
    void loadPreview()
  }, [loadPreview])

  const files = [
    { label: "App folder", path: state.paths.app_dir },
    { label: "Jobs database", path: state.paths.database },
    { label: "Settings", path: state.paths.settings },
    { label: "Profile", path: state.paths.profile },
  ]

  return (
    <div className="flex flex-col gap-10">
      <Section
        title="Re-score jobs"
        intro="After you change your profile or rules, score the jobs still in To review again so their scores follow the new rules."
      >
        <Rescore preview={preview} onRetry={() => void loadPreview()} readOnly={readOnly} />
      </Section>

      <Section title="Export to CSV" intro="Download every job you're tracking, with its score and status, as one file.">
        <div>
          <a href="/api/export.csv" download className={cn(buttonVariants({ variant: "outline" }), "bg-card")}>
            <DownloadIcon aria-hidden="true" />
            Export to CSV
          </a>
        </div>
      </Section>

      <Section title="Your files" intro="Everything stays on this computer, in these files. Your keys are kept in the app folder.">
        <dl className="flex flex-col gap-3">
          {files.map((f) => (
            <div key={f.label} className="flex flex-col gap-0.5 sm:grid sm:grid-cols-[10rem_minmax(0,1fr)] sm:gap-4">
              <dt className="text-13 text-muted-foreground">{f.label}</dt>
              <dd className="text-13 break-all">{f.path}</dd>
            </div>
          ))}
        </dl>
      </Section>

      {!readOnly && (
        <Section title="Setup" intro="Walk through the setup steps again. What you saved is filled in on each step.">
          <div>
            <a href="#/setup" className={cn(buttonVariants({ variant: "outline" }), "bg-card")}>
              Run the setup again
            </a>
          </div>
        </Section>
      )}

      <Section title="Delete all job data" intro="Start again with an empty list. Your settings, profile and keys are kept.">
        <DeleteAll readOnly={readOnly} onDeleted={() => void loadPreview()} />
      </Section>
    </div>
  )
}

function Rescore({ preview, onRetry, readOnly }: { preview: PreviewLoad; onRetry: () => void; readOnly: boolean }) {
  const [confirming, setConfirming] = useState(false)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (preview.kind === "loading")
    return (
      <p className="text-muted-foreground" aria-live="polite">
        Counting the jobs to score again…
      </p>
    )
  if (preview.kind === "error")
    return (
      <p className="text-13">
        <span className="font-medium text-destructive">{preview.message}</span>{" "}
        <Button type="button" variant="outline" size="sm" className="ml-2 bg-card" onClick={onRetry}>
          Try again
        </Button>
      </p>
    )

  const p = preview.preview
  const start = async () => {
    setStarting(true)
    setError(null)
    try {
      const body: StartRunBody = { kind: "rescore", window_days: null }
      await api.send("POST", "/api/runs", body)
      setConfirming(false)
      navigate("/")
    } catch (e) {
      setError(messageOf(e))
    } finally {
      setStarting(false)
    }
  }

  return (
    <div className="flex flex-col items-start gap-3">
      <p>{p.count === 0 ? "No jobs to score again: none are in To review." : rescoreLine(p)}</p>
      <Button
        type="button"
        variant="outline"
        className="bg-card"
        onClick={() => setConfirming(true)}
        disabled={readOnly || p.count === 0}
      >
        Re-score jobs
      </Button>
      {readOnly && <p className="text-13 text-muted-foreground">Not available with the sample data.</p>}
      <AlertDialog open={confirming} onOpenChange={(o) => !starting && setConfirming(o)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Re-score {p.count === 1 ? "1 job" : `${p.count} jobs`}?</AlertDialogTitle>
            <AlertDialogDescription>
              {rescoreLine(p)} Your AI provider charges for the tokens. Statuses are kept.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {error && (
            <p role="alert" className="text-13 font-medium text-destructive">
              {error}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel className="bg-card" disabled={starting}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              disabled={starting}
              onClick={(e) => {
                e.preventDefault() // stay open until the run has started (or say why it didn't)
                void start()
              }}
            >
              {starting ? "Starting…" : "Re-score jobs"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function DeleteAll({ readOnly, onDeleted }: { readOnly: boolean; onDeleted: () => void }) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const [typed, setTyped] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const confirmed = typed.trim() === CONFIRM_WORD

  const openDialog = () => {
    setTyped("")
    setError(null)
    setOpen(true)
  }

  const run = async () => {
    if (!confirmed) return
    setBusy(true)
    setError(null)
    try {
      await api.send("POST", "/api/reset", { confirm: CONFIRM_WORD })
      setOpen(false)
      toast.success("All job data was deleted. Your settings, profile and keys are kept.")
      onDeleted()
    } catch (e) {
      setError(messageOf(e))
    } finally {
      setBusy(false)
    }
  }

  const label = `Type "${CONFIRM_WORD}" to confirm`
  return (
    <div className="flex flex-col items-start gap-2">
      <Button type="button" variant="destructive" onClick={openDialog} disabled={readOnly}>
        Delete all job data
      </Button>
      {readOnly && <p className="text-13 text-muted-foreground">Not available with the sample data.</p>}
      <AlertDialog open={open} onOpenChange={(o) => !busy && setOpen(o)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete all job data?</AlertDialogTitle>
            <AlertDialogDescription>
              Every job, score, status and run is deleted from this computer. This can't be undone. Your settings,
              profile and keys are kept, and nothing is changed in Notion, the CSV file or the webhook.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <Field id={`${id}-confirm`} label={label} error={error ?? undefined}>
            <Input
              id={`${id}-confirm`}
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void run()
              }}
              autoComplete="off"
              spellCheck={false}
              aria-invalid={error ? true : undefined}
              aria-describedby={describedBy(`${id}-confirm`, undefined, error ?? undefined)}
              className="bg-card"
            />
          </Field>
          <AlertDialogFooter>
            <AlertDialogCancel className="bg-card" disabled={busy}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={!confirmed || busy}
              onClick={(e) => {
                e.preventDefault() // stay open until it's done (or say why it wasn't)
                void run()
              }}
            >
              {busy ? "Deleting…" : "Delete all job data"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
