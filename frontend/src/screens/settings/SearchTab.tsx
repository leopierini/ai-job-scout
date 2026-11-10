import { useCallback, useId, useState } from "react"
import { ChevronRightIcon } from "lucide-react"
import { toast } from "sonner"

import { api } from "@/api/client"
import type { IndeedJobType, LinkedInSettings, PostingLanguage, Settings } from "@/api/types"
import { ChipsInput } from "@/components/ChipsInput"
import { EstimateNote } from "@/components/EstimateNote"
import { describedBy, Field } from "@/components/Field"
import { MultiSelect } from "@/components/MultiSelect"
import { RegionPicker } from "@/components/RegionPicker"
import { Section } from "@/components/Section"
import { Checkbox } from "@/components/ui/checkbox"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { useLiveEstimate } from "@/lib/estimate"
import { fieldError, mergePaths } from "@/lib/form"
import { indeedCountryName } from "@/lib/places"
import { useRegions } from "@/lib/regions"
import { JOB_TYPES, LINKEDIN_SORTS, POSTING_LANGUAGES } from "@/lib/search-options"
import { cn } from "@/lib/utils"
import { Loaded, NumberField, SaveBar, SwitchRow, TabBody, useTabForm, type TabProps } from "./shared"

const SEARCH_PATHS = ["linkedin", "indeed", "windows", "filters"] as const

/** Asks Indeed for every job type once a date window is set; see `jobscout.sources.indeed`. */
const KAIX_INDEED_ACTOR = "kaix~indeed-scraper"

const loadSettings = () => api.get<Settings>("/api/settings")

export function SearchTab(props: TabProps) {
  return <Loaded load={loadSettings}>{(data) => <SearchForm {...props} loaded={data} />}</Loaded>
}

/** A select can't hold "", LinkedIn's own default order: it's "default" in the select. */
const sortToSelect = (v: LinkedInSettings["sort_by"]) => v || "default"
const sortFromSelect = (v: string): LinkedInSettings["sort_by"] => (v === "default" ? "" : (v as LinkedInSettings["sort_by"]))

