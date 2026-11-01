import { CheckIcon, CircleAlertIcon } from "lucide-react"

/** The outcome of a connection test, read out when it changes. */
export function TestLine({ busy, busyText = "Testing…", result }: {
  busy: boolean
  busyText?: string
  result: { ok: boolean; message: string } | null
}) {
  return (
    <p role="status" className={busy || result ? "text-13" : "sr-only"}>
      {busy ? (
        <span className="text-muted-foreground">{busyText}</span>
      ) : result ? (
        <span className="inline-flex items-start gap-1.5">
          {result.ok ? (
            <CheckIcon className="mt-px size-4 shrink-0" aria-hidden="true" />
          ) : (
            <CircleAlertIcon className="mt-px size-4 shrink-0 text-destructive" aria-hidden="true" />
          )}
          <span className={result.ok ? "font-medium" : "font-medium text-destructive"}>{result.message}</span>
        </span>
      ) : null}
    </p>
  )
}
