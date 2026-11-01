import { useId, useState, type ChangeEvent } from "react"
import { FileTextIcon } from "lucide-react"

import { describedBy, Field } from "@/components/Field"
import { Button, buttonVariants } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"
import type { CvFile, YouState } from "./wizard"

const MAX_BYTES = 5 * 1024 * 1024
const ACCEPT = ".pdf,.txt,.md,application/pdf,text/plain,text/markdown"
const TOO_BIG = "That file is over 5 MB. Pick a smaller one, or paste your CV instead."
const WRONG_TYPE = "That isn't a PDF, TXT or MD file. Pick one of those, or paste your CV instead."
const UNREADABLE = "That file couldn't be read. Try again, or paste your CV instead."

/** The file's content as base64, without the "data:…;base64," prefix. */
function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const url = String(reader.result)
      resolve(url.slice(url.indexOf(",") + 1))
    }
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

type CvValue = Pick<YouState, "cvMode" | "cvText" | "cvFile">

/** The CV: an uploaded PDF, TXT or MD file (up to 5 MB), or pasted text. */
export function CvInput({ value, onChange }: { value: CvValue; onChange: (v: Partial<CvValue>) => void }) {
  const id = useId()
  const [error, setError] = useState<string | null>(null)

  const pick = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = "" // picking the same file again still fires a change
    if (!file) return
    setError(null)
    if (!/\.(pdf|txt|md)$/i.test(file.name)) return setError(WRONG_TYPE)
    if (file.size > MAX_BYTES) return setError(TOO_BIG)
    try {
      const cvFile: CvFile = { name: file.name, base64: await readBase64(file) }
      onChange({ cvFile })
    } catch {
      setError(UNREADABLE)
    }
  }

  const fileHint = "PDF, TXT or MD, up to 5 MB."
  return (
    <Tabs value={value.cvMode} onValueChange={(v) => onChange({ cvMode: v as CvValue["cvMode"] })}>
      <TabsList>
        <TabsTrigger value="upload">Upload a file</TabsTrigger>
        <TabsTrigger value="paste">Paste the text</TabsTrigger>
      </TabsList>
      <TabsContent value="upload" className="pt-3">
        {value.cvFile ? (
          <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-card px-4 py-3">
            <FileTextIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
            <span className="min-w-0 flex-1 truncate font-medium">{value.cvFile.name}</span>
            <Button type="button" variant="ghost" size="sm" onClick={() => onChange({ cvFile: null })}>
              Remove
            </Button>
          </div>
        ) : (
          <Field id={`${id}-file`} label="Your CV file" hint={fileHint} error={error ?? undefined}>
            {/* The native input stays in the tab order (visually hidden); the label below is its visible button. */}
            <div className="flex flex-wrap items-center gap-3">
              <input
                id={`${id}-file`}
                type="file"
                accept={ACCEPT}
                onChange={pick}
                aria-invalid={error ? true : undefined}
                aria-describedby={describedBy(`${id}-file`, fileHint, error ?? undefined)}
                className="peer sr-only"
              />
              <label
                htmlFor={`${id}-file`}
                className={cn(
                  buttonVariants({ variant: "outline" }),
                  "cursor-pointer bg-card peer-focus-visible:border-ring peer-focus-visible:ring-3 peer-focus-visible:ring-ring",
                )}
              >
                Choose a file
              </label>
              <span className="text-13 text-muted-foreground">No file chosen</span>
            </div>
          </Field>
        )}
      </TabsContent>
      <TabsContent value="paste" className="pt-3">
        <Field id={`${id}-text`} label="Your CV as text">
          <Textarea
            id={`${id}-text`}
            value={value.cvText}
            onChange={(e) => onChange({ cvText: e.target.value })}
            placeholder="Paste the text of your CV here"
            className="max-h-96 min-h-40 bg-card"
          />
        </Field>
      </TabsContent>
    </Tabs>
  )
}
