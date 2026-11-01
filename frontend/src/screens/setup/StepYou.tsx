import { useEffect, useId, useRef, useState } from "react"

import { api, ApiError } from "@/api/client"
import type { Draft, DraftBody } from "@/api/types"
import { ChipsInput } from "@/components/ChipsInput"
import { ErrorNote } from "@/components/ErrorNote"
import { describedBy, Field } from "@/components/Field"
import { RegionPicker } from "@/components/RegionPicker"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { CvInput } from "./CvInput"
import { ProfileReview } from "./ProfileReview"
import { Section, StepFooter, type StepProps } from "./parts"
import { useSave } from "./useSave"
import {
  CAREER_LEVELS,
  PROFILE_TEMPLATE,
  applyYou,
  canLeaveYou,
  draftAnswers,
  fieldMessage,
  messageOf,
  type SearchState,
  type YouState,
} from "./wizard"

interface StepYouProps extends StepProps {
  you: YouState
  onChange: (patch: Partial<YouState>) => void
  search: SearchState
  onSearchChange: (patch: Partial<SearchState>) => void
  /** LinkedIn's region names: a place to avoid that matches one is a country, the rest are cities. */
  regionNames: string[]
}

const DRAFTING = "Reading your CV and drafting your profile… this can take a minute"
const NEED_ROLE = "Add at least one target role."

