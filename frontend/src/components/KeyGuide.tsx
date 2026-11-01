import { useState } from "react"
import { ChevronRightIcon, ExternalLinkIcon } from "lucide-react"

import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { KEY_GUIDES, type KeyGuideProvider } from "@/lib/key-guides"
import { cn } from "@/lib/utils"

/** Collapsible, numbered steps for getting one key, with a link to the right page. */
export function KeyGuide({ provider, defaultOpen = false }: { provider: KeyGuideProvider; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  const guide = KEY_GUIDES[provider]
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="inline-flex items-center gap-1 rounded-sm text-13 font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring">
        <ChevronRightIcon aria-hidden="true" className={cn("size-3.5 transition-transform", open && "rotate-90")} />
        {guide.title}
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ol className="mt-2 flex flex-col gap-2 text-13">
          {guide.steps.map((step, i) => (
            <li key={step} className="grid grid-cols-[16px_1fr] gap-2">
              <span className="num text-muted-foreground">{i + 1}</span>
              <span>{step}</span>
            </li>
          ))}
        </ol>
        <a
          href={guide.link.href}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-3 ml-6 inline-flex items-center gap-1 rounded-sm text-13 font-medium text-foreground underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
        >
          {guide.link.label}
          <span className="sr-only">(opens in a new tab)</span>
          <ExternalLinkIcon aria-hidden="true" className="size-3.5" />
        </a>
      </CollapsibleContent>
    </Collapsible>
  )
}
