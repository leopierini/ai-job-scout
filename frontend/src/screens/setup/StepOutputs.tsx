import { useId, useState } from "react"

import { api } from "@/api/client"
import type { ConnectionResult, Keys, NotionConnectResult } from "@/api/types"
import { describedBy, Field } from "@/components/Field"
import { KeyGuide } from "@/components/KeyGuide"
import { OutputBlock } from "@/components/OutputBlock"
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
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { StepFooter, TestLine, type StepProps } from "./parts"
import { useSave } from "./useSave"
import {
  applyOutputs,
  csvSignature,
  fieldMessage,
  messageOf,
  NOTION_REPLACE_TEXT,
  notionConnected,
  notionSignature,
  notReadyOutputs,
  notReadyQuestion,
  webhookSignature,
  type OutputName,
  type OutputsState,
} from "./wizard"

interface StepOutputsProps extends StepProps {
  outputs: OutputsState
  /** Applied to the latest state, so a test result arriving late doesn't undo typing. */
  onChange: (patch: (o: OutputsState) => OutputsState) => void
  keys: Keys
  csvDefault: string
}

export function StepOutputs({ outputs, onChange, keys, csvDefault, ...nav }: StepOutputsProps) {
  const id = useId()
  const [busy, setBusy] = useState<OutputName | null>(null)
  const [confirm, setConfirm] = useState<OutputName[] | null>(null)
  const [confirmReplace, setConfirmReplace] = useState(false)
  const save = useSave()
  const { notion, csv, webhook } = outputs
  const fields = save.error?.fields ?? null

  const set = <K extends OutputName>(name: K, patch: Partial<OutputsState[K]>) =>
    onChange((o) => ({ ...o, [name]: { ...o[name], ...patch } }))

  const connectNotion = async () => {
    const token = notion.token.trim()
    const parent = notion.parent
    let sig = notionSignature(token, parent)
    const fail = (message: string) => set("notion", { message: { sig, ok: false, message } })
    if (!token && !keys.notion_token.set) return fail("Paste your Notion token first.")
    if (!parent.trim()) return fail("Paste the link to the Notion page that should hold your jobs.")
    setBusy("notion")
    try {
      if (token) {
        await nav.saveKeys({ notion_token: token })
        sig = notionSignature("", parent) // saved: the box empties, and the key shows as set
        set("notion", { token: "" })
      }
      const res = await api.send<NotionConnectResult>("POST", "/api/notion/connect", { parent_page: parent })
      set("notion", {
        connectedSig: res.ok ? sig : null,
        hasDatabase: true,
        message: { sig, ok: res.ok, message: res.message },
      })
    } catch (err) {
      fail(messageOf(err))
    } finally {
      setBusy(null)
    }
  }
  const notionSig = notionSignature(notion.token, notion.parent)
  const notionResult =
    notion.message && notion.message.sig === notionSig
      ? notion.message
      : notionConnected(notion)
        ? { ok: true, message: "Connected to your Notion database." }
        : null

  const checkCsv = async () => {
    const path = csv.path.trim() || csvDefault
    setBusy("csv")
    try {
      const res = await api.send<ConnectionResult>("POST", "/api/test/csv", { path })
      const filled = csv.path.trim() ? {} : { path } // an empty box was checked as the default path: show it
      set("csv", { ...filled, test: { sig: csvSignature(path), ok: res.ok, message: res.message } })
    } catch (err) {
      set("csv", { test: { sig: csvSignature(path), ok: false, message: messageOf(err) } })
    } finally {
      setBusy(null)
    }
  }

  const testWebhook = async () => {
    const sig = webhookSignature(webhook.url)
    if (!sig) return set("webhook", { test: { sig, ok: false, message: "Paste the webhook URL first." } })
    setBusy("webhook")
    try {
      const res = await api.send<ConnectionResult>("POST", "/api/test/webhook", { url: sig })
      set("webhook", { test: { sig, ok: res.ok, message: res.message } })
    } catch (err) {
      set("webhook", { test: { sig, ok: false, message: messageOf(err) } })
    } finally {
      setBusy(null)
    }
  }

  const saveAndGo = async (o: OutputsState) => {
    const ok = await save.run(async () => {
      const token = o.notion.token.trim()
      if (token) await nav.saveKeys({ notion_token: token })
      await nav.saveSettings((s) => applyOutputs(s, o, csvDefault))
    })
    if (ok) nav.next()
  }

  const next = () => {
    const pending = notReadyOutputs(outputs)
    if (pending.length) setConfirm(pending)
    else void saveAndGo(outputs)
  }

  const continueWithout = () => {
    if (!confirm) return
    const keep = (name: OutputName) => outputs[name].enabled && !confirm.includes(name)
    const off: OutputsState = {
      notion: { ...outputs.notion, enabled: keep("notion") },
      csv: { ...outputs.csv, enabled: keep("csv") },
      webhook: { ...outputs.webhook, enabled: keep("webhook") },
    }
    setConfirm(null)
    onChange(() => off)
    void saveAndGo(off)
  }

  const csvResult = csv.test && csv.test.sig === csvSignature(csv.path) ? csv.test : null
  const webhookResult = webhook.test && webhook.test.sig === webhookSignature(webhook.url) ? webhook.test : null
  const csvError = fieldMessage(fields, "outputs.csv")
  const webhookError = fieldMessage(fields, "outputs.webhook")
  const notionTokenHint = "Kept in a file on this computer only."
  const parentHint = "Open the page in Notion, choose Share, then Copy link."

  return (
    <>
      <div className="flex flex-col gap-6">
        <div className="flex items-center gap-3">
          <Checkbox id={`${id}-app`} checked disabled aria-describedby={`${id}-app-note`} />
          <div className="flex flex-col gap-0.5">
            <Label htmlFor={`${id}-app`} className="text-base font-semibold peer-disabled:opacity-100">
              AI Job Scout (always on)
            </Label>
            <p id={`${id}-app-note`} className="text-13 text-muted-foreground">
              Every job is kept here, with its score and your notes.
            </p>
          </div>
        </div>

        <OutputBlock
          id={`${id}-notion`}
          title="Notion"
          note="Adds each new job to a database in your Notion."
          checked={notion.enabled}
          onCheckedChange={(enabled) => set("notion", { enabled })}
        >
          <div className="flex flex-col gap-3">
            <Field id={`${id}-notion-token`} label="Notion token" hint={notionTokenHint}>
              <Input
                id={`${id}-notion-token`}
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={notion.token}
                onChange={(e) => set("notion", { token: e.target.value })}
                placeholder={
                  keys.notion_token.set
                    ? `Saved (${keys.notion_token.hint || "set"}). Paste a new token to replace it.`
                    : "Paste your integration's secret"
                }
                aria-describedby={describedBy(`${id}-notion-token`, notionTokenHint)}
                className="bg-card"
              />
            </Field>
            <KeyGuide provider="notion" />
          </div>
          <Field id={`${id}-notion-page`} label="Link to the Notion page for your jobs" hint={parentHint}>
            <Input
              id={`${id}-notion-page`}
              value={notion.parent}
              onChange={(e) => set("notion", { parent: e.target.value })}
              placeholder="https://www.notion.so/…"
              autoComplete="off"
              spellCheck={false}
              aria-describedby={describedBy(`${id}-notion-page`, parentHint)}
              className="bg-card"
            />
          </Field>
          <div className="flex flex-col items-start gap-2">
            <Button
              type="button"
              variant="outline"
              className="bg-card"
              onClick={() => (notion.hasDatabase ? setConfirmReplace(true) : void connectNotion())}
              disabled={busy !== null || save.saving}
            >
              Create database
            </Button>
            <TestLine busy={busy === "notion"} busyText="Creating the database in Notion…" result={notionResult} />
          </div>
        </OutputBlock>

        <OutputBlock
          id={`${id}-csv`}
          title="CSV file"
          note="Saves your jobs to a spreadsheet file you can open in Excel or Numbers."
          checked={csv.enabled}
          onCheckedChange={(enabled) => set("csv", { enabled })}
        >
          <Field id={`${id}-csv-path`} label="File path" error={csvError}>
            <Input
              id={`${id}-csv-path`}
              value={csv.path}
              onChange={(e) => set("csv", { path: e.target.value })}
              placeholder={csvDefault}
              autoComplete="off"
              spellCheck={false}
              aria-invalid={csvError ? true : undefined}
              aria-describedby={describedBy(`${id}-csv-path`, undefined, csvError)}
              className="bg-card"
            />
          </Field>
          <div className="flex items-center gap-3">
            <Switch
              id={`${id}-csv-auto`}
              checked={csv.autoExport}
              onCheckedChange={(autoExport) => set("csv", { autoExport })}
            />
            <Label htmlFor={`${id}-csv-auto`}>Export after every run</Label>
          </div>
          <div className="flex flex-col items-start gap-2">
            <Button type="button" variant="outline" className="bg-card" onClick={checkCsv} disabled={busy !== null || save.saving}>
              Check
            </Button>
            <TestLine busy={busy === "csv"} busyText="Checking the folder…" result={csvResult} />
          </div>
        </OutputBlock>

        <OutputBlock
          id={`${id}-webhook`}
          title="Webhook"
          note="Works with Zapier, Make and n8n: they give you a URL that receives each new job."
          checked={webhook.enabled}
          onCheckedChange={(enabled) => set("webhook", { enabled })}
        >
          <Field id={`${id}-webhook-url`} label="Webhook URL" error={webhookError}>
            <Input
              id={`${id}-webhook-url`}
              type="url"
              value={webhook.url}
              onChange={(e) => set("webhook", { url: e.target.value })}
              placeholder="https://hooks.example.com/…"
              autoComplete="off"
              spellCheck={false}
              aria-invalid={webhookError ? true : undefined}
              aria-describedby={describedBy(`${id}-webhook-url`, undefined, webhookError)}
              className="bg-card"
            />
          </Field>
          <div className="flex flex-col items-start gap-2">
            <Button type="button" variant="outline" className="bg-card" onClick={testWebhook} disabled={busy !== null || save.saving}>
              Send a test job
            </Button>
            <TestLine busy={busy === "webhook"} busyText="Sending a test job…" result={webhookResult} />
          </div>
        </OutputBlock>
      </div>

      <StepFooter
        {...nav}
        onNext={next}
        nextDisabled={busy !== null || save.saving}
        saving={save.saving}
        error={save.error?.message ?? null}
      />

      <AlertDialog open={confirmReplace} onOpenChange={setConfirmReplace}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Create a new Notion database?</AlertDialogTitle>
            <AlertDialogDescription>{NOTION_REPLACE_TEXT}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="bg-card">Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => void connectNotion()}>Create new database</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={confirm !== null} onOpenChange={(open) => !open && setConfirm(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{confirm ? notReadyQuestion(confirm) : ""}</AlertDialogTitle>
            <AlertDialogDescription>
              {confirm && confirm.length > 1
                ? "They'll be turned off. You can set them up later in Settings."
                : "It'll be turned off. You can set it up later in Settings."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="bg-card">Stay here</AlertDialogCancel>
            <AlertDialogAction onClick={continueWithout}>
              {confirm && confirm.length > 1 ? "Continue without them" : "Continue without it"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
