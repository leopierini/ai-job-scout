import { useId, useState } from "react"
import { toast } from "sonner"

import { api } from "@/api/client"
import type { ConnectionResult, Keys, KeysUpdate, ProviderId, SecretName, Settings } from "@/api/types"
import { describedBy, Field } from "@/components/Field"
import { KeyGuide } from "@/components/KeyGuide"
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
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import type { KeyGuideProvider } from "@/lib/key-guides"
import { providerInfo } from "@/lib/providers"
import { messageOf } from "@/screens/setup/wizard"
import { keyStatus } from "./labels"
import { Loaded, SaveBar, TabBody, useTabForm, type TabProps } from "./shared"

interface KeyRow {
  name: SecretName
  label: string
  /** What it's used for. */
  use: string
  /** What stops working without it (the Remove dialog says so). */
  without: string
  guide: KeyGuideProvider
  /** The AI provider it belongs to, for its test. */
  provider?: ProviderId
}

const KEY_ROWS: KeyRow[] = [
  {
    name: "apify_token",
    label: "Apify token",
    use: "Runs the searches on LinkedIn and Indeed. Apify charges for each job found.",
    without: "Searches won't run until you add a new one.",
    guide: "apify",
  },
  {
    name: "anthropic_api_key",
    label: "Anthropic API key",
    use: "Scores jobs when Anthropic is your AI. Paid per use.",
    without: "Scoring with Anthropic stops until you add a new one.",
    guide: "anthropic",
    provider: "anthropic",
  },
  {
    name: "openai_api_key",
    label: "OpenAI API key",
    use: "Scores jobs when OpenAI is your AI. Paid per use.",
    without: "Scoring with OpenAI stops until you add a new one.",
    guide: "openai",
    provider: "openai",
  },
  {
    name: "gemini_api_key",
    label: "Google Gemini API key",
    use: "Scores jobs when Google Gemini is your AI. Has a free tier with limits.",
    without: "Scoring with Gemini stops until you add a new one.",
    guide: "gemini",
    provider: "gemini",
  },
  {
    name: "openrouter_api_key",
    label: "OpenRouter API key",
    use: "Scores jobs when OpenRouter is your AI. Paid per use.",
    without: "Scoring with OpenRouter stops until you add a new one.",
    guide: "openrouter",
    provider: "openrouter",
  },
  {
    name: "notion_token",
    label: "Notion token",
    use: "Adds new jobs to your Notion database and reads back the statuses you set there.",
    without: "Jobs stop going to Notion until you add a new one.",
    guide: "notion",
  },
]

type Typed = Record<SecretName, string>
const BLANK = Object.fromEntries(KEY_ROWS.map((r) => [r.name, ""])) as Typed

interface KeysData {
  keys: Keys
  settings: Settings
}

async function loadKeysTab(): Promise<KeysData> {
  const [keys, settings] = await Promise.all([api.get<Keys>("/api/keys"), api.get<Settings>("/api/settings")])
  return { keys, settings }
}

export function KeysTab(props: TabProps) {
  return <Loaded load={loadKeysTab}>{(data) => <KeysForm {...props} loaded={data} />}</Loaded>
}

