import { useId } from "react"
import { PlusIcon, XIcon } from "lucide-react"

import type { Dealbreaker } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

interface DealbreakersEditorProps {
  value: Dealbreaker[]
  onChange: (value: Dealbreaker[]) => void
  /** The server's message for any rule. */
  error?: string
  hint?: string
}

/** Rules that hold a job's score down: one row per rule (the rule, then what happens), added and removed here. */
export function DealbreakersEditor({
  value,
  onChange,
  error,
  hint = "When one applies, the job's score is held down. Write the rule, then what happens.",
}: DealbreakersEditorProps) {
  const id = useId()
  const setRule = (i: number, patch: Partial<Dealbreaker>) =>
    onChange(value.map((d, j) => (j === i ? { ...d, ...patch } : d)))
  const describedBy = [`${id}-rules-hint`, error ? `${id}-rules-error` : ""].filter(Boolean).join(" ")

  return (
    <fieldset className="flex flex-col gap-3" aria-describedby={describedBy}>
      <legend className="mb-2 text-sm font-medium">Dealbreakers</legend>
      <p id={`${id}-rules-hint`} className="-mt-2 text-13 text-muted-foreground">
        {hint}
      </p>
      {value.length === 0 && <p className="text-13 text-muted-foreground">No dealbreakers yet.</p>}
      <ol className="flex flex-col gap-3">
        {value.map((rule, i) => (
          <li key={i} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 sm:grid-cols-[minmax(0,1fr)_10rem_auto]">
            <Input
              className="col-span-2 bg-card sm:col-span-1"
              value={rule.text}
              onChange={(e) => setRule(i, { text: e.target.value })}
              placeholder="If the posting needs a work visa I don't have"
              aria-label={`Dealbreaker ${i + 1}: if…`}
              aria-invalid={error ? true : undefined}
            />
            <Input
              value={rule.consequence}
              onChange={(e) => setRule(i, { consequence: e.target.value })}
              placeholder="below 40"
              aria-label={`Dealbreaker ${i + 1}: then the score is`}
              className="bg-card"
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={() => onChange(value.filter((_, j) => j !== i))}
              aria-label={`Remove dealbreaker ${i + 1}`}
            >
              <XIcon aria-hidden="true" />
            </Button>
          </li>
        ))}
      </ol>
      {error && (
        <p id={`${id}-rules-error`} className="text-13 font-medium text-destructive">
          {error}
        </p>
      )}
      <div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="bg-card"
          onClick={() => onChange([...value, { text: "", consequence: "" }])}
        >
          <PlusIcon aria-hidden="true" />
          Add a dealbreaker
        </Button>
      </div>
    </fieldset>
  )
}
