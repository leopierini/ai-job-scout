import { useEffect, useId, useState } from "react"
import { ChevronRightIcon } from "lucide-react"
import { toast } from "sonner"

import { api } from "@/api/client"
import type { ApifyBoardName, ApifyBoardSettings, Settings, StartupPlace, VcBoardPreset } from "@/api/types"
import { ChipsInput } from "@/components/ChipsInput"
import { EstimateNote } from "@/components/EstimateNote"
import { describedBy, Field } from "@/components/Field"
import { MultiSelect } from "@/components/MultiSelect"
import { Section } from "@/components/Section"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Input } from "@/components/ui/input"
import { useLiveEstimate } from "@/lib/estimate"
import { fieldError, mergePaths } from "@/lib/form"
import { cn } from "@/lib/utils"
import { Loaded, NumberField, SaveBar, SwitchRow, TabBody, useTabForm, type TabProps } from "./shared"

const STARTUP_PATHS = [
  "startup_queries",
  "startup_places",
  "builtineurope",
  "jobgether",
  "vcboards",
  "careers",
  "wttj",
  "wellfound",
  "yc",
  "atsindex",
] as const

const loadSettings = () => api.get<Settings>("/api/settings")

export function StartupsTab(props: TabProps) {
  return <Loaded load={loadSettings}>{(data) => <StartupsForm {...props} loaded={data} />}</Loaded>
}

/** A list bundled with the app, loaded once; an empty list if it can't be loaded (typed values still work). */
function useBundled<T>(path: string): T[] | null {
  const [items, setItems] = useState<T[] | null>(null)
  useEffect(() => {
    let live = true
    api.get<T[]>(path).then(
      (p) => live && setItems(p),
      () => live && setItems([]),
    )
    return () => {
      live = false
    }
  }, [path])
  return items
}

const OWN_SEARCHES = "Leave it empty to use the searches at the top."
const OWN_PLACES = "Leave it empty to use the places at the top."

const lower = (values: string[]) => [...new Set(values.map((v) => v.trim().toLowerCase()).filter(Boolean))]

/** "united kingdom" → "United Kingdom": countries are saved in lower case, as the site wants them. */
const titleCase = (value: string) => value.replace(/\b\p{L}/gu, (c) => c.toUpperCase())

/** Jobgether's experience levels (its API's own values). */
const JOBGETHER_EXPERIENCE = [
  { value: "entry-level-graduate", label: "Entry level or graduate" },
  { value: "junior-1-2-years", label: "Junior, 1–2 years" },
  { value: "mid-level-2-5-years", label: "Mid level, 2–5 years" },
  { value: "senior-5-10-years", label: "Senior, 5–10 years" },
  { value: "expert-10-years", label: "Expert, 10+ years" },
]

/** What each Apify board is, and what to type as a location for its actor. */
const APIFY_BOARDS: {
  name: ApifyBoardName
  title: string
  /** The board's name inside a sentence ("Search company job boards"). */
  inSentence: string
  intro: string
  locationHint: string
  placeholder: string
}[] = [
  {
    name: "wttj",
    title: "Welcome to the Jungle",
    inSentence: "Welcome to the Jungle",
    intro: "Startups and scale-ups across Europe, including the jobs that used to be on Otta. Searched through Apify. Its terms forbid reading the site automatically: turn it on only if you accept that.",
    locationHint: "A country code such as IT, a place such as Milan, or Remote. Each one adds one search.",
    placeholder: "e.g. IT",
  },
  {
    name: "wellfound",
    title: "Wellfound",
    inSentence: "Wellfound",
    intro: "Startup jobs, most with a salary range. Mostly in the US. Searched through Apify, which also charges $0.005 for each search plus its platform usage. Its terms forbid reading the site automatically: turn it on only if you accept that.",
    locationHint: "A place such as Berlin or Italy, or Remote. Each one adds one search.",
    placeholder: "e.g. Italy",
  },
  {
    name: "yc",
    title: "Y Combinator",
    inSentence: "Y Combinator",
    intro: "Jobs at Y Combinator startups (Work at a Startup). Searched through Apify.",
    locationHint: "A place such as Milan, Italy or Europe, or Remote. Each one adds one search.",
    placeholder: "e.g. Europe",
  },
  {
    name: "atsindex",
    title: "Company job boards",
    inSentence: "company job boards",
    intro: "Thousands of companies' own job boards, searched at once. Searched through Apify.",
    locationHint: "A country code such as IT, a place such as Milan or Europe, or Remote. Each one adds one search.",
    placeholder: "e.g. IT",
  },
]