function SearchForm({ loaded, readOnly, onDirtyChange }: TabProps & { loaded: Settings }) {
  const id = useId()
  const { regions } = useRegions()
  const { draft, save, root, fields } = useTabForm(loaded, onDirtyChange)
  const s = draft.value
  const set = draft.set
  const err = (path: string) => fieldError(fields, path)
  const live = useLiveEstimate(s)

  const onSave = async () => {
    const ok = await save.run(async () => {
      const fresh = await api.get<Settings>("/api/settings")
      draft.reset(await api.send<Settings>("PUT", "/api/settings", mergePaths(fresh, s, SEARCH_PATHS)))
    })
    if (ok) toast.success("Settings saved.")
  }

  const toggleType = useCallback(
    (t: IndeedJobType, on: boolean) =>
      set("indeed.job_types", on ? [...s.indeed.job_types, t] : s.indeed.job_types.filter((x) => x !== t)),
    [set, s.indeed.job_types],
  )

  const sortError = err("linkedin.sort_by")
  const jobTypesError = err("indeed.job_types")
  const priceHint = (source: string) =>
    `What Apify charges for each ${source} job, in US dollars. Only used to estimate costs: check the actor's page on Apify if it changes.`

  return (
    <>
      <TabBody readOnly={readOnly} root={root}>
        <Section title="LinkedIn" intro="Searched through Apify, which charges for each job found.">
          <SwitchRow
            label="Search LinkedIn"
            hint="Each keyword is searched once in each region."
            checked={s.linkedin.enabled}
            onCheckedChange={(on) => set("linkedin.enabled", on)}
          />
          <ChipsInput
            label="Keywords"
            value={s.linkedin.keywords}
            onChange={(v) => set("linkedin.keywords", v)}
            placeholder="e.g. Software Engineer"
            hint="Job titles to search for. Each keyword adds one search per region."
            error={err("linkedin.keywords")}
          />
          <RegionPicker
            label="Regions"
            value={s.linkedin.regions}
            onChange={(v) => set("linkedin.regions", v)}
            hint="Where to search. Each region adds one search per keyword."
            error={err("linkedin.regions")}
          />
          <NumberField
            label="Results per keyword"
            hint="The most jobs taken from each search (one keyword in one region). Each job found is paid for."
            value={s.linkedin.cap_per_keyword}
            onChange={(v) => set("linkedin.cap_per_keyword", v)}
            min={1}
            max={1000}
            error={err("linkedin.cap_per_keyword")}
          />
          <Field id={`${id}-sort`} label="Sort results by" hint="Which jobs come first when a search finds more than the results per keyword." error={sortError}>
            <Select value={sortToSelect(s.linkedin.sort_by)} onValueChange={(v) => set("linkedin.sort_by", sortFromSelect(v))}>
              <SelectTrigger
                id={`${id}-sort`}
                className="w-56 bg-card"
                aria-invalid={sortError ? true : undefined}
                aria-describedby={describedBy(`${id}-sort`, "hint", sortError)}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {LINKEDIN_SORTS.map((o) => (
                  <SelectItem key={o.value || "default"} value={sortToSelect(o.value)}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <SwitchRow
            label="Fetch company details"
            hint="Adds what LinkedIn says about each company, which gives the AI more to judge by. Searches take a little longer."
            checked={s.linkedin.scrape_company}
            onCheckedChange={(on) => set("linkedin.scrape_company", on)}
          />
          <NumberField
            label="Price per result (USD)"
            hint={priceHint("LinkedIn")}
            value={s.linkedin.price_per_result}
            onChange={(v) => set("linkedin.price_per_result", v)}
            min={0}
            step={0.0001}
            error={err("linkedin.price_per_result")}
          />
        </Section>

        <Section title="Indeed" intro="Also searched through Apify, at its own price per result.">
          <SwitchRow
            label="Search Indeed"
            hint="Each search below is run once in each country."
            checked={s.indeed.enabled}
            onCheckedChange={(on) => set("indeed.enabled", on)}
          />
          <MultiSelect
            label="Countries"
            options={(regions?.indeed ?? s.indeed.countries).map((c) => ({ value: c, label: indeedCountryName(c) }))}
            value={s.indeed.countries}
            onChange={(v) => set("indeed.countries", v)}
            addLabel="Add a country"
            searchPlaceholder="Search countries"
            emptyText={regions ? "No country matches." : "Loading countries…"}
            hint="Each country adds one search per search below."
            error={err("indeed.countries")}
          />
          <ChipsInput
            label="Searches"
            value={s.indeed.queries}
            onChange={(v) => set("indeed.queries", v)}
            placeholder="e.g. software engineer"
            hint="Short searches, as you'd type them on Indeed."
            error={err("indeed.queries")}
          />
          <fieldset className="flex flex-col gap-3" aria-describedby={`${id}-types-hint`}>
            <legend className="mb-3 text-sm font-medium">Job types</legend>
            <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
              {JOB_TYPES.map((t) => (
                <div key={t.value} className="flex items-center gap-2">
                  <Checkbox
                    id={`${id}-type-${t.value}`}
                    checked={s.indeed.job_types.includes(t.value)}
                    onCheckedChange={(on) => toggleType(t.value, on === true)}
                    aria-invalid={jobTypesError ? true : undefined}
                  />
                  <Label htmlFor={`${id}-type-${t.value}`} className="font-normal">
                    {t.label}
                  </Label>
                </div>
              ))}
            </div>
            <p id={`${id}-types-hint`} className="text-13 text-muted-foreground">
              {s.indeed.actor === KAIX_INDEED_ACTOR
                ? "The default Indeed actor can't ask for job types together with dates, so it searches every type and the AI judges the type. These apply to other actors."
                : "Only jobs of these types are asked for. Indeed decides which type each job is."}
            </p>
            {jobTypesError && <p className="text-13 font-medium text-destructive">{jobTypesError}</p>}
          </fieldset>
          <NumberField
            label="Results per search"
            hint="The most jobs taken from each search (one search in one country). Each job found is paid for."
            value={s.indeed.max_rows_per_query}
            onChange={(v) => set("indeed.max_rows_per_query", v)}
            min={1}
            max={1000}
            error={err("indeed.max_rows_per_query")}
          />
          <NumberField
            label="Price per result (USD)"
            hint={priceHint("Indeed")}
            value={s.indeed.price_per_result}
            onChange={(v) => set("indeed.price_per_result", v)}
            min={0}
            step={0.0001}
            error={err("indeed.price_per_result")}
          />
        </Section>

        <Section
          title="Search window"
          intro="Each run looks back to the one before it, so you only pay for jobs posted since then."
        >
          <NumberField
            label="First search, in days"
            hint="How far back a search looks the first time it runs, e.g. after you add a keyword or region."
            value={s.windows.default_days}
            onChange={(v) => set("windows.default_days", v)}
            min={1}
            max={365}
            error={err("windows.default_days")}
          />
          <NumberField
            label="Overlap, in minutes"
            hint="Extra time added to each window so jobs posted around the last run aren't missed. Repeats are removed, but they're still paid for."
            value={s.windows.overlap_minutes}
            onChange={(v) => set("windows.overlap_minutes", v)}
            min={0}
            max={1440}
            error={err("windows.overlap_minutes")}
          />
        </Section>

        <Section
          title="Places to leave out"
          intro="Jobs whose location names one of these are dropped before scoring, so the AI never reads them."
        >
          <ChipsInput
            label="Excluded countries"
            value={s.filters.excluded_countries}
            onChange={(v) => set("filters.excluded_countries", v)}
            placeholder="e.g. Switzerland"
            hint="Written as the job location writes them, e.g. Switzerland."
            error={err("filters.excluded_countries")}
            optional
          />
          <ChipsInput
            label="Excluded cities"
            value={s.filters.excluded_cities}
            onChange={(v) => set("filters.excluded_cities", v)}
            placeholder="e.g. Basel"
            hint="Useful for one city in a country you otherwise search."
            error={err("filters.excluded_cities")}
            optional
          />
        </Section>

        <Section
          title="Jobs to leave out"
          intro="Also dropped before scoring, so the AI only reads jobs that could fit."
        >
          <ChipsInput
            label="Words in the title"
            value={s.filters.excluded_title_words}
            onChange={(v) => set("filters.excluded_title_words", v)}
            placeholder="e.g. Senior"
            hint="A job whose title has one of these as a whole word is dropped, e.g. Senior, Lead or Head."
            error={err("filters.excluded_title_words")}
            optional
          />
          <MultiSelect
            label="Languages you work in"
            options={POSTING_LANGUAGES}
            value={s.filters.languages}
            onChange={(v) => set("filters.languages", v as PostingLanguage[])}
            addLabel="Add a language"
            searchPlaceholder="Search languages"
            emptyText="No language matches."
            hint="A job clearly written in another language is dropped. Leave it empty to keep every language."
            error={err("filters.languages")}
          />
        </Section>

        <Actors
          linkedin={s.linkedin.actor}
          indeed={s.indeed.actor}
          onChange={(source, v) => set(`${source}.actor`, v)}
          errors={{ linkedin: err("linkedin.actor"), indeed: err("indeed.actor") }}
        />

        <Section title="Cost">
          <div role="status">
            <EstimateNote
              live={live}
              lead="A run with these settings can cost up to"
              credit={live.kind === "ready" ? live.estimate.credit_usd : null}
              sample={readOnly}
            />
          </div>
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
          }}
        />
      )}
    </>
  )
}

