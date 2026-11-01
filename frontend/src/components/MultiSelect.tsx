import { useId, useState, type ReactNode } from "react"
import { ChevronDownIcon, XIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"

export interface Option {
  value: string
  label: string
}

interface MultiSelectProps {
  options: Option[]
  value: string[]
  onChange: (value: string[]) => void
  label: ReactNode
  /** The button that opens the list, e.g. "Add a region". */
  addLabel: string
  searchPlaceholder: string
  emptyText: string
  hint?: ReactNode
  error?: string
  /** Shown under the chips, e.g. a way to add something that isn't in the list. */
  children?: ReactNode
}

/**
 * Pick several options from a long list: the picked ones as removable chips, and a searchable list that stays
 * open while you pick (arrow keys and Enter, or click; Escape closes it).
 */
export function MultiSelect({
  options,
  value,
  onChange,
  label,
  addLabel,
  searchPlaceholder,
  emptyText,
  hint,
  error,
  children,
}: MultiSelectProps) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const labelOf = new Map(options.map((o) => [o.value, o.label]))
  const toggle = (v: string) => onChange(value.includes(v) ? value.filter((x) => x !== v) : [...value, v])
  const describedBy = [hint ? `${id}-hint` : "", error ? `${id}-error` : ""].filter(Boolean).join(" ") || undefined

  return (
    <div role="group" aria-labelledby={`${id}-label`} aria-describedby={describedBy} className="flex flex-col gap-2">
      <p id={`${id}-label`} className="text-sm leading-snug font-medium">
        {label}
      </p>
      {value.length > 0 && (
        <ul className="flex flex-wrap gap-1">
          {value.map((v) => {
            const name = labelOf.get(v) ?? v
            return (
              <li key={v} className="inline-flex h-7 items-center gap-1 rounded-sm bg-neutral-soft pr-1 pl-2 text-13">
                {name}
                <button
                  type="button"
                  onClick={() => toggle(v)}
                  aria-label={`Remove ${name}`}
                  className="inline-flex size-5 items-center justify-center rounded-sm text-muted-foreground outline-none hover:bg-border hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring"
                >
                  <XIcon className="size-3.5" aria-hidden="true" />
                </button>
              </li>
            )
          })}
        </ul>
      )}
      <div>
        <Popover open={open} onOpenChange={setOpen}>
          <PopoverTrigger asChild>
            <Button variant="outline" className="bg-card" aria-invalid={error ? true : undefined}>
              {addLabel}
              <ChevronDownIcon aria-hidden="true" className="text-muted-foreground" />
            </Button>
          </PopoverTrigger>
          <PopoverContent align="start" className="w-72 p-0">
            <Command>
              <CommandInput placeholder={searchPlaceholder} aria-label={searchPlaceholder} />
              <CommandList>
                <CommandEmpty>{emptyText}</CommandEmpty>
                <CommandGroup>
                  {options.map((o) => {
                    const checked = value.includes(o.value)
                    return (
                      <CommandItem
                        key={o.value}
                        value={`${o.label} ${o.value}`}
                        onSelect={() => toggle(o.value)}
                        data-checked={checked}
                        aria-checked={checked}
                      >
                        {o.label}
                      </CommandItem>
                    )
                  })}
                </CommandGroup>
              </CommandList>
            </Command>
          </PopoverContent>
        </Popover>
      </div>
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
      {children}
    </div>
  )
}
