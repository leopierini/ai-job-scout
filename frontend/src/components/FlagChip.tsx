/** A warning about a job, e.g. "Asks for SQL". Amber, never the accent. A long one wraps inside the chip. */
export function FlagChip({ text }: { text: string }) {
  return (
    <span className="inline-flex min-h-5 max-w-full items-center rounded-sm bg-warn-soft px-2 text-xs leading-5 font-semibold break-words text-warn">
      {text}
    </span>
  )
}
