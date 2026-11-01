import { useId } from "react"

import { ChipsInput } from "@/components/ChipsInput"
import { DealbreakersEditor } from "@/components/DealbreakersEditor"
import { describedBy, Field } from "@/components/Field"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { fieldMessage, type FieldErrors, type YouState } from "./wizard"

type ReviewValue = Pick<YouState, "headline" | "profile" | "dealbreakers">

interface ProfileReviewProps {
  value: ReviewValue
  onChange: (v: Partial<ReviewValue>) => void
  keywords: string[]
  onKeywordsChange: (keywords: string[]) => void
  /** Ours (an empty profile) or the server's (422). */
  profileError?: string
  fields: FieldErrors
}

/** The draft to check and change: headline, profile, dealbreakers and search keywords. */
export function ProfileReview({ value, onChange, keywords, onKeywordsChange, profileError, fields }: ProfileReviewProps) {
  const id = useId()
  const headlineError = fieldMessage(fields, "ui.headline")
  const rulesError = fieldMessage(fields, "scoring.dealbreakers")
  const headlineHint = "One line shown at the top of your jobs."
  const profileHint = "Markdown. Who you are and what you want, in your own words. Every job is scored against it."

  return (
    <div className="flex flex-col gap-5">
      <Field id={`${id}-headline`} label="Headline" hint={headlineHint} error={headlineError}>
        <Input
          id={`${id}-headline`}
          value={value.headline}
          maxLength={120}
          onChange={(e) => onChange({ headline: e.target.value })}
          placeholder="e.g. Software Engineer, early career, open to the EU and UK"
          aria-invalid={headlineError ? true : undefined}
          aria-describedby={describedBy(`${id}-headline`, headlineHint, headlineError)}
          className="bg-card"
        />
      </Field>
      <Field id={`${id}-profile`} label="Profile" hint={profileHint} error={profileError}>
        <Textarea
          id={`${id}-profile`}
          value={value.profile}
          onChange={(e) => onChange({ profile: e.target.value })}
          aria-invalid={profileError ? true : undefined}
          aria-describedby={describedBy(`${id}-profile`, profileHint, profileError)}
          className="max-h-[32rem] min-h-64 bg-card font-mono text-13 md:text-13"
        />
      </Field>

      <DealbreakersEditor
        value={value.dealbreakers}
        onChange={(dealbreakers) => onChange({ dealbreakers })}
        error={rulesError}
      />

      <ChipsInput
        label="Search keywords"
        value={keywords}
        onChange={onKeywordsChange}
        placeholder="e.g. Software Engineer"
        hint="Job titles to search for. You can change them on the next step too."
      />
    </div>
  )
}
