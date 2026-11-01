import type { ComponentType, ReactNode } from "react"

import { cn } from "@/lib/utils"

/**
 * The bar at the top of every screen: the app name (back to the jobs), an optional `lead` next to it (the
 * main screen's profile headline) and whatever the screen adds on the right.
 */
export function AppBar({ lead, children }: { lead?: ReactNode; children?: ReactNode }) {
  return (
    <header className="flex min-h-14 flex-wrap items-center gap-x-4 gap-y-2 border-b bg-card px-4 py-3 md:px-5">
      <a
        href="#/"
        className="rounded-sm text-base font-semibold outline-none focus-visible:ring-3 focus-visible:ring-ring"
      >
        AI Job Scout
      </a>
      {lead}
      {children && <div className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-2">{children}</div>}
    </header>
  )
}

/** A text link in the bar, with an optional lucide icon. */
export function BarLink({
  href,
  icon: Icon,
  current = false,
  children,
}: {
  href: string
  icon?: ComponentType<{ className?: string; "aria-hidden"?: boolean }>
  current?: boolean
  children: ReactNode
}) {
  return (
    <a
      href={href}
      aria-current={current ? "page" : undefined}
      className={cn(
        "inline-flex items-center gap-1 rounded-sm text-13 font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring",
        current && "text-foreground",
      )}
    >
      {Icon && <Icon className="size-4" aria-hidden />}
      {children}
    </a>
  )
}
