import { useId, useState } from "react"

import { api } from "@/api/client"
import type { ConnectionResult, Keys } from "@/api/types"
import { describedBy, Field } from "@/components/Field"
import { KeyGuide } from "@/components/KeyGuide"
import { ProviderPicker } from "@/components/ProviderPicker"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { providerInfo } from "@/lib/providers"
import { Section, StepFooter, TestLine, type StepProps } from "./parts"
import { useSave } from "./useSave"
import { aiReady, aiSignature, applyAi, fieldMessage, messageOf, modelOf, typedKey, type AiState } from "./wizard"

interface StepAIProps extends StepProps {
  ai: AiState
  /** Merged into the latest state, so a test result arriving late doesn't undo typing. */
  onChange: (patch: Partial<AiState>) => void
  keys: Keys
  claudeCodeFound: boolean
}

export function StepAI({ ai, onChange, keys, claudeCodeFound, ...nav }: StepAIProps) {
  const id = useId()
  const info = providerInfo(ai.provider)
  const model = ai.models[ai.provider] ?? ""
  const keyValue = info.keyField ? (ai.keys[info.keyField] ?? "") : ""
  const savedKey = info.keyField ? keys[info.keyField] : null
  const [testing, setTesting] = useState(false)
  const save = useSave()

  // A result is shown only while it describes what is on screen.
  const result = ai.test && ai.test.sig === aiSignature(ai) ? ai.test : null
  const ready = aiReady(ai)

  const test = async () => {
    const sig = aiSignature(ai)
    const fail = (message: string) => onChange({ test: { sig, ok: false, message } })
    if (!modelOf(ai)) return fail("Choose a model first.")
    if (info.needsKey && !typedKey(ai) && !savedKey?.set) return fail(`Paste your ${info.label} API key first.`)
    setTesting(true)
    try {
      const res = await api.send<ConnectionResult>("POST", "/api/test/ai", {
        provider: ai.provider,
        model: modelOf(ai),
        api_key: typedKey(ai) || null,
      })
      onChange({ test: { sig, ok: res.ok, message: res.message } })
    } catch (err) {
      fail(messageOf(err))
    } finally {
      setTesting(false)
    }
  }

  const next = async () => {
    const ok = await save.run(async () => {
      const key = typedKey(ai)
      if (info.keyField && key) await nav.saveKeys({ [info.keyField]: key })
      await nav.saveSettings((s) => applyAi(s, ai))
    })
    if (ok) nav.next()
  }

  const fields = save.error?.fields ?? null
  const modelError = fieldMessage(fields, "scoring.model")
  const modelHint = info.models.length
    ? `Pick a suggestion or type any model id ${info.label} offers.`
    : "Type the model's name exactly as Ollama shows it."
  const keyHint = "Kept in a file on this computer only. It's never shown again in full."

  return (
    <>
      <div className="flex flex-col gap-8">
        <Section title="Provider">
          <ProviderPicker
            value={ai.provider}
            onChange={(provider) => onChange({ provider })}
            claudeCodeFound={claudeCodeFound}
          />
        </Section>

        <Section title={`Connect ${info.label}`}>
          {info.keyField && info.guide && (
            <div className="flex flex-col gap-3">
              <Field id={`${id}-key`} label={`${info.label} API key`} hint={keyHint}>
                <Input
                  id={`${id}-key`}
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                  value={keyValue}
                  onChange={(e) => onChange({ keys: { ...ai.keys, [info.keyField!]: e.target.value } })}
                  placeholder={savedKey?.set ? `Saved (${savedKey.hint || "set"}). Paste a new key to replace it.` : "Paste your key"}
                  aria-describedby={describedBy(`${id}-key`, keyHint)}
                  className="bg-card"
                />
              </Field>
              <KeyGuide provider={info.guide} />
            </div>
          )}
          <Field id={`${id}-model`} label="Model" hint={modelHint} error={modelError}>
            <Input
              id={`${id}-model`}
              list={info.models.length ? `${id}-models` : undefined}
              value={model}
              onChange={(e) => onChange({ models: { ...ai.models, [ai.provider]: e.target.value } })}
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
            <Button type="button" variant="outline" className="bg-card" onClick={test} disabled={testing}>
              Test connection
            </Button>
            <TestLine
              busy={testing}
              busyText={`Sending a short test message to ${info.label}…`}
              result={result}
            />
          </div>
        </Section>
      </div>
      <StepFooter
        {...nav}
        onNext={next}
        nextDisabled={!ready}
        saving={save.saving}
        note={ready ? undefined : "Test the connection to continue."}
        error={save.error?.message ?? null}
      />
    </>
  )
}
