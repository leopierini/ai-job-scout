import type { ReactNode } from "react"
import { CircleAlertIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

interface ErrorNoteProps {
  /** What happened, in plain words (usually the server's `error` message). */
  message: string
  /** What to do next, when the message doesn't already say it. */
  hint?: string
  onRetry?: () => void
  retryLabel?: string
  /** Extra buttons next to Retry. */
  actions?: ReactNode
  className?: string
  /** More below the hint, e.g. links to where it's fixed. */
  children?: ReactNode
}

export function ErrorNote({ message, hint, onRetry, retryLabel = "Retry", actions, className, children }: ErrorNoteProps) {
  return (
    <div role="alert" className={cn("flex gap-3 rounded-lg border bg-card p-4", className)}>
      <CircleAlertIcon className="mt-px size-4 shrink-0 text-destructive" aria-hidden="true" />
      <div className="flex min-w-0 flex-col gap-1">
        <p className="font-medium break-words">{message}</p>
        {hint && <p className="text-muted-foreground">{hint}</p>}
        {children}
        {(onRetry || actions) && (
          <div className="mt-2 flex flex-wrap gap-2">
            {onRetry && (
              <Button variant="outline" size="sm" className="bg-card" onClick={onRetry}>
                {retryLabel}
              </Button>
            )}
            {actions}
          </div>
        )}
      </div>
    </div>
  )
}
