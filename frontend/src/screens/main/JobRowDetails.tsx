import type { ReactNode } from "react"
import { ExternalLinkIcon } from "lucide-react"

import type { JobOut } from "@/api/types"
import { relativeDay } from "@/lib/format"
import { fullDay, sourceLabel } from "@/lib/jobs"

function Item({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[96px_1fr] gap-2">
      <dt className="text-muted-foreground">{term}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  )
}

/** What a row opens to: the facts that don't fit in the table, and the whole reason when the row cut it short. */
export function JobRowDetails({ job, id, showReason }: { job: JobOut; id: string; showReason: boolean }) {
  return (
    <div id={id} className="flex flex-col gap-3 py-1 text-13">
      {showReason && job.reason && <p className="max-w-prose whitespace-normal">{job.reason}</p>}
      <dl className="grid gap-x-8 gap-y-1 whitespace-normal md:grid-cols-2">
        <Item term="Place">{job.location || "Not given"}</Item>
        <Item term="Deadline">{fullDay(job.deadline) || "Not given"}</Item>
        {job.recruiter && (
          <Item term="Recruiter">
            {job.recruiter}
            {job.recruiter_title && <span className="text-muted-foreground">, {job.recruiter_title}</span>}
            {job.recruiter_url && (
              <>
                {" "}
                <a
                  href={job.recruiter_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 rounded-sm font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
                >
                  Contact
                  <ExternalLinkIcon className="size-3.5" aria-hidden="true" />
                </a>
              </>
            )}
          </Item>
        )}
        <Item term="Keyword">{job.keyword || "Not given"}</Item>
        <Item term="Source">{sourceLabel(job.source)}</Item>
        <Item term="Area">{job.area || "Not given"}</Item>
        <Item term="First seen">{relativeDay(job.first_seen_at)}</Item>
        {job.employment_type && <Item term="Type">{job.employment_type}</Item>}
      </dl>
    </div>
  )
}