export function StepYou({ you, onChange, search, onSearchChange, regionNames, ...nav }: StepYouProps) {
  const id = useId()
  const reviewRef = useRef<HTMLHeadingElement>(null)
  const [drafting, setDrafting] = useState(false)
  const [draftError, setDraftError] = useState<string | null>(null)
  const [rolesError, setRolesError] = useState<string | null>(null)
  const [focusReview, setFocusReview] = useState(0)
  const save = useSave()

  // After a draft or "Write it myself", move to the review: it's what to read next.
  useEffect(() => {
    if (focusReview) reviewRef.current?.focus()
  }, [focusReview])

  const openReview = (patch: Partial<YouState>) => {
    onChange({ ...patch, reviewOpen: true })
    setFocusReview((n) => n + 1)
  }

  const draft = async () => {
    setDraftError(null)
    if (you.roles.length === 0) {
      setRolesError(NEED_ROLE)
      document.getElementById(`${id}-roles`)?.focus()
      return
    }
    setRolesError(null)
    const body: DraftBody = {
      cv_text: you.cvMode === "paste" ? you.cvText : null,
      cv_file: you.cvMode === "upload" ? you.cvFile : null,
      answers: draftAnswers(you),
    }
    setDrafting(true)
    try {
      const d = await api.send<Draft>("POST", "/api/draft", body)
      onSearchChange({ keywords: d.keywords, indeedQueries: d.indeed_queries })
      openReview({ headline: d.headline, profile: d.profile_md, dealbreakers: d.dealbreakers, drafted: true })
    } catch (err) {
      const roles = err instanceof ApiError ? fieldMessage(err.fields, "answers.target_roles") : undefined
      if (roles) setRolesError(roles)
      else setDraftError(messageOf(err))
    } finally {
      setDrafting(false)
    }
  }

  const writeMyself = () => {
    setDraftError(null)
    openReview({ profile: you.profile.trim() ? you.profile : PROFILE_TEMPLATE })
  }

  const gate = canLeaveYou(you)
  const next = async () => {
    if (gate) return
    const ok = await save.run(async () => {
      await api.send("PUT", "/api/profile", { text: you.profile })
      await nav.saveSettings((s) => applyYou(s, you, regionNames))
    })
    if (ok) nav.next()
  }

  const fields = save.error?.fields ?? null
  const startError = fieldMessage(fields, "cleanup.earliest_start")
  const regionsError = fieldMessage(fields, "linkedin.regions")
  const startHint = "Jobs that start before this date are left out."

  return (
    <>
      <div className="flex flex-col gap-8">
        <Section title="Your CV" intro="The AI reads it to draft your profile. It's optional, but the draft is much better with it.">
          <CvInput value={you} onChange={onChange} />
        </Section>

        <Section title="What you're looking for">
          <ChipsInput
            id={`${id}-roles`}
            label="Target roles"
            value={you.roles}
            onChange={(roles) => {
              onChange({ roles })
              if (roles.length) setRolesError(null)
            }}
            placeholder="e.g. Software Engineer"
            hint="Press Enter after each one."
            error={rolesError ?? undefined}
          />
          <Field id={`${id}-level`} label="Career level">
            <Select value={you.careerLevel || undefined} onValueChange={(careerLevel) => onChange({ careerLevel })}>
              <SelectTrigger id={`${id}-level`} className="w-full bg-card sm:w-80">
                <SelectValue placeholder="Choose your level" />
              </SelectTrigger>
              <SelectContent>
                {CAREER_LEVELS.map((level) => (
                  <SelectItem key={level} value={level}>
                    {level}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <RegionPicker
            value={you.regions}
            onChange={(regions) => onChange({ regions })}
            hint="LinkedIn searches each region you add."
            error={regionsError}
          />
          <ChipsInput
            label="Places to avoid"
            optional
            value={you.avoid}
            onChange={(avoid) => onChange({ avoid })}
            placeholder="e.g. Paris"
            hint="Countries or cities whose jobs you never want to see."
          />
          <Field id={`${id}-start`} label="Earliest start" optional hint={startHint} error={startError}>
            <Input
              id={`${id}-start`}
              type="date"
              value={you.earliestStart}
              onChange={(e) => onChange({ earliestStart: e.target.value })}
              aria-invalid={startError ? true : undefined}
              aria-describedby={describedBy(`${id}-start`, startHint, startError)}
              className="w-full bg-card sm:w-48"
            />
          </Field>
          <ChipsInput
            label="Languages you work in"
            optional
            value={you.languages}
            onChange={(languages) => onChange({ languages })}
            placeholder="e.g. English"
          />
          <Field id={`${id}-extra`} label="Anything else that rules a job out" optional>
            <Textarea
              id={`${id}-extra`}
              value={you.extra}
              onChange={(e) => onChange({ extra: e.target.value })}
              placeholder="e.g. I need visa sponsorship to work in the UK."
              className="bg-card"
            />
          </Field>
          <div className="flex flex-col items-start gap-3">
            <Button
              type="button"
              variant={you.reviewOpen ? "outline" : "default"}
              className={you.reviewOpen ? "bg-card" : undefined}
              onClick={draft}
              disabled={drafting}
            >
              {drafting ? "Drafting…" : you.drafted ? "Draft again" : "Draft my profile"}
            </Button>
            <p role="status" className={drafting ? "text-13 text-muted-foreground" : "sr-only"}>
              {drafting ? DRAFTING : ""}
            </p>
            {draftError && !drafting && (
              <ErrorNote
                className="w-full"
                message={draftError}
                hint="If the AI isn't working, go back to step 1 and test it again, or write the profile yourself."
                onRetry={draft}
                retryLabel="Try again"
                actions={
                  <Button variant="outline" size="sm" className="bg-card" onClick={writeMyself}>
                    Write it myself
                  </Button>
                }
              />
            )}
            {!you.reviewOpen && !draftError && !drafting && (
              <p className="text-13 text-muted-foreground">
                Rather not use AI for this?{" "}
                <button
                  type="button"
                  onClick={writeMyself}
                  className="rounded-sm font-medium text-foreground underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
                >
                  Write it myself
                </button>
              </p>
            )}
          </div>
        </Section>

        {you.reviewOpen && (
          <section className="flex flex-col gap-5 border-t pt-6">
            <div className="flex flex-col gap-1">
              <h2 ref={reviewRef} tabIndex={-1} className="text-base font-semibold outline-none">
                Your profile
              </h2>
              <p className="text-muted-foreground">Check it and change anything that isn't right. Next saves it.</p>
            </div>
            <ProfileReview
              value={you}
              onChange={onChange}
              keywords={search.keywords}
              onKeywordsChange={(keywords) => onSearchChange({ keywords })}
              profileError={fieldMessage(fields, "text")}
              fields={fields}
            />
          </section>
        )}
      </div>
      <StepFooter
        {...nav}
        onNext={next}
        nextPrimary={you.reviewOpen}
        nextDisabled={gate !== null}
        saving={save.saving}
        note={gate ?? undefined}
        error={save.error?.message ?? null}
      />
    </>
  )
}
