import type { ReactNode } from "react"

import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"

/** One output: a switch with its name, and its settings below while it's on. */
export function OutputBlock({
  id,
  title,
  note,
  checked,
  onCheckedChange,
  children,
}: {
  id: string
  title: string
  note: string
  checked: boolean
  onCheckedChange: (on: boolean) => void
  children: ReactNode
}) {
  return (
    <section className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-3 gap-y-1 border-t pt-6">
      <Switch id={id} checked={checked} onCheckedChange={onCheckedChange} aria-describedby={`${id}-note`} />
      <Label htmlFor={id} className="text-base font-semibold">
        {title}
      </Label>
      <p id={`${id}-note`} className="col-start-2 text-13 text-muted-foreground">
        {note}
      </p>
      {checked && <div className="col-span-2 mt-4 flex flex-col gap-4 sm:col-span-1 sm:col-start-2">{children}</div>}
    </section>
  )
}
