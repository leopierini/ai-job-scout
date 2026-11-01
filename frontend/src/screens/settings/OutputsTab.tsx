import { useId, useState } from "react"
import { DownloadIcon } from "lucide-react"
import { toast } from "sonner"

import { api } from "@/api/client"
import type { ConnectionResult, Keys, NotionConnectResult, Settings, TableColumn } from "@/api/types"
import { describedBy, Field } from "@/components/Field"
import { KeyGuide } from "@/components/KeyGuide"
import { OutputBlock } from "@/components/OutputBlock"
import { Section } from "@/components/Section"
import { TestLine } from "@/components/TestLine"
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
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { fieldError, mergePaths } from "@/lib/form"
import { TABLE_COLUMNS, TABLE_SORTS } from "@/lib/search-options"
import { cn } from "@/lib/utils"
import { defaultCsvPath, messageOf, NOTION_REPLACE_TEXT } from "@/screens/setup/wizard"
import { Loaded, SaveBar, SwitchRow, TabBody, useTabForm, type TabProps } from "./shared"

/** Settings paths this tab saves. The Notion page and database are the server's, set by Create database. */
const OUTPUT_PATHS = ["outputs.csv", "outputs.webhook", "outputs.notion.enabled", "ui.columns", "ui.default_sort"] as const

/** Score and role are what a row is: the table always shows them. */
const ALWAYS_SHOWN: TableColumn[] = ["score", "role"]

interface OutputsData {
  settings: Settings
  keys: Keys
}

/** The draft: the settings, and a Notion token typed here (saved as a key, never shown again). */
interface OutputsDraft {
  settings: Settings
  notionToken: string
}

type Busy = "notion" | "csv" | "webhook" | null
interface Result {
  sig: string
  ok: boolean
  message: string
}

async function loadOutputsTab(): Promise<OutputsData> {
  const [settings, keys] = await Promise.all([api.get<Settings>("/api/settings"), api.get<Keys>("/api/keys")])
  return { settings, keys }
}

export function OutputsTab(props: TabProps) {
  return <Loaded load={loadOutputsTab}>{(data) => <OutputsForm {...props} loaded={data} />}</Loaded>
}

/** What a Notion message was about: it only shows while the boxes still say this. */
const notionSignature = (token: string, parent: string) => `${token.trim()}\n${parent.trim()}`