/** Which Apify actor runs each search: rarely changed, so folded away. */
function Actors({
  linkedin,
  indeed,
  onChange,
  errors,
}: {
  linkedin: string
  indeed: string
  onChange: (source: "linkedin" | "indeed", value: string) => void
  errors: { linkedin?: string; indeed?: string }
}) {
  const id = useId()
  // Open by itself when one of them was refused, so the highlighted field can be seen.
  const [open, setOpen] = useState(false)
  const shown = open || Boolean(errors.linkedin || errors.indeed)
  const hint = "Change it only if you use another actor that takes the same input."
  return (
    <Collapsible open={shown} onOpenChange={setOpen}>
      <CollapsibleTrigger className="inline-flex items-center gap-1 rounded-sm text-13 font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring">
        <ChevronRightIcon aria-hidden="true" className={cn("size-3.5 transition-transform", shown && "rotate-90")} />
        Apify actors
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mt-4 flex flex-col gap-5">
          {(["linkedin", "indeed"] as const).map((source) => (
            <Field
              key={source}
              id={`${id}-${source}`}
              label={source === "linkedin" ? "LinkedIn actor" : "Indeed actor"}
              hint={hint}
              error={errors[source]}
            >
              <Input
                id={`${id}-${source}`}
                value={source === "linkedin" ? linkedin : indeed}
                onChange={(e) => onChange(source, e.target.value)}
                autoComplete="off"
                spellCheck={false}
                aria-invalid={errors[source] ? true : undefined}
                aria-describedby={describedBy(`${id}-${source}`, hint, errors[source])}
                className="bg-card sm:max-w-sm"
              />
            </Field>
          ))}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}
