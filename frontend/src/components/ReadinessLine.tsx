import { Fragment } from "react"

import { missingLabel, settingsHref, type Missing } from "@/lib/readiness"
import { cn } from "@/lib/utils"

const LINK =
  "rounded-sm font-medium text-foreground underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"

/**
 * "Before your first search: add your Apify token · choose where to search.", each item a link to where it's fixed:
 * its Settings tab, unless `href` says otherwise (the wizard links to its own steps).
 */
export function ReadinessLine({
  missing,
  lead = "Before your first search:",
  href = settingsHref,
  className,
}: {
  missing: Missing[]
  lead?: string
  href?: (m: Missing) => string
  className?: string
}) {
  if (missing.length === 0) return null
  return (
    <p className={cn("text-13", className)}>
      {lead}{" "}
      {missing.map((m, i) => (
        <Fragment key={m.what}>
          {i > 0 && " · "}
          <a href={href(m)} className={LINK}>
            {missingLabel(m)}
          </a>
        </Fragment>
      ))}
      .
    </p>
  )
}
