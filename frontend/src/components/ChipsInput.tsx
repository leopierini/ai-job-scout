import { useId, useRef, useState, type ClipboardEvent, type KeyboardEvent, type ReactNode } from "react"
import { XIcon } from "lucide-react"

import { describedBy, Field } from "@/components/Field"
import { addUnique, splitList } from "@/lib/lists"
import { cn } from "@/lib/utils"

interface ChipsInputProps {
  value: string[]
  onChange: (value: string[]) => void
  label: ReactNode
  placeholder?: string
  hint?: ReactNode
  error?: string
  optional?: boolean
  id?: string
}

/**
 * A list of short texts as removable chips. Enter or a comma adds what was typed, Backspace in the empty box
 * removes the last chip, and pasting a comma or line separated list adds each item. Text left in the box is
 * added when it loses focus, so nothing typed is lost.
 */
export function ChipsInput({ value, onChange, label, placeholder, hint, error, optional, id }: ChipsInputProps) {
  const autoId = useId()
  const inputId = id ?? autoId
  const inputRef = useRef<HTMLInputElement>(null)
  const [text, setText] = useState("")

  const add = (items: string[]) => {
    if (items.length) onChange(addUnique(value, items))
  }
  const commit = () => {
    add(splitList(text))
    setText("")
  }
  const remove = (index: number) => {
    onChange(value.filter((_, i) => i !== index))
    inputRef.current?.focus()
  }

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault() // Enter never submits a form from here
      commit()
    } else if (e.key === "Backspace" && text === "" && value.length > 0) {
      e.preventDefault()
      onChange(value.slice(0, -1))
    }
  }
  const onPaste = (e: ClipboardEvent<HTMLInputElement>) => {
    const pasted = e.clipboardData.getData("text")
    if (!/[,\r\n]/.test(pasted)) return // a single item: let it land in the box as usual
    e.preventDefault()
    add(splitList(text + pasted))
    setText("")
  }
  const onTextChange = (next: string) => {
    // A separator typed some other way (an input method, autocorrect): add everything before it.
    const last = Math.max(next.lastIndexOf(","), next.lastIndexOf("\n"))
    if (last === -1) return setText(next)
    add(splitList(next.slice(0, last)))
    setText(next.slice(last + 1))
  }

  return (
    <Field id={inputId} label={label} hint={hint} error={error} optional={optional}>
      <div
        className={cn(
          "flex min-h-9 w-full flex-wrap items-center gap-1 rounded-md border border-input bg-card px-1 py-1 transition-[color,box-shadow] focus-within:border-ring focus-within:ring-3 focus-within:ring-ring",
          error && "border-destructive ring-3 ring-destructive/20",
        )}
        onClick={(e) => {
          if (e.target === e.currentTarget) inputRef.current?.focus()
        }}
      >
        {value.length > 0 && (
          <ul className="contents" aria-label={typeof label === "string" ? `${label}, added` : undefined}>
            {value.map((chip, i) => (
              <li
                key={chip + i}
                className="inline-flex h-7 max-w-full items-center gap-1 rounded-sm bg-neutral-soft pr-1 pl-2 text-13"
              >
                <span className="truncate">{chip}</span>
                <button
                  type="button"
                  onClick={() => remove(i)}
                  aria-label={`Remove ${chip}`}
                  className="inline-flex size-5 shrink-0 items-center justify-center rounded-sm text-muted-foreground outline-none hover:bg-border hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring"
                >
                  <XIcon className="size-3.5" aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        )}
        <input
          ref={inputRef}
          id={inputId}
          value={text}
          onChange={(e) => onTextChange(e.target.value)}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          onBlur={commit}
          placeholder={value.length === 0 ? placeholder : undefined}
          aria-describedby={describedBy(inputId, hint, error)}
          aria-invalid={error ? true : undefined}
          autoComplete="off"
          enterKeyHint="enter"
          className="h-7 min-w-32 flex-1 bg-transparent px-1.5 text-base outline-none placeholder:text-muted-foreground md:text-sm"
        />
      </div>
    </Field>
  )
}