function KeysForm({ loaded, readOnly, onDirtyChange }: TabProps & { loaded: KeysData }) {
  const { draft, save, root } = useTabForm<Typed>(BLANK, onDirtyChange)
  const [keys, setKeys] = useState(loaded.keys)
  const [open, setOpen] = useState<Set<SecretName>>(new Set())
  const [removing, setRemoving] = useState<KeyRow | null>(null)
  const [removeBusy, setRemoveBusy] = useState(false)
  const [testing, setTesting] = useState<SecretName | null>(null)
  const [tests, setTests] = useState<Partial<Record<SecretName, ConnectionResult & { sig: string }>>>({})
  const { scoring } = loaded.settings

  const toggle = (name: SecretName, on: boolean) => {
    setOpen((o) => {
      const next = new Set(o)
      if (on) next.add(name)
      else next.delete(name)
      return next
    })
    if (!on) draft.set(name, "")
  }

  const test = async (row: KeyRow) => {
    const typed = draft.value[row.name].trim()
    const sig = typed
    setTesting(row.name)
    try {
      let res: ConnectionResult
      if (row.name === "apify_token") {
        res = await api.send<ConnectionResult>("POST", "/api/test/apify", { token: typed || null })
      } else {
        const provider = row.provider!
        // The model saved for this provider, or the first one it suggests.
        const model = scoring.provider === provider ? scoring.model : (providerInfo(provider).models[0] ?? "")
        res = await api.send<ConnectionResult>("POST", "/api/test/ai", { provider, model, api_key: typed || null })
      }
      setTests((t) => ({ ...t, [row.name]: { ...res, sig } }))
    } catch (e) {
      setTests((t) => ({ ...t, [row.name]: { ok: false, message: messageOf(e), sig } }))
    } finally {
      setTesting(null)
    }
  }

  const onSave = async () => {
    const ok = await save.run(async () => {
      const update: KeysUpdate = {}
      for (const row of KEY_ROWS) {
        const v = draft.value[row.name].trim()
        if (v) update[row.name] = v
      }
      setKeys(await api.send<Keys>("PUT", "/api/keys", update))
      draft.reset()
      setOpen(new Set())
      setTests({})
    })
    if (ok) toast.success("Keys saved.")
  }

  const remove = async () => {
    if (!removing) return
    setRemoveBusy(true)
    try {
      setKeys(await api.send<Keys>("PUT", "/api/keys", { [removing.name]: null }))
      toast.success(`${removing.label} removed.`)
      setTests((t) => ({ ...t, [removing.name]: undefined }))
      setRemoving(null)
    } catch (e) {
      toast.error(messageOf(e))
    } finally {
      setRemoveBusy(false)
    }
  }

  return (
    <>
      <TabBody readOnly={readOnly} root={root}>
        <section className="flex flex-col gap-1">
          <h2 className="text-base font-semibold">Keys</h2>
          <p className="text-muted-foreground">
            Kept in a file on this computer only. A saved key is never shown again: only its last 4 characters.
          </p>
        </section>
        <ul className="-mt-4 flex flex-col">
          {KEY_ROWS.map((row) => (
            <KeyRowItem
              key={row.name}
              row={row}
              status={keys[row.name]}
              open={open.has(row.name)}
              onOpenChange={(on) => toggle(row.name, on)}
              value={draft.value[row.name]}
              onValueChange={(v) => draft.set(row.name, v)}
              canTest={row.name === "apify_token" || row.provider !== undefined}
              testing={testing === row.name}
              anyTesting={testing !== null}
              onTest={() => void test(row)}
              result={(() => {
                const t = tests[row.name]
                return t && t.sig === draft.value[row.name].trim() ? t : null
              })()}
              onRemove={() => setRemoving(row)}
            />
          ))}
        </ul>
      </TabBody>
      {!readOnly && (
        <SaveBar
          dirty={draft.dirty}
          saving={save.saving}
          busy={testing !== null || removeBusy}
          error={save.error}
          onSave={() => void onSave()}
          onDiscard={() => {
            draft.reset()
            save.setError(null)
            setOpen(new Set())
          }}
        />
      )}

      <AlertDialog open={removing !== null} onOpenChange={(o) => !o && !removeBusy && setRemoving(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove the {removing?.label}?</AlertDialogTitle>
            <AlertDialogDescription>
              It's deleted from this computer. {removing?.without}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="bg-card" disabled={removeBusy}>
              Keep it
            </AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={removeBusy}
              onClick={(e) => {
                e.preventDefault() // stay open until the key is really removed
                void remove()
              }}
            >
              {removeBusy ? "Removing…" : "Remove"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

interface KeyRowItemProps {
  row: KeyRow
  status: Keys[SecretName]
  open: boolean
  onOpenChange: (open: boolean) => void
  value: string
  onValueChange: (v: string) => void
  canTest: boolean
  testing: boolean
  anyTesting: boolean
  onTest: () => void
  result: ConnectionResult | null
  onRemove: () => void
}

function KeyRowItem({
  row,
  status,
  open,
  onOpenChange,
  value,
  onValueChange,
  canTest,
  testing,
  anyTesting,
  onTest,
  result,
  onRemove,
}: KeyRowItemProps) {
  const id = useId()
  const hint = "Paste the new key, then Save changes. Test checks it before you save."
  return (
    <li className="flex flex-col gap-3 border-t py-5 first:border-t-0">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-col gap-0.5">
          <h3 className="font-medium">{row.label}</h3>
          <p className="text-13 text-muted-foreground">{row.use}</p>
          <p className="text-13">
            <span className={status.set ? "font-medium" : "text-muted-foreground"}>{keyStatus(status)}</span>
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {canTest && (status.set || value.trim()) && (
            <Button type="button" variant="outline" size="sm" className="bg-card" onClick={onTest} disabled={anyTesting}>
              Test
            </Button>
          )}
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="bg-card"
            onClick={() => onOpenChange(!open)}
            aria-expanded={open}
            aria-controls={open ? `${id}-replace` : undefined}
          >
            {open ? "Cancel" : status.set ? "Replace" : "Add"}
          </Button>
          {status.set && (
            <Button type="button" variant="ghost" size="sm" onClick={onRemove}>
              Remove
            </Button>
          )}
        </div>
      </div>
      {open && (
        <div id={`${id}-replace`} className="flex flex-col gap-3">
          <Field id={`${id}-input`} label={status.set ? `New ${row.label}` : row.label} hint={hint}>
            <Input
              id={`${id}-input`}
              type="password"
              autoComplete="off"
              spellCheck={false}
              autoFocus
              value={value}
              onChange={(e) => onValueChange(e.target.value)}
              placeholder="Paste the key"
              aria-describedby={describedBy(`${id}-input`, hint)}
              className="bg-card sm:max-w-md"
            />
          </Field>
          <KeyGuide provider={row.guide} />
        </div>
      )}
      <TestLine busy={testing} busyText="Testing…" result={result} />
    </li>
  )
}
