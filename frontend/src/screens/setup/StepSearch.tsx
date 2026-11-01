import { useId, useState } from "react"

import { api } from "@/api/client"
import type { ConnectionResult, IndeedJobType, Keys, Settings } from "@/api/types"
import { ChipsInput } from "@/components/ChipsInput"
import { EstimateNote } from "@/components/EstimateNote"
import { describedBy, Field } from "@/components/Field"
import { KeyGuide } from "@/components/KeyGuide"
import { MultiSelect } from "@/components/MultiSelect"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { money } from "@/lib/format"
import { useLiveEstimate } from "@/lib/estimate"
import { indeedCountryName } from "@/lib/places"
import { useRegions } from "@/lib/regions"
import { JOB_TYPES } from "@/lib/search-options"
import { Section, StepFooter, TestLine, type StepProps } from "./parts"
import { useSave } from "./useSave"
import {
  NEED_REGION,
  apifyResult,
  applySearch,
  canLeaveSearch,
  fieldMessage,
  messageOf,
  type SearchState,
} from "./wizard"

interface StepSearchProps extends StepProps {
  search: SearchState
  onChange: (patch: Partial<SearchState>) => void
  keys: Keys
  /** Go to the You step, where the regions are chosen. */
  toRegions: () => void
  /** The settings as last saved: the estimate applies this step's unsaved choices on top. */
  saved: Settings
}

