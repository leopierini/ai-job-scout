import { useId } from "react"

import type { CleanupSettings } from "@/api/types"
import { describedBy, Field } from "@/components/Field"
import { Section } from "@/components/Section"
import { Input } from "@/components/ui/input"
import { fieldError, type FieldErrors } from "@/lib/form"
import { NumberField, SwitchRow } from "./shared"

interface CleanupSectionProps {
  cleanup: CleanupSettings
  /** Set a path under `cleanup`. */
  set: (path: keyof CleanupSettings, value: unknown) => void
  fields: FieldErrors
}

/** "Clean up old jobs": the switch for the whole step, each rule, and the rules' dates and ages. */
export function CleanupSection({ cleanup: c, set, fields }: CleanupSectionProps) {
  const id = useId()
  const off = !c.enabled
  const startError = fieldError(fields, "cleanup.earliest_start")
  const startHint = "Jobs starting before this date are archived. Leave it empty to use today's date."

  return (
    <Section
      title="Clean up old jobs"
      intro="The first step of every run. Only jobs in To review and Interested are archived, and you can bring any of them back from Archived."
    >
      <SwitchRow
        label="Clean up before each run"
        hint="Turn it off to keep every job until you move it yourself."
        checked={c.enabled}
        onCheckedChange={(on) => set("enabled", on)}
      />
      <div className="flex flex-col gap-5">
        <SwitchRow
          label="Archive jobs whose deadline has passed"
          hint="Uses the application deadline when the posting gives one."
          checked={c.deadline_passed}
          onCheckedChange={(on) => set("deadline_passed", on)}
          disabled={off}
        />
        <SwitchRow
          label="Archive jobs that start too early"
          hint="Uses the start date when the posting gives one."
          checked={c.start_too_early}
          onCheckedChange={(on) => set("start_too_early", on)}
          disabled={off}
        />
        <Field id={`${id}-start`} label="Earliest start date" hint={startHint} error={startError} optional>
          <Input
            id={`${id}-start`}
            type="date"
            value={c.earliest_start ?? ""}
            onChange={(e) => set("earliest_start", e.target.value || null)}
            disabled={off || !c.start_too_early}
            aria-invalid={startError ? true : undefined}
            aria-describedby={describedBy(`${id}-start`, startHint, startError)}
            className="num w-44 bg-card"
          />
        </Field>
        <SwitchRow
          label="Archive old postings"
          hint="Archives jobs posted longer ago than the age below."
          checked={c.too_old}
          onCheckedChange={(on) => set("too_old", on)}
          disabled={off}
        />
        <NumberField
          label="Maximum posting age, in days"
          hint="Jobs posted more than this many days ago are archived."
          value={c.max_age_days}
          onChange={(v) => set("max_age_days", v)}
          min={1}
          error={fieldError(fields, "cleanup.max_age_days")}
          disabled={off || !c.too_old}
        />
        <SwitchRow
          label="Interested jobs are exempt from the age rule"
          hint="A job you marked Interested stays, however old the posting is."
          checked={c.interested_exempt_from_age}
          onCheckedChange={(on) => set("interested_exempt_from_age", on)}
          disabled={off || !c.too_old}
        />
        <SwitchRow
          label="Archive jobs in places you exclude"
          hint="Uses the excluded countries and cities on the Search tab, for jobs found before you excluded them."
          checked={c.excluded_place}
          onCheckedChange={(on) => set("excluded_place", on)}
          disabled={off}
        />
      </div>
    </Section>
  )
}