function OutputsForm({ loaded, readOnly, onDirtyChange, state }: TabProps & { loaded: OutputsData }) {
  const id = useId()
  const { draft, save, root, fields } = useTabForm<OutputsDraft>({ settings: loaded.settings, notionToken: "" }, onDirtyChange)
  const [keys, setKeys] = useState(loaded.keys)
  const { settings: s, notionToken } = draft.value
  const { csv, webhook, notion } = s.outputs
  const csvDefault = defaultCsvPath(state)
  const set = (path: string, value: unknown) => draft.set(`settings.${path}`, value)
  const err = (path: string) => fieldError(fields, path)

  const [busy, setBusy] = useState<Busy>(null)
  // The page to create the database in: only Create database uses it, so it isn't part of the draft.
  const [parent, setParent] = useState(notion.parent_page_id)
  // The page the database was created in: the connection shown counts while the page box still says it. A new
  // token alone keeps the database (Save stores the key), so it doesn't count here.
  const [connectedPage, setConnectedPage] = useState<string | null>(
    notion.database_id && loaded.keys.notion_token.set ? notion.parent_page_id.trim() : null,
  )
  const [confirmReplace, setConfirmReplace] = useState(false)
  const [notionMessage, setNotionMessage] = useState<Result | null>(null)
  const [csvTest, setCsvTest] = useState<Result | null>(null)
  const [webhookTest, setWebhookTest] = useState<Result | null>(null)

  const sig = notionSignature(notionToken, parent)
  const connected = connectedPage !== null && connectedPage === parent.trim()
  const notionResult =
    notionMessage && notionMessage.sig === sig
      ? notionMessage
      : connected
        ? { ok: true, message: `Connected to your Notion database (${notion.database_id}).` }
        : null

  const connectNotion = async () => {
    const token = notionToken.trim()
    const fail = (message: string) => setNotionMessage({ sig, ok: false, message })
    if (!token && !keys.notion_token.set) return fail("Paste your Notion token first.")
    if (!parent.trim()) return fail("Paste the link to the Notion page that should hold your jobs.")
    setBusy("notion")
    let tokenSaved = false
    try {
      if (token) {
        setKeys(await api.send<Keys>("PUT", "/api/keys", { notion_token: token }))
        draft.set("notionToken", "") // saved: the box empties, and the key shows as set
        tokenSaved = true
      }
      const res = await api.send<NotionConnectResult>("POST", "/api/notion/connect", { parent_page: parent })
      // Connecting saved the database on the server: take it into the draft and keep every other unsaved edit
      // (the switch stays as it is on screen; Save turns Notion off again if that's what it shows).
      const fresh = await api.get<Settings>("/api/settings")
      draft.rebase({ ...draft.base, settings: fresh, notionToken: "" }, [
        "settings.outputs.notion.database_id",
        "settings.outputs.notion.parent_page_id",
        "notionToken",
      ])
      setConnectedPage(res.ok ? parent.trim() : null)
      setNotionMessage({ sig: notionSignature("", parent), ok: res.ok, message: res.message })
    } catch (e) {
      // A token saved before the failure has left its box: the message is about what's on screen now.
      setNotionMessage({ sig: notionSignature(tokenSaved ? "" : token, parent), ok: false, message: messageOf(e) })
    } finally {
      setBusy(null)
    }
  }

  const checkCsv = async () => {
    const path = csv.path.trim() || csvDefault
    const sigNow = csv.path.trim()
    setBusy("csv")
    try {
      const res = await api.send<ConnectionResult>("POST", "/api/test/csv", { path })
      setCsvTest({ sig: sigNow, ok: res.ok, message: res.message })
    } catch (e) {
      setCsvTest({ sig: sigNow, ok: false, message: messageOf(e) })
    } finally {
      setBusy(null)
    }
  }

  const testWebhook = async () => {
    const url = webhook.url.trim()
    if (!url) return setWebhookTest({ sig: url, ok: false, message: "Paste the webhook URL first." })
    setBusy("webhook")
    try {
      const res = await api.send<ConnectionResult>("POST", "/api/test/webhook", { url })
      setWebhookTest({ sig: url, ok: res.ok, message: res.message })
    } catch (e) {
      setWebhookTest({ sig: url, ok: false, message: messageOf(e) })
    } finally {
      setBusy(null)
    }
  }

  const onSave = async () => {
    const ok = await save.run(async () => {
      const token = notionToken.trim()
      if (token) setKeys(await api.send<Keys>("PUT", "/api/keys", { notion_token: token }))
      const fresh = await api.get<Settings>("/api/settings")
      const edited: Settings = {
        ...s,
        outputs: { ...s.outputs, csv: { ...csv, path: csv.path.trim() }, webhook: { ...webhook, url: webhook.url.trim() } },
      }
      const saved = await api.send<Settings>("PUT", "/api/settings", mergePaths(fresh, edited, OUTPUT_PATHS))
      draft.reset({ settings: saved, notionToken: "" })
    })
    if (ok) toast.success("Settings saved.")
  }

  const discard = () => {
    draft.reset()
    save.setError(null)
    setParent(draft.base.settings.outputs.notion.parent_page_id)
  }

  const toggleColumn = (c: TableColumn, on: boolean) => {
    const next = on ? [...s.ui.columns, c] : s.ui.columns.filter((x) => x !== c)
    set("ui.columns", TABLE_COLUMNS.map((t) => t.value).filter((v) => next.includes(v))) // in the table's order
  }

  const csvResult = csvTest && csvTest.sig === csv.path.trim() ? csvTest : null
  const webhookResult = webhookTest && webhookTest.sig === webhook.url.trim() ? webhookTest : null
  const csvError = err("outputs.csv")
  const webhookError = err("outputs.webhook")
  const tokenHint = "Kept in a file on this computer only. It's never shown again in full."
  const parentHint = "Open the page in Notion, choose Share, then Copy link. The database is created inside it."
  const csvHint = "Where the file is saved. Leave it empty to use the path shown in the box."
  const sortError = err("ui.default_sort")
  const tokenPlaceholder = keys.notion_token.set
    ? `Saved (${keys.notion_token.hint || "set"}). Paste a new token to replace it.`
    : "Paste your integration's secret"

  return (
    <>
      <TabBody readOnly={readOnly} root={root}>
        <section className="flex flex-col gap-1">
          <h2 className="text-base font-semibold">Where results go</h2>
          <p className="text-muted-foreground">
            Every job is always kept here, with its score and status. You can also send new jobs to these.
          </p>
        </section>

        <div className="-mt-4 flex flex-col gap-6">
          <OutputBlock
            id={`${id}-notion`}
            title="Notion"
            note="Adds each new job to a database in your Notion, and reads back the statuses you set there."
            checked={notion.enabled}
            onCheckedChange={(on) => set("outputs.notion.enabled", on)}
          >
            <div className="flex flex-col gap-3">
              <Field id={`${id}-notion-token`} label="Notion token" hint={tokenHint}>
                <Input
                  id={`${id}-notion-token`}
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                  value={notionToken}
                  onChange={(e) => draft.set("notionToken", e.target.value)}
                  placeholder={tokenPlaceholder}
                  aria-describedby={describedBy(`${id}-notion-token`, tokenHint)}
                  className="bg-card"
                />
              </Field>
              <KeyGuide provider="notion" />
            </div>
            <Field id={`${id}-notion-page`} label="Link to the Notion page for your jobs" hint={parentHint}>
              <Input
                id={`${id}-notion-page`}
                value={parent}
                onChange={(e) => setParent(e.target.value)}
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
                onClick={() => (notion.database_id ? setConfirmReplace(true) : void connectNotion())}
                disabled={busy !== null || save.saving}
              >
                Create database
              </Button>
              <TestLine busy={busy === "notion"} busyText="Creating the database in Notion…" result={notionResult} />
              {!connected && !notionResult && (
                <p className="text-13 text-muted-foreground">
                  {notion.database_id
                    ? "Create a new database to use this page."
                    : "Not connected yet: new jobs go to Notion once the database is created."}
                </p>
              )}
            </div>
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
          </OutputBlock>

          <OutputBlock
            id={`${id}-csv`}
            title="CSV file"
            note="Saves your jobs to a spreadsheet file you can open in Excel or Numbers."
            checked={csv.enabled}
            onCheckedChange={(on) => set("outputs.csv.enabled", on)}
          >
            <Field id={`${id}-csv-path`} label="File path" hint={csvHint} error={csvError}>
              <Input
                id={`${id}-csv-path`}
                value={csv.path}
                onChange={(e) => set("outputs.csv.path", e.target.value)}
                placeholder={csvDefault}
                autoComplete="off"
                spellCheck={false}
                aria-invalid={csvError ? true : undefined}
                aria-describedby={describedBy(`${id}-csv-path`, csvHint, csvError)}
                className="bg-card"
              />
            </Field>
            <SwitchRow
              label="Export after every run"
              hint="Writes the whole file again at the end of each run, so it always matches your jobs."
              checked={csv.auto_export}
              onCheckedChange={(on) => set("outputs.csv.auto_export", on)}
            />
            <div className="flex flex-col items-start gap-2">
              <div className="flex flex-wrap gap-2">
                <Button type="button" variant="outline" className="bg-card" onClick={() => void checkCsv()} disabled={busy !== null || save.saving}>
                  Check
                </Button>
                <a href="/api/export.csv" download className={cn(buttonVariants({ variant: "outline" }), "bg-card")}>
                  <DownloadIcon aria-hidden="true" />
                  Export now
                </a>
              </div>
              <TestLine busy={busy === "csv"} busyText="Checking the folder…" result={csvResult} />
            </div>
          </OutputBlock>

          <OutputBlock
            id={`${id}-webhook`}
            title="Webhook"
            note="Works with Zapier, Make and n8n: they give you a URL that receives each new job."
            checked={webhook.enabled}
            onCheckedChange={(on) => set("outputs.webhook.enabled", on)}
          >
            <Field id={`${id}-webhook-url`} label="Webhook URL" hint="Each new job is sent to it as JSON." error={webhookError}>
              <Input
                id={`${id}-webhook-url`}
                type="url"
                value={webhook.url}
                onChange={(e) => set("outputs.webhook.url", e.target.value)}
                placeholder="https://hooks.example.com/…"
                autoComplete="off"
                spellCheck={false}
                aria-invalid={webhookError ? true : undefined}
                aria-describedby={describedBy(`${id}-webhook-url`, "hint", webhookError)}
                className="bg-card"
              />
            </Field>
            <div className="flex flex-col items-start gap-2">
              <Button type="button" variant="outline" className="bg-card" onClick={() => void testWebhook()} disabled={busy !== null || save.saving}>
                Send a test job
              </Button>
              <TestLine busy={busy === "webhook"} busyText="Sending a test job…" result={webhookResult} />
            </div>
          </OutputBlock>
        </div>

        <Section title="Results table" intro="How your jobs are listed on the main screen.">
          <fieldset className="flex flex-col gap-3" aria-describedby={`${id}-cols-hint`}>
            <legend className="mb-3 text-sm font-medium">Columns</legend>
            <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
              {TABLE_COLUMNS.map((c) => {
                const always = ALWAYS_SHOWN.includes(c.value)
                return (
                  <div key={c.value} className="flex items-center gap-2">
                    <Checkbox
                      id={`${id}-col-${c.value}`}
                      checked={always || s.ui.columns.includes(c.value)}
                      onCheckedChange={(on) => toggleColumn(c.value, on === true)}
                      disabled={always}
                    />
                    <Label htmlFor={`${id}-col-${c.value}`} className="font-normal">
                      {c.label}
                    </Label>
                  </div>
                )
              })}
            </div>
            <p id={`${id}-cols-hint`} className="text-13 text-muted-foreground">
              Fit and Role are always shown. On a phone, the other columns fold into the role.
            </p>
            {err("ui.columns") && <p className="text-13 font-medium text-destructive">{err("ui.columns")}</p>}
          </fieldset>
          <Field id={`${id}-sort`} label="Sort jobs by" hint="The order the table starts in. You can still sort by a column." error={sortError}>
            <Select value={s.ui.default_sort} onValueChange={(v) => set("ui.default_sort", v)}>
              <SelectTrigger
                id={`${id}-sort`}
                className="w-56 bg-card"
                aria-invalid={sortError ? true : undefined}
                aria-describedby={describedBy(`${id}-sort`, "hint", sortError)}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TABLE_SORTS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
        </Section>
      </TabBody>
      {!readOnly && (
        <SaveBar
          dirty={draft.dirty}
          saving={save.saving}
          busy={busy !== null}
          error={save.error}
          onSave={() => void onSave()}
          onDiscard={discard}
        />
      )}
    </>
  )
}
