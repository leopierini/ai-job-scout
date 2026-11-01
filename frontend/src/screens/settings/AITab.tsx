import { useId, useRef, useState } from "react"
import { toast } from "sonner"

import { api } from "@/api/client"
import type { ConnectionResult, Keys, ProviderId, Settings } from "@/api/types"
import { describedBy, Field } from "@/components/Field"
import { ProviderPicker } from "@/components/ProviderPicker"
import { Section } from "@/components/Section"
import { TestLine } from "@/components/TestLine"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { fieldError, mergePaths } from "@/lib/form"
import { providerInfo } from "@/lib/providers"
import { messageOf } from "@/screens/setup/wizard"
import { Loaded, NumberField, SaveBar, TabBody, useTabForm, type TabProps } from "./shared"

const AI_PATHS = ["scoring.provider", "scoring.model", "scoring.batch_size", "scoring.max_jobs_per_run"] as const

interface AIData {
  settings: Settings
  keys: Keys
}

async function loadAITab(): Promise<AIData> {
  const [settings, keys] = await Promise.all([api.get<Settings>("/api/settings"), api.get<Keys>("/api/keys")])
  return { settings, keys }
}

const SAMPLE_AI_NOTE = "Sample data is scored offline; no AI is used."

export function AITab(props: TabProps) {
  if (props.state.mode === "sample") {
    return (
      <Section title="Provider" intro="The AI that reads each job and scores it against your profile.">
        <p className="text-muted-foreground">{SAMPLE_AI_NOTE}</p>
      </Section>
    )
  }
  return <Loaded load={loadAITab}>{(data) => <AIForm {...props} loaded={data.settings} keys={data.keys} />}</Loaded>
}

const signature = (provider: ProviderId, model: string) => `${provider}\n${model.trim()}`

function AIForm({ loaded, keys, readOnly, onDirtyChange, state }: TabProps & { loaded: Settings; keys: Keys }) {
  const id = useId()
  const { draft, save, root, fields } = useTabForm(loaded, onDirtyChange)
  const { scoring } = draft.value
  const info = providerInfo(scoring.provider)
  const savedKey = info.keyField ? keys[info.keyField] : null
  // The model last used with each provider, so switching back and forth loses nothing.
  const models = useRef<Partial<Record<ProviderId, string>>>({ [loaded.scoring.provider]: loaded.scoring.model })
  const [testing, setTesting] = useState(false)
  const [test, setTest] = useState<(ConnectionResult & { sig: string }) | null>(null)
  const result = test && test.sig === signature(scoring.provider, scoring.model) ? test : null
  const err = (path: string) => fieldError(fields, path)

  const pickProvider = (provider: ProviderId) => {
    models.current[scoring.provider] = scoring.model
    draft.set("scoring.provider", provider)
    draft.set("scoring.model", models.current[provider] ?? providerInfo(provider).models[0] ?? "")
  }

  const runTest = async () => {
    const sig = signature(scoring.provider, scoring.model)
    if (!scoring.model.trim()) return setTest({ sig, ok: false, message: "Choose a model first." })
    setTesting(true)
    try {
      const res = await api.send<ConnectionResult>("POST", "/api/test/ai", {
        provider: scoring.provider,
        model: scoring.model.trim(),
        api_key: null, // the saved key: keys are changed on the Keys tab
      })
      setTest({ ...res, sig })
    } catch (e) {
      setTest({ sig, ok: false, message: messageOf(e) })
    } finally {
      setTesting(false)
    }
  }

  const onSave = async () => {
    const ok = await save.run(async () => {
      const fresh = await api.get<Settings>("/api/settings")
      const edited = { ...draft.value, scoring: { ...scoring, model: scoring.model.trim() } }
      draft.reset(await api.send<Settings>("PUT", "/api/settings", mergePaths(fresh, edited, AI_PATHS)))
    })
    if (ok) toast.success("Settings saved.")
  }

  const modelError = err("scoring.model")
  const modelHint = info.models.length
    ? `Pick a suggestion or type any model id ${info.label} offers. Bigger models score better and cost more.`
    : "Type the model's name exactly as Ollama shows it."

  return (
    <>
      <TabBody readOnly={readOnly} root={root}>
        <Section title="Provider" intro="The AI that reads each job and scores it against your profile.">
          <ProviderPicker value={info.id} onChange={pickProvider} claudeCodeFound={state.claude_code_found} />
          {info.keyField && (
            <p className="text-13 text-muted-foreground">
              {savedKey?.set ? (
                <>
                  Your {info.label} API key is set{savedKey.hint ? ` (${savedKey.hint})` : ""}.{" "}
                </>
              ) : (
                <>{info.label} needs an API key, and none is saved yet. </>
              )}
              <a
                href="#/settings/keys"
                className="rounded-sm font-medium text-foreground underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
              >
                {savedKey?.set ? "Change it on the Keys tab" : "Add it on the Keys tab"}
              </a>
            </p>
          )}
        </Section>

        <Section title="Model">
          <Field id={`${id}-model`} label="Model" hint={modelHint} error={modelError}>
            <Input
              id={`${id}-model`}
              list={info.models.length ? `${id}-models` : undefined}
              value={scoring.model}
              onChange={(e) => draft.set("scoring.model", e.target.value)}
              placeholder={info.modelPlaceholder}
              autoComplete="off"
              spellCheck={false}
              aria-invalid={modelError ? true : undefined}
              aria-describedby={describedBy(`${id}-model`, modelHint, modelError)}
              className="bg-card sm:max-w-sm"
            />
            {info.models.length > 0 && (
              <datalist id={`${id}-models`}>
                {info.models.map((m) => (
                  <option key={m} value={m} />
                ))}
              </datalist>
            )}
          </Field>
          <div className="flex flex-col items-start gap-2">
            <Button type="button" variant="outline" className="bg-card" onClick={() => void runTest()} disabled={testing}>
              Test connection
            </Button>
            <TestLine busy={testing} busyText={`Sending a short test message to ${info.label}…`} result={result} />
          </div>
        </Section>

        <Section title="Cost and speed">
          <NumberField
            label="Batch size"
            hint="Jobs sent to the AI in one request, 1 to 50. Your profile is sent once per batch, so bigger batches cost a little less, but one failed answer affects more jobs."
            value={scoring.batch_size}
            onChange={(v) => draft.set("scoring.batch_size", v)}
            min={1}
            max={50}
            error={err("scoring.batch_size")}
          />
          <NumberField
            label="Most jobs to score per run"
            hint="Caps what the AI costs in one run: jobs over the limit wait for the next run. Leave it empty for no limit."
            value={scoring.max_jobs_per_run}
            onChange={(v) => draft.set("scoring.max_jobs_per_run", v)}
            min={1}
            placeholder="No limit"
            error={err("scoring.max_jobs_per_run")}
          />
        </Section>
      </TabBody>
      {!readOnly && (
        <SaveBar
          dirty={draft.dirty}
          saving={save.saving}
          error={save.error}
          onSave={() => void onSave()}
          onDiscard={() => {
            draft.reset()
            save.setError(null)
            setTest(null)
          }}
        />
      )}
    </>
  )
}
