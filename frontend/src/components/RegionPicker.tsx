import { useId, useState } from "react"
import { ChevronRightIcon } from "lucide-react"

import type { Region } from "@/api/types"
import { describedBy, Field } from "@/components/Field"
import { MultiSelect } from "@/components/MultiSelect"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Input } from "@/components/ui/input"
import { useRegions } from "@/lib/regions"
import { cn } from "@/lib/utils"

interface RegionPickerProps {
  value: Region[]
  onChange: (value: Region[]) => void
  label?: string
  hint?: string
  error?: string
}

/** LinkedIn regions to search: a searchable list of the bundled ones, plus any custom LinkedIn geoId. */
export function RegionPicker({ value, onChange, label = "Where to search", hint, error }: RegionPickerProps) {
  const { regions, error: loadError, retry } = useRegions()
  const known = regions?.linkedin ?? []
  // Custom regions the user added are options too, so they show as chips and can be removed.
  const all = [...known, ...value.filter((r) => !known.some((k) => k.geo_id === r.geo_id))]
  const byId = new Map(all.map((r) => [r.geo_id, r]))

  return (
    <MultiSelect
      label={label}
      options={all.map((r) => ({ value: r.geo_id, label: r.name }))}
      value={value.map((r) => r.geo_id)}
      onChange={(ids) => onChange(ids.map((id) => byId.get(id)).filter((r): r is Region => r !== undefined))}
      addLabel="Add a region"
      searchPlaceholder="Search regions"
      emptyText={loadError ? "The regions couldn't be loaded." : regions ? "No region matches." : "Loading regions…"}
      hint={hint}
      error={error}
    >
      {loadError && (
        <p className="text-13 text-muted-foreground">
          The list of regions couldn't be loaded.{" "}
          <button
            type="button"
            onClick={retry}
            className="rounded-sm font-medium text-foreground underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
          >
            Try again
          </button>
        </p>
      )}
      <CustomRegion
        onAdd={(r) => {
          if (!value.some((v) => v.geo_id === r.geo_id)) onChange([...value, r])
        }}
      />
    </MultiSelect>
  )
}

/** "Add a custom geoId": a name and LinkedIn's numeric id for a place that isn't in the list. */
function CustomRegion({ onAdd }: { onAdd: (r: Region) => void }) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState("")
  const [geoId, setGeoId] = useState("")
  const [errors, setErrors] = useState<{ name?: string; geoId?: string }>({})

  const add = (e: { preventDefault(): void }) => {
    e.preventDefault()
    const next = {
      name: name.trim() ? undefined : "Add a name for this place.",
      geoId: /^\d+$/.test(geoId.trim()) ? undefined : "A geoId is a number, e.g. 101165590.",
    }
    setErrors(next)
    if (next.name || next.geoId) return
    onAdd({ name: name.trim(), geo_id: geoId.trim() })
    setName("")
    setGeoId("")
  }

  const idHint = "Search LinkedIn Jobs for the place and copy the number after geoId= in the address bar."
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="inline-flex items-center gap-1 rounded-sm text-13 font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring">
        <ChevronRightIcon aria-hidden="true" className={cn("size-3.5 transition-transform", open && "rotate-90")} />
        Add a custom geoId
      </CollapsibleTrigger>
      <CollapsibleContent>
        {/* Not a <form>: the wizard step around it may be one. Enter in either box adds. */}
        <div
          className="mt-3 grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-start"
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.target as HTMLElement).tagName === "INPUT") add(e)
          }}
        >
          <Field id={`${id}-name`} label="Place name" error={errors.name}>
            <Input
              id={`${id}-name`}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Greater Milan"
              aria-invalid={errors.name ? true : undefined}
              aria-describedby={describedBy(`${id}-name`, undefined, errors.name)}
              className="bg-card"
            />
          </Field>
          <Field id={`${id}-geo`} label="LinkedIn geoId" error={errors.geoId} hint={idHint}>
            <Input
              id={`${id}-geo`}
              value={geoId}
              onChange={(e) => setGeoId(e.target.value)}
              inputMode="numeric"
              placeholder="e.g. 101165590"
              aria-invalid={errors.geoId ? true : undefined}
              aria-describedby={describedBy(`${id}-geo`, idHint, errors.geoId)}
              className="bg-card"
            />
          </Field>
          <Button type="button" variant="outline" className="bg-card sm:mt-6" onClick={add}>
            Add
          </Button>
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}
