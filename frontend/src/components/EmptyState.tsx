import type { ReactNode } from "react"

/** What a screen shows when there's nothing in it yet: what this is, and the one thing to do next. */
export function EmptyState({
  title,
  body,
  action,
  centered = false,
}: {
  title: string
  body: ReactNode
  action?: ReactNode
  /** Centre it in the space it is in (the main screen before the first run). */
  centered?: boolean
}) {
  return (
    <div
      className={
        centered
          ? "flex flex-1 flex-col items-center justify-center gap-2 py-8 text-center"
          : "flex flex-col items-start gap-2 py-8"
      }
    >
      <h2 className="text-base font-semibold">{title}</h2>
      <p className="max-w-prose text-muted-foreground">{body}</p>
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}