export function StepSearch({ search, onChange, keys, saved, toRegions, ...nav }: StepSearchProps) {
  const id = useId()
  const { regions } = useRegions()
  const [testing, setTesting] = useState(false)
  // After the first Next, the checks run on every change, so a fixed field stops showing its error.
  const [tried, setTried] = useState(false)
  const save = useSave()
  const live = useLiveEstimate(applySearch(saved, search))
  const estimate = live.kind === "ready" ? live.estimate : null

  const token = search.apifyToken.trim()
  const result = apifyResult(search)
  const gate = canLeaveSearch({ search, regions: saved.linkedin.regions, tokenSaved: keys.apify_token.set })
  const credit = result?.ok ? result.credit : (estimate?.credit_usd ?? null)

  const test = async () => {
    setTesting(true)
    try {
      const res = await api.send<ConnectionResult>("POST", "/api/test/apify", { token: token || null })
      onChange({ apifyTest: { sig: token, ok: res.ok, message: res.message, credit: res.credit_usd ?? null } })
    } catch (e) {
      onChange({ apifyTest: { sig: token, ok: false, message: messageOf(e), credit: null } })
    } finally {
      setTesting(false)
    }
  }

  const validate = (): Record<string, string> => {
    // The token, keywords and regions are the gate's (Next stays disabled until they're in place).
    const out: Record<string, string> = {}
    if (!search.linkedin && !search.indeed) out.sources = "Turn on LinkedIn or Indeed, or both."
    if (search.indeed && search.indeedCountries.length === 0) out.countries = "Choose at least one country."
    if (search.indeed && search.indeedQueries.length === 0) out.queries = "Add at least one search for Indeed."
    if (search.indeed && search.indeedJobTypes.length === 0) out.jobTypes = "Choose at least one job type."
    return out
  }

  const errors = tried ? validate() : {}
  const fields = save.error?.fields ?? null
  const err = (key: string, path: string) => errors[key] ?? fieldMessage(fields, path)

  const next = async () => {
    if (gate) return
    setTried(true)
    if (Object.keys(validate()).length) return
    const ok = await save.run(async () => {
      if (token) await nav.saveKeys({ apify_token: token })
      await nav.saveSettings((s) => applySearch(s, search))
    })
    if (ok) nav.next()
  }

  const tokenHint = "Kept in a file on this computer only."
  const toggleType = (t: IndeedJobType, on: boolean) =>
    onChange({ indeedJobTypes: on ? [...search.indeedJobTypes, t] : search.indeedJobTypes.filter((x) => x !== t) })
  const firstError = Object.values(errors)[0]

  return (
    <>
      <div className="flex flex-col gap-8">
        <Section title="Apify" intro="Apify runs the searches on LinkedIn and Indeed, and charges a small amount for each job found.">
          <div className="flex flex-col gap-3">
            <Field id={`${id}-token`} label="Apify token" hint={tokenHint} error={err("token", "apify_token")}>
              <Input
                id={`${id}-token`}
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={search.apifyToken}
                onChange={(e) => onChange({ apifyToken: e.target.value })}
                placeholder={
                  keys.apify_token.set
                    ? `Saved (${keys.apify_token.hint || "set"}). Paste a new token to replace it.`
                    : "Paste your token"
                }
                aria-invalid={errors.token ? true : undefined}
                aria-describedby={describedBy(`${id}-token`, tokenHint, errors.token)}
                className="bg-card"
              />
            </Field>
            <KeyGuide provider="apify" />
          </div>
          <div className="flex flex-col items-start gap-2">
            <Button type="button" variant="outline" className="bg-card" onClick={test} disabled={testing}>
              Test
            </Button>
            <TestLine busy={testing} busyText="Checking the token with Apify…" result={result} />
            {result?.ok && result.credit !== null && (
              <p className="text-13 text-muted-foreground">
                Credit left this month <span className="num text-foreground">{money(result.credit)}</span>
              </p>
            )}
          </div>
        </Section>

        <Section title="What to search">
          <ChipsInput
            label="Keywords"
            value={search.keywords}
            onChange={(keywords) => onChange({ keywords })}
            placeholder="e.g. Software Engineer"
            hint="Job titles to search for. Each keyword is searched in each region."
            error={err("keywords", "linkedin.keywords")}
          />
          <div className="flex items-center gap-3">
            <Switch
              id={`${id}-linkedin`}
              checked={search.linkedin}
              onCheckedChange={(linkedin) => onChange({ linkedin })}
            />
            <Label htmlFor={`${id}-linkedin`}>Search LinkedIn</Label>
          </div>
          <div className="grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-1">
            <Switch
              id={`${id}-indeed`}
              checked={search.indeed}
              onCheckedChange={(indeed) => onChange({ indeed })}
              aria-describedby={`${id}-indeed-note`}
            />
            <Label htmlFor={`${id}-indeed`}>Search Indeed</Label>
            <p id={`${id}-indeed-note`} className="col-start-2 text-13 text-muted-foreground">
              Indeed costs about 2.5× more per result.
            </p>
          </div>
          {errors.sources && <p className="text-13 font-medium text-destructive">{errors.sources}</p>}

          {search.indeed && (
            <div className="flex flex-col gap-5 rounded-lg border bg-card p-4">
              <MultiSelect
                label="Indeed countries"
                options={(regions?.indeed ?? search.indeedCountries).map((c) => ({ value: c, label: indeedCountryName(c) }))}
                value={search.indeedCountries}
                onChange={(indeedCountries) => onChange({ indeedCountries })}
                addLabel="Add a country"
                searchPlaceholder="Search countries"
                emptyText={regions ? "No country matches." : "Loading countries…"}
                error={err("countries", "indeed.countries")}
              />
              <ChipsInput
                label="Indeed searches"
                value={search.indeedQueries}
                onChange={(indeedQueries) => onChange({ indeedQueries })}
                placeholder="e.g. software engineer"
                hint="Short searches, as you'd type them on Indeed."
                error={err("queries", "indeed.queries")}
              />
              <fieldset className="flex flex-col gap-3">
                <legend className="mb-3 text-sm font-medium">Job types</legend>
                <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
                  {JOB_TYPES.map((t) => (
                    <div key={t.value} className="flex items-center gap-2">
                      <Checkbox
                        id={`${id}-type-${t.value}`}
                        checked={search.indeedJobTypes.includes(t.value)}
                        onCheckedChange={(on) => toggleType(t.value, on === true)}
                      />
                      <Label htmlFor={`${id}-type-${t.value}`} className="font-normal">
                        {t.label}
                      </Label>
                    </div>
                  ))}
                </div>
                {err("jobTypes", "indeed.job_types") && (
                  <p className="text-13 font-medium text-destructive">{err("jobTypes", "indeed.job_types")}</p>
                )}
              </fieldset>
            </div>
          )}
        </Section>

        <Section title="Cost">
          <div role="status">
            <EstimateNote live={live} lead="Your first run can cost up to" credit={credit} />
          </div>
        </Section>
      </div>
      <StepFooter
        {...nav}
        onNext={next}
        nextDisabled={gate !== null}
        saving={save.saving}
        note={
          gate === NEED_REGION ? (
            <>
              {gate}:{" "}
              <button
                type="button"
                onClick={toRegions}
                className="rounded-sm font-medium text-foreground underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
              >
                add regions on the You step
              </button>
            </>
          ) : (
            (gate ?? (firstError ? "Fix the fields marked above to continue." : undefined))
          )
        }
        error={save.error?.message ?? null}
      />
    </>
  )
}
