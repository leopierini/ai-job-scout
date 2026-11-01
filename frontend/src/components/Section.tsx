import type { ReactNode } from "react"

/** A titled part of a form: a hairline above every one but the first. */
export function Section({ title, intro, children }: { title: string; intro?: ReactNode; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-5 border-t pt-6 first:border-t-0 first:pt-0">
      <div className="flex flex-col gap-1">
        <h2 className="text-base font-semibold">{title}</h2>
        {intro && <p className="text-muted-foreground">{intro}</p>}
      </div>
      {children}
    </section>
  )
}
