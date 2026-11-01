/** The line above the bar while the sample data is shown, with the way to the user's own setup. */
export function SampleStrip({ note }: { note?: string }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b bg-neutral-soft px-4 py-2 text-13 md:px-5">
      <span>
        You're exploring sample data for a fictional person.{note ? ` ${note}` : ""}
      </span>
      <a
        href="#/setup"
        className="rounded-sm font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
      >
        Set up your own search
      </a>
    </div>
  )
}
