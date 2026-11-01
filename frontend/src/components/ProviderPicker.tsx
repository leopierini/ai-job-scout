import { useId } from "react"

import type { ProviderInfo } from "@/lib/providers"
import { providerLabel, providersInOrder } from "@/lib/providers"
import { cn } from "@/lib/utils"

interface ProviderPickerProps {
  value: ProviderInfo["id"]
  onChange: (id: ProviderInfo["id"]) => void
  claudeCodeFound: boolean
}

/** The AI providers as a list of radio cards: each one's name and a line on what it is and what it costs. */
export function ProviderPicker({ value, onChange, claudeCodeFound }: ProviderPickerProps) {
  const id = useId()
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="sr-only">AI provider</legend>
      {providersInOrder(claudeCodeFound).map((p) => (
        <label
          key={p.id}
          className={cn(
            "flex cursor-pointer items-start gap-3 rounded-lg border bg-card px-4 py-3 transition-colors hover:border-muted-foreground/40",
            "has-checked:border-foreground has-focus-visible:ring-3 has-focus-visible:ring-ring",
            "has-disabled:cursor-default has-disabled:hover:border-border",
          )}
        >
          <input
            type="radio"
            name={`${id}-provider`}
            value={p.id}
            checked={value === p.id}
            onChange={() => onChange(p.id)}
            className="mt-0.5 size-4 shrink-0 accent-foreground outline-none"
          />
          <span className="flex min-w-0 flex-col gap-0.5">
            <span className="font-medium">{providerLabel(p, claudeCodeFound)}</span>
            <span className="text-13 text-muted-foreground">{p.note}</span>
          </span>
        </label>
      ))}
    </fieldset>
  )
}
