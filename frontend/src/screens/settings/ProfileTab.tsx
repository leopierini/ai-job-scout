import { useCallback, useId } from "react"
import { toast } from "sonner"

import { api } from "@/api/client"
import type { Profile, Settings } from "@/api/types"
import { DealbreakersEditor } from "@/components/DealbreakersEditor"
import { describedBy, Field } from "@/components/Field"
import { Section } from "@/components/Section"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { DEFAULT_BANDS, fieldError, mergePaths } from "@/lib/form"
import { CleanupSection } from "./CleanupSection"
import { Loaded, NumberField, SaveBar, SwitchRow, TabBody, useTabForm, type TabProps } from "./shared"

/** The settings this tab edits; a save writes only these over freshly loaded settings. */
const PROFILE_PATHS = [
  "ui.headline",
  "scoring.dealbreakers",
  "scoring.bands",
  "scoring.extra_instructions",
  "scoring.reasoning_language",
  "scoring.min_score",
  "scoring.send_below_threshold",
  "cleanup",
] as const

interface ProfileData {
  settings: Settings
  profile: string
}

async function loadProfileTab(): Promise<ProfileData> {
  const [settings, profile] = await Promise.all([api.get<Settings>("/api/settings"), api.get<Profile>("/api/profile")])
  return { settings, profile: profile.text }
}

export function ProfileTab(props: TabProps) {
  return <Loaded load={loadProfileTab}>{(data) => <ProfileForm {...props} loaded={data} />}</Loaded>
}

function ProfileForm({ loaded, readOnly, onDirtyChange, reloadState }: TabProps & { loaded: ProfileData }) {
  const id = useId()
  const { draft, save, root, fields } = useTabForm(loaded, onDirtyChange)
  const { settings: s, profile } = draft.value
  const set = useCallback((path: string, value: unknown) => draft.set(`settings.${path}`, value), [draft])
  const err = (path: string) => fieldError(fields, path)

  const onSave = async () => {
    const ok = await save.run(async () => {
      const savedProfile =
        profile === draft.base.profile ? profile : (await api.send<Profile>("PUT", "/api/profile", { text: profile })).text
      const fresh = await api.get<Settings>("/api/settings")
      const rules = s.scoring.dealbreakers
        .map((d) => ({ text: d.text.trim(), consequence: d.consequence.trim() }))
        .filter((d) => d.text !== "")
      const edited = { ...s, scoring: { ...s.scoring, dealbreakers: rules } }
      const saved = await api.send<Settings>("PUT", "/api/settings", mergePaths(fresh, edited, PROFILE_PATHS))
      draft.reset({ settings: saved, profile: savedProfile })
    })
    if (ok) {
      toast.success("Settings saved.")
      void reloadState() // the headline is shown in the top bar
    }
  }

  const discard = () => {
    draft.reset()
    save.setError(null)
  }

  const headlineHint = "One line shown at the top of your jobs, at most 120 characters."
  const profileHint =
    "Markdown. Who you are and what you want, in your own words. Every job is scored against it, and it's sent with each batch, so a very long profile costs a little more."
  const bandsHint = "How the AI turns a fit into a number: one range per line, and what it means."
  const extraHint = "Anything else the AI should weigh, in plain words. Sent with each batch, so long text costs a little more."
  const languageHint = "The language of the short reason shown with each score, e.g. English or Deutsch."
  const headlineError = err("ui.headline")
  const profileError = err("text")
  const bandsError = err("scoring.bands")
  const extraError = err("scoring.extra_instructions")
  const languageError = err("scoring.reasoning_language")

  return (
    <>
      <TabBody readOnly={readOnly} root={root}>
        <Section title="About you" intro="What the AI reads before it scores a job.">
          <Field id={`${id}-headline`} label="Headline" hint={headlineHint} error={headlineError}>
            <Input
              id={`${id}-headline`}
              value={s.ui.headline}
              maxLength={120}
              onChange={(e) => set("ui.headline", e.target.value)}
              placeholder="e.g. Software Engineer, early career, open to the EU and UK"
              aria-invalid={headlineError ? true : undefined}
              aria-describedby={describedBy(`${id}-headline`, headlineHint, headlineError)}
              className="bg-card"
            />
          </Field>
          <Field id={`${id}-profile`} label="Profile" hint={profileHint} error={profileError}>
            <Textarea
              id={`${id}-profile`}
              value={profile}
              onChange={(e) => draft.set("profile", e.target.value)}
              aria-invalid={profileError ? true : undefined}
              aria-describedby={describedBy(`${id}-profile`, profileHint, profileError)}
              className="max-h-[32rem] min-h-64 bg-card font-mono text-13 md:text-13"
            />
          </Field>
        </Section>

        <Section title="Scoring rules" intro="How each job is judged against your profile.">
          <DealbreakersEditor
            value={s.scoring.dealbreakers}
            onChange={(rules) => set("scoring.dealbreakers", rules)}
            error={err("scoring.dealbreakers")}
          />
          <Field id={`${id}-bands`} label="Score bands" hint={bandsHint} error={bandsError}>
            <Textarea
              id={`${id}-bands`}
              value={s.scoring.bands}
              onChange={(e) => set("scoring.bands", e.target.value)}
              aria-invalid={bandsError ? true : undefined}
              aria-describedby={describedBy(`${id}-bands`, bandsHint, bandsError)}
              className="min-h-32 bg-card text-13 md:text-13"
            />
            <div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="bg-card"
                onClick={() => set("scoring.bands", DEFAULT_BANDS)}
                disabled={s.scoring.bands === DEFAULT_BANDS}
              >
                Reset to default
              </Button>
            </div>
          </Field>
          <Field id={`${id}-extra`} label="Extra instructions" hint={extraHint} error={extraError} optional>
            <Textarea
              id={`${id}-extra`}
              value={s.scoring.extra_instructions}
              onChange={(e) => set("scoring.extra_instructions", e.target.value)}
              placeholder="e.g. A company that sponsors visas is a plus."
              aria-invalid={extraError ? true : undefined}
              aria-describedby={describedBy(`${id}-extra`, extraHint, extraError)}
              className="min-h-20 bg-card"
            />
          </Field>
          <Field id={`${id}-language`} label="Reasoning language" hint={languageHint} error={languageError}>
            <Input
              id={`${id}-language`}
              value={s.scoring.reasoning_language}
              onChange={(e) => set("scoring.reasoning_language", e.target.value)}
              aria-invalid={languageError ? true : undefined}
              aria-describedby={describedBy(`${id}-language`, languageHint, languageError)}
              className="bg-card sm:max-w-xs"
            />
          </Field>
        </Section>

        <Section title="Which jobs you keep">
          <NumberField
            label="Minimum score"
            hint="Jobs scoring below this go to Below threshold instead of To review. 0 keeps every scored job."
            value={s.scoring.min_score}
            onChange={(v) => set("scoring.min_score", v)}
            min={0}
            max={100}
            error={err("scoring.min_score")}
          />
          <SwitchRow
            label="Also send jobs below the minimum to your outputs"
            hint="Notion, the CSV file and the webhook get every scored job, not only the ones you keep."
            checked={s.scoring.send_below_threshold}
            onCheckedChange={(on) => set("scoring.send_below_threshold", on)}
          />
        </Section>

        <CleanupSection cleanup={s.cleanup} set={(path, v) => set(`cleanup.${path}`, v)} fields={fields} />
      </TabBody>
      {!readOnly && (
        <SaveBar
          dirty={draft.dirty}
          saving={save.saving}
          error={save.error}
          onSave={() => void onSave()}
          onDiscard={discard}
        />
      )}
    </>
  )
}