function StartupsForm({ loaded, readOnly, onDirtyChange }: TabProps & { loaded: Settings }) {
  const { draft, save, root, fields } = useTabForm(loaded, onDirtyChange)
  const s = draft.value
  const set = draft.set
  const err = (path: string) => fieldError(fields, path)
  const live = useLiveEstimate(s)
  const presets = useBundled<VcBoardPreset>("/api/vc-boards")
  const places = useBundled<StartupPlace>("/api/startup-places")

  const onSave = async () => {
    const ok = await save.run(async () => {
      const fresh = await api.get<Settings>("/api/settings")
      draft.reset(await api.send<Settings>("PUT", "/api/settings", mergePaths(fresh, s, STARTUP_PATHS)))
    })
    if (ok) toast.success("Settings saved.")
  }

  const presetUrls = new Set((presets ?? []).map((p) => p.url))
  const chosenPresets = s.vcboards.boards.filter((b) => presetUrls.has(b))
  const otherBoards = s.vcboards.boards.filter((b) => !presetUrls.has(b))

  return (
    <>
      <TabBody readOnly={readOnly} root={root}>
        <p className="text-muted-foreground">
          More places to find jobs at startups. The first four are free: they only cost the AI's reading of each new job,
          so they keep only jobs that match your searches, and only recent ones, except career pages.
        </p>

        <Section
          title="What and where to search"
          intro="Every source below uses these searches and places, each in its own format, unless it has its own. Career pages are the exception: they keep every job unless you set their own title words and places."
        >
          <ChipsInput
            label="Searches"
            value={s.startup_queries}
            onChange={(v) => set("startup_queries", v)}
            placeholder="e.g. AI engineer"
            hint="Job titles or skills. On the boards searched through Apify, each one is searched in each place."
            error={err("startup_queries")}
          />
          <MultiSelect
            label="Places"
            options={(places ?? []).map((p) => ({ value: p.key, label: p.name }))}
            value={s.startup_places}
            onChange={(v) => set("startup_places", v)}
            addLabel="Add a place"
            searchPlaceholder="Search places"
            emptyText={places ? "No place matches." : "Loading places…"}
            hint="Pick countries, Europe for all of Europe, and Remote for remote jobs anywhere. On the boards searched through Apify, each place adds one search."
            error={err("startup_places")}
          />
        </Section>

        <Section title="Built in Europe" intro="Free. Jobs at venture-backed European startups, many with a salary.">
          <SwitchRow
            label="Search Built in Europe"
            hint="Each search is run once in each country."
            checked={s.builtineurope.enabled}
            onCheckedChange={(on) => set("builtineurope.enabled", on)}
          />
          {s.builtineurope.enabled && (
            <>
              <ChipsInput
                label="Searches for this source"
                value={s.builtineurope.queries}
                onChange={(v) => set("builtineurope.queries", v)}
                placeholder="e.g. AI engineer"
                hint={`Job titles or skills, as you'd type them on the site. ${OWN_SEARCHES}`}
                error={err("builtineurope.queries")}
                optional
              />
              <ChipsInput
                label="Countries for this source"
                value={s.builtineurope.countries.map(titleCase)}
                onChange={(v) => set("builtineurope.countries", lower(v))}
                placeholder="e.g. Italy"
                hint={`In English. ${OWN_PLACES}`}
                error={err("builtineurope.countries")}
                optional
              />
              <NumberField
                label="Results per search"
                hint="The most jobs taken from each search."
                value={s.builtineurope.max_results_per_query}
                onChange={(v) => set("builtineurope.max_results_per_query", v)}
                min={1}
                max={500}
                error={err("builtineurope.max_results_per_query")}
              />
            </>
          )}
        </Section>

        <Section title="Jobgether" intro="Free. A large board of remote jobs, with filters for place and experience.">
          <SwitchRow
            label="Search Jobgether"
            hint="Each search is run once, in all your places at once."
            checked={s.jobgether.enabled}
            onCheckedChange={(on) => set("jobgether.enabled", on)}
          />
          {s.jobgether.enabled && (
            <>
              <ChipsInput
                label="Searches for this source"
                value={s.jobgether.queries}
                onChange={(v) => set("jobgether.queries", v)}
                placeholder="e.g. AI engineer"
                hint={`Job titles or skills. ${OWN_SEARCHES}`}
                error={err("jobgether.queries")}
                optional
              />
              <ChipsInput
                label="Places for this source"
                value={s.jobgether.locations}
                onChange={(v) => set("jobgether.locations", lower(v))}
                placeholder="e.g. italy"
                hint={`As Jobgether writes them, e.g. italy, europe or anywhere. ${OWN_PLACES}`}
                error={err("jobgether.locations")}
                optional
              />
              <MultiSelect
                label="Experience"
                options={JOBGETHER_EXPERIENCE}
                value={s.jobgether.experience}
                onChange={(v) => set("jobgether.experience", v)}
                addLabel="Add a level"
                searchPlaceholder="Search levels"
                emptyText="No level matches."
                hint="Leave it empty for any level."
                error={err("jobgether.experience")}
              />
              <NumberField
                label="Results per search"
                hint="The most jobs taken from each search, up to 250."
                value={s.jobgether.max_results_per_query}
                onChange={(v) => set("jobgether.max_results_per_query", v)}
                min={1}
                max={250}
                error={err("jobgether.max_results_per_query")}
              />
            </>
          )}
        </Section>

        <Section
          title="VC job boards"
          intro="Free. Venture capital firms list the open jobs at every startup they back: a hand-picked list of startups that keeps itself up to date."
        >
          <SwitchRow
            label="Search VC job boards"
            hint="Each board is searched once for each search below."
            checked={s.vcboards.enabled}
            onCheckedChange={(on) => set("vcboards.enabled", on)}
          />
          {s.vcboards.enabled && (
            <>
              <MultiSelect
                label="Boards"
                options={(presets ?? []).map((p) => ({ value: p.url, label: p.name }))}
                value={chosenPresets}
                onChange={(v) => set("vcboards.boards", [...v, ...otherBoards])}
                addLabel="Add a board"
                searchPlaceholder="Search firms"
                emptyText={presets ? "No firm matches." : "Loading boards…"}
                hint="Boards on Getro's platform say in their terms that they may not be read automatically. Choose them only if you accept that."
              />
              <ChipsInput
                label="Other boards"
                value={otherBoards}
                onChange={(v) => set("vcboards.boards", [...chosenPresets, ...v])}
                placeholder="e.g. https://jobs.example-ventures.com"
                hint="The address of any other board on Getro or Consider. Getro's terms apply to these too."
                error={err("vcboards.boards")}
                optional
              />
              <ChipsInput
                label="Searches for this source"
                value={s.vcboards.queries}
                onChange={(v) => set("vcboards.queries", v)}
                placeholder="e.g. AI engineer"
                hint={
                  s.startup_queries.length > 0
                    ? OWN_SEARCHES
                    : "Leave it empty to take every job in your places, as there are no searches at the top."
                }
                error={err("vcboards.queries")}
                optional
              />
              <ChipsInput
                label="Places for this source"
                value={s.vcboards.locations}
                onChange={(v) => set("vcboards.locations", v)}
                placeholder="e.g. Italy"
                hint={`As the boards write them, e.g. Italy or Remote. ${OWN_PLACES}`}
                error={err("vcboards.locations")}
                optional
              />
              <NumberField
                label="Results per board"
                hint="The most jobs taken from each board for each search."
                value={s.vcboards.max_results_per_board}
                onChange={(v) => set("vcboards.max_results_per_board", v)}
                min={1}
                max={1000}
                error={err("vcboards.max_results_per_board")}
              />
            </>
          )}
        </Section>

        <Section
          title="Career pages"
          intro="Free. Follow the companies you like: their own job boards are read directly, so a new job shows up even if it's posted nowhere else."
        >
          <SwitchRow
            label="Read career pages"
            hint="Every open job is read once, however old; later runs bring only new ones. Works with Ashby, Greenhouse, Lever, Recruitee, Workable, Personio and Teamtailor."
            checked={s.careers.enabled}
            onCheckedChange={(on) => set("careers.enabled", on)}
          />
          {s.careers.enabled && (
            <>
              <ChipsInput
                label="Career pages"
                value={s.careers.pages}
                onChange={(v) => set("careers.pages", v)}
                placeholder="e.g. https://jobs.ashbyhq.com/example-labs"
                hint="The address of each company's careers page or job board."
                error={err("careers.pages")}
              />
              <ChipsInput
                label="Words in the title"
                value={s.careers.title_words}
                onChange={(v) => set("careers.title_words", v)}
                placeholder="e.g. engineer"
                hint="Only jobs whose title has one of these are kept. Leave it empty to keep every job."
                error={err("careers.title_words")}
                optional
              />
              <ChipsInput
                label="Places"
                value={s.careers.locations}
                onChange={(v) => set("careers.locations", v)}
                placeholder="e.g. Italy"
                hint="Only jobs whose location names one of these, e.g. Milan or Remote, are kept. Leave it empty to keep every place."
                error={err("careers.locations")}
                optional
              />
            </>
          )}
        </Section>

        {APIFY_BOARDS.map((b) => (
          <ApifyBoardSection
            key={b.name}
            board={b}
            value={s[b.name]}
            onChange={(field, v) => set(`${b.name}.${field}`, v)}
            err={(field) => err(`${b.name}.${field}`)}
          />
        ))}

        <Section title="Cost">
          <div role="status">
            <EstimateNote
              live={live}
              lead="A run with all your sources can cost up to"
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

function ApifyBoardSection({
  board,
  value,
  onChange,
  err,
}: {
  board: (typeof APIFY_BOARDS)[number]
  value: ApifyBoardSettings
  onChange: (field: keyof ApifyBoardSettings, value: unknown) => void
  err: (field: keyof ApifyBoardSettings) => string | undefined
}) {
  return (
    <Section title={board.title} intro={board.intro}>
      <SwitchRow
        label={`Search ${board.inSentence}`}
        hint="Apify charges for each job found."
        checked={value.enabled}
        onCheckedChange={(on) => onChange("enabled", on)}
      />
      {value.enabled && (
        <>
          <ChipsInput
            label="Searches for this source"
            value={value.queries}
            onChange={(v) => onChange("queries", v)}
            placeholder="e.g. AI engineer"
            hint={`Job titles or skills. Each one is searched in each place. ${OWN_SEARCHES}`}
            error={err("queries")}
            optional
          />
          <ChipsInput
            label="Places for this source"
            value={value.locations}
            onChange={(v) => onChange("locations", v)}
            placeholder={board.placeholder}
            hint={`${board.locationHint} ${OWN_PLACES}`}
            error={err("locations")}
            optional
          />
          <NumberField
            label="Results per search"
            hint="The most jobs taken from each search. Each job found is paid for."
            value={value.max_results_per_search}
            onChange={(v) => onChange("max_results_per_search", v)}
            min={1}
            max={1000}
            error={err("max_results_per_search")}
          />
          <NumberField
            label="Price per result (USD)"
            hint={`What Apify charges for each job from ${board.inSentence}, in US dollars. Only used to estimate costs.`}
            value={value.price_per_result}
            onChange={(v) => onChange("price_per_result", v)}
            min={0}
            step={0.0001}
            error={err("price_per_result")}
          />
          <ActorField value={value.actor} onChange={(v) => onChange("actor", v)} error={err("actor")} />
        </>
      )}
    </Section>
  )
}

/** The Apify actor: rarely changed, so folded away (open by itself when it was refused). */
function ActorField({ value, onChange, error }: { value: string; onChange: (v: string) => void; error?: string }) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const shown = open || Boolean(error)
  const hint = "Change it only if you use another actor that takes the same input."
  return (
    <Collapsible open={shown} onOpenChange={setOpen}>
      <CollapsibleTrigger className="inline-flex items-center gap-1 rounded-sm text-13 font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring">
        <ChevronRightIcon aria-hidden="true" className={cn("size-3.5 transition-transform", shown && "rotate-90")} />
        Apify actor
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mt-4">
          <Field id={id} label="Actor" hint={hint} error={error}>
            <Input
              id={id}
              value={value}
              onChange={(e) => onChange(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              aria-invalid={error ? true : undefined}
              aria-describedby={describedBy(id, hint, error)}
              className="bg-card sm:max-w-sm"
            />
          </Field>
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}
