/** A warning about a job, e.g. "Asks for SQL". Amber, never the accent. */
export function FlagChip({ text }: { text: string }) {
  return (
    <span className="inline-flex h-5 items-center rounded-sm bg-warn-soft px-2 text-xs font-semibold text-warn">
      {text}
    </span>
  )
}
