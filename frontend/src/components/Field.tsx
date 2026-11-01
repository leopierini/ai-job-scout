import type { ReactNode } from "react"

import { Label } from "@/components/ui/label"

/** The ids an input should list in aria-describedby for this field's hint and error. */
export function describedBy(id: string, hint?: ReactNode, error?: string): string | undefined {
  const ids = [hint ? `${id}-hint` : "", error ? `${id}-error` : ""].filter(Boolean)
  return ids.length ? ids.join(" ") : undefined
}

interface FieldProps {
  id: string
  label: ReactNode
  /** One line under the input: what to enter. */
  hint?: ReactNode
  /** What's wrong with the value (the server's message for this field, or ours). */
  error?: string
  optional?: boolean
  children: ReactNode
}

/** A label, the input, a hint and an error, in that order. The input gets `id` and `describedBy(id, hint, error)`. */
export function Field({ id, label, hint, error, optional, children }: FieldProps) {
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <Label htmlFor={id} className="leading-snug">
        {label}
        {optional && <span className="font-normal text-muted-foreground">(optional)</span>}
      </Label>
      {children}
      {hint && (
        <p id={`${id}-hint`} className="text-13 text-muted-foreground">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} className="text-13 font-medium text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}
