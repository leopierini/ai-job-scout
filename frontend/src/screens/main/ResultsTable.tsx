import { useId, useLayoutEffect, useMemo, useRef, useState, type MouseEvent, type RefObject } from "react"
import { ArrowDownIcon, ArrowUpIcon, ChevronDownIcon, XIcon } from "lucide-react"

import type { JobOut, Status, TableColumn, View, ViewCounts } from "@/api/types"
import { ErrorNote } from "@/components/ErrorNote"
import { FlagChip } from "@/components/FlagChip"
import { ScoreCell } from "@/components/ScoreCell"
import { ScrollStrip } from "@/components/ScrollStrip"
import { Button, buttonVariants } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { relativeDay, STATUS_LABELS } from "@/lib/format"
import { filterJobs, fullDay, monthYear, SOURCE_LABELS, sortJobs, sourceLabel, type SortDir, type SortKey } from "@/lib/jobs"
import { TABLE_COLUMNS } from "@/lib/search-options"
import { cn } from "@/lib/utils"
import { JobRowDetails } from "./JobRowDetails"
import type { JobsLoad } from "./useJobs"

/** The tabs, in this order; "Archived" and "Below threshold" sit in the More menu. */
const TAB_ORDER: Status[] = ["to_review", "interested", "applied", "interviewing", "offer", "rejected", "not_interested"]
const MORE_VIEWS: { view: "archived" | "below"; label: string }[] = [
  { view: "archived", label: "Archived" },
  { view: "below", label: "Below threshold" },
]

function viewLabel(view: View): string {
  return view === "archived" ? "Archived" : view === "below" ? "Below threshold" : STATUS_LABELS[view]
}

const HEADERS = Object.fromEntries(TABLE_COLUMNS.map((c) => [c.value, c.label])) as Record<TableColumn, string>
const SORTABLE: Partial<Record<TableColumn, SortKey>> = { score: "score", posted: "posted", starts: "starts" }
/** A header click on a new column starts here: best score, newest posting, soonest start. */
const FIRST_DIR: Record<SortKey, SortDir> = { score: "desc", posted: "desc", starts: "asc" }

/** Columns beside score and role are hidden on phones; the role cell shows their content instead. */
const WIDE_ONLY = "hidden md:table-cell"

/** "Amsterdam, North Holland, Netherlands" → "Amsterdam, Netherlands". */
function shortPlace(location: string): string {
  const parts = location.split(",").map((p) => p.trim()).filter(Boolean)
  return parts.length > 2 ? `${parts[0]}, ${parts[parts.length - 1]}` : parts.join(", ")
}

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1)
}

// --- actions ------------------------------------------------------------------

export interface RowHandlers {
  onMove: (job: JobOut, to: Status) => void
  onRestore: (job: JobOut) => void
  onKeep: (job: JobOut) => void
}

function RowActions({ job, view, onMove, onRestore, onKeep }: { job: JobOut; view: View } & RowHandlers) {
  const open = job.url ? (
    <a
      href={job.url}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(buttonVariants({ variant: "outline", size: "sm" }), "bg-card")}
    >
      Open
      <span className="sr-only"> {job.title} (opens in a new tab)</span>
    </a>
  ) : null
  if (view === "archived" || view === "below") {
    return (
      <div className="flex items-center gap-2">
        {open}
        <Button
          variant="outline"
          size="sm"
          className="bg-card"
          onClick={() => (view === "archived" ? onRestore(job) : onKeep(job))}
        >
          {view === "archived" ? "Restore" : "Keep anyway"}
        </Button>
      </div>
    )
  }
  return (
    <div className="flex items-center gap-2">
      {open}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" className="bg-card">
            Move to…
            <ChevronDownIcon aria-hidden="true" className="text-muted-foreground" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-auto">
          {TAB_ORDER.filter((s) => s !== job.status).map((s) => (
            <DropdownMenuItem key={s} onSelect={() => onMove(job, s)}>
              {STATUS_LABELS[s]}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      {job.status !== "not_interested" && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Not interested"
              onClick={() => onMove(job, "not_interested")}
            >
              <XIcon aria-hidden="true" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Not interested</TooltipContent>
        </Tooltip>
      )}
    </div>
  )
}

// --- one row ----------------------------------------------------------------------

function Dash() {
  return (
    <span className="text-muted-foreground">
      <span aria-hidden="true">–</span>
      <span className="sr-only">Not given</span>
    </span>
  )
}

function Why({ job, view }: { job: JobOut; view: View }) {
  return (
    <div className="flex flex-col items-start gap-1">
      {view === "archived" && job.archived_reason && (
        <p className="text-13">
          <span className="font-medium">Archived:</span> {job.archived_reason}
        </p>
      )}
      {job.reason && (
        <p data-reason className="line-clamp-2 text-13">
          {job.reason}
        </p>
      )}
      {job.flags.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {job.flags.map((f) => (
            <FlagChip key={f} text={f} />
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * Whether the reason shown in the row (two lines at most) was cut short, measured while the row is open: the
 * details then repeat it in full. Of the two copies (phone and wide layout) only the visible one has a height.
 */
function useReasonCut(rowRef: RefObject<HTMLTableRowElement | null>, active: boolean): boolean {
  const [cut, setCut] = useState(false)
  useLayoutEffect(() => {
    const row = rowRef.current
    if (!active || !row) return
    const measure = () =>
      setCut(
        Array.from(row.querySelectorAll<HTMLElement>("[data-reason]")).some(
          (p) => p.clientHeight > 0 && p.scrollHeight > p.clientHeight + 1,
        ),
      )
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(row)
    return () => observer.disconnect()
  }, [rowRef, active])
  return cut
}

function JobRow({
  job,
  view,
  columns,
  expanded,
  onToggle,
  handlers,
}: {
  job: JobOut
  view: View
  columns: TableColumn[]
  expanded: boolean
  onToggle: () => void
  handlers: RowHandlers
}) {
  const detailsId = useId()
  const rowRef = useRef<HTMLTableRowElement>(null)
  const reasonCut = useReasonCut(rowRef, expanded)
  const place = shortPlace(job.location)
  const starts = monthYear(job.start_date)
  const posted = relativeDay(job.posted)

  // Clicking anywhere on the row opens it; its links, buttons and menus keep their own clicks.
  const onRowClick = (e: MouseEvent<HTMLTableRowElement>) => {
    if ((e.target as HTMLElement).closest("a, button, input, [role='menuitem'], [role='menu']")) return
    onToggle()
  }

  const cell = (col: TableColumn) => {
    switch (col) {
      case "score":
        return (
          <TableCell key={col} className="w-12 py-3 align-top">
            <ScoreCell score={job.score} />
          </TableCell>
        )
      case "role":
        return (
          <TableCell key={col} className="min-w-0 py-3 align-top whitespace-normal md:min-w-56">
            <button
              type="button"
              className="rounded-sm text-left font-semibold outline-none focus-visible:ring-3 focus-visible:ring-ring"
              aria-expanded={expanded}
              aria-controls={expanded ? detailsId : undefined}
              onClick={onToggle}
            >
              {job.title}
            </button>
            <p className="text-13 text-muted-foreground">{[job.company, place].filter(Boolean).join(" · ")}</p>
            {/* Phones: the other columns' content, then the actions. */}
            <div className="mt-2 flex flex-col gap-2 md:hidden">
              {columns.includes("why") && <Why job={job} view={view} />}
              {(starts || posted) && (
                <p className="text-13 text-muted-foreground">
                  {capitalise([starts && `starts ${starts}`, posted && `posted ${posted}`].filter(Boolean).join(", "))}
                </p>
              )}
              <RowActions job={job} view={view} {...handlers} />
            </div>
          </TableCell>
        )
      case "why":
        return (
          <TableCell key={col} className={cn(WIDE_ONLY, "max-w-sm min-w-48 py-3 align-top whitespace-normal")}>
            <Why job={job} view={view} />
          </TableCell>
        )
      case "starts":
        return (
          <TableCell key={col} className={cn(WIDE_ONLY, "py-3 align-top")}>
            {starts || <Dash />}
          </TableCell>
        )
      case "posted":
        return (
          <TableCell key={col} className={cn(WIDE_ONLY, "py-3 align-top")}>
            {posted || <Dash />}
          </TableCell>
        )
      case "deadline":
        return (
          <TableCell key={col} className={cn(WIDE_ONLY, "py-3 align-top")}>
            {fullDay(job.deadline) || <Dash />}
          </TableCell>
        )
      case "source":
        return (
          <TableCell key={col} className={cn(WIDE_ONLY, "py-3 align-top")}>
            {sourceLabel(job.source)}
          </TableCell>
        )
    }
  }

  return (
    <>
      <TableRow ref={rowRef} className={cn("cursor-pointer", expanded && "border-b-0 bg-card!")} onClick={onRowClick}>
        {columns.map(cell)}
        <TableCell className={cn(WIDE_ONLY, "py-3 align-top")}>
          <RowActions job={job} view={view} {...handlers} />
        </TableCell>
      </TableRow>
      {expanded && (
        <TableRow className="bg-card!">
          {/* An empty cell under the score, so the details line up with the role. */}
          <TableCell />
          <TableCell colSpan={columns.length} className="pt-0 pb-4 whitespace-normal">
            <JobRowDetails job={job} id={detailsId} showReason={!columns.includes("why") || reasonCut} />
          </TableCell>
        </TableRow>
      )}
    </>
  )
}

// --- the table and its controls ----------------------------------------------------------

interface ResultsTableProps extends RowHandlers {
  view: View
  onViewChange: (view: View) => void
  counts: ViewCounts | null
  load: JobsLoad
  onRetry: () => void
  columns: TableColumn[]
  defaultSort: SortKey
}

export function ResultsTable(props: ResultsTableProps) {
  const { view, onViewChange, counts, load, onRetry, columns, defaultSort, ...handlers } = props
  const [text, setText] = useState("")
  const [source, setSource] = useState("all")
  const [minScore, setMinScore] = useState("")
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: defaultSort, dir: FIRST_DIR[defaultSort] })
  const [expanded, setExpanded] = useState<string | null>(null)

  const cols = useMemo(() => {
    const picked = columns.filter((c) => c in HEADERS)
    // Score and role are what a row is: always shown, first.
    return [...(["score", "role"] as const), ...picked.filter((c) => c !== "score" && c !== "role")]
  }, [columns])

  const minValue = minScore.trim() === "" ? null : Number(minScore)
  const filter = { text, source, minScore: minValue !== null && Number.isFinite(minValue) ? minValue : null }
  const filtering = filter.text.trim() !== "" || filter.source !== "all" || filter.minScore !== null
  const rows = load.kind === "ready" ? sortJobs(filterJobs(load.jobs, filter), sort.key, sort.dir) : []

  const clearFilters = () => {
    setText("")
    setSource("all")
    setMinScore("")
  }

  const onSort = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: FIRST_DIR[key] }))

  const isMore = view === "archived" || view === "below"
  const count = (v: View) => (counts ? <span className="num text-xs text-muted-foreground">{counts[v]}</span> : null)

  return (
    <Tabs value={view} onValueChange={(v) => onViewChange(v as View)} className="gap-3">
      <div className="flex items-center gap-4 border-b">
        {/* The active tab's line sits inside the strip (bottom-0), so nothing overflows downwards. */}
        <ScrollStrip>
          <TabsList variant="line" aria-label="Job lists" className="h-10 justify-start gap-4 p-0">
            {TAB_ORDER.map((s) => (
              <TabsTrigger
                key={s}
                value={s}
                className="h-full flex-none px-0 text-13 group-data-horizontal/tabs:after:bottom-0"
              >
                {STATUS_LABELS[s]} {count(s)}
              </TabsTrigger>
            ))}
          </TabsList>
        </ScrollStrip>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className={cn("-mx-2 shrink-0 text-13 text-foreground/60", isMore && "text-foreground")}
            >
              {isMore ? viewLabel(view) : "More"} {isMore && count(view)}
              <ChevronDownIcon aria-hidden="true" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-auto">
            {MORE_VIEWS.map((m) => (
              <DropdownMenuItem key={m.view} onSelect={() => onViewChange(m.view)} className="justify-between gap-4">
                {m.label} {count(m.view)}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <TabsContent
        value={view}
        className="flex flex-col gap-3"
        // Archived and Below threshold have no tab to be labelled by: they name themselves.
        {...(isMore ? { "aria-labelledby": undefined, "aria-label": viewLabel(view) } : {})}
      >
        {view === "archived" && (
          <p className="text-13 text-muted-foreground">Jobs that cleanup put away. Restore one to bring it back.</p>
        )}
        {view === "below" && (
          <p className="text-13 text-muted-foreground">
            Jobs that scored under your minimum. Keep one to move it to Interested.
          </p>
        )}

        <div className="flex flex-wrap items-center gap-2" role="search" aria-label="Filter jobs">
          <Input
            type="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Filter by role, company or place"
            aria-label="Filter by role, company or place"
            className="h-8 w-full bg-card sm:w-72"
          />
          <Select value={source} onValueChange={setSource}>
            <SelectTrigger size="sm" aria-label="Source" className="bg-card">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All</SelectItem>
              {Object.entries(SOURCE_LABELS)
                .filter(([value]) => value !== "sample")
                .map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
            </SelectContent>
          </Select>
          <Input
            type="number"
            inputMode="numeric"
            min={0}
            max={100}
            value={minScore}
            onChange={(e) => setMinScore(e.target.value)}
            placeholder="Min score"
            aria-label="Minimum score"
            className="num h-8 w-32 bg-card placeholder:font-sans"
          />
        </div>

        {load.kind === "loading" && (
          <p className="py-6 text-muted-foreground" aria-live="polite">
            Loading jobs…
          </p>
        )}
        {load.kind === "error" && <ErrorNote message={load.message} onRetry={onRetry} />}
        {load.kind === "ready" && load.jobs.length === 0 && (
          <p className="py-6 text-muted-foreground">Nothing in {viewLabel(view)} yet.</p>
        )}
        {load.kind === "ready" && load.jobs.length > 0 && rows.length === 0 && (
          <div className="flex flex-wrap items-center gap-3 py-6">
            <p className="text-muted-foreground">No jobs match these filters.</p>
            {filtering && (
              <Button variant="outline" size="sm" className="bg-card" onClick={clearFilters}>
                Clear filters
              </Button>
            )}
          </div>
        )}
        {rows.length > 0 && (
          <Table className="table-auto">
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                {cols.map((c) => {
                  const key = SORTABLE[c]
                  const active = key !== undefined && sort.key === key
                  return (
                    <TableHead
                      key={c}
                      className={cn("h-8 text-xs font-medium text-muted-foreground", c !== "score" && c !== "role" && WIDE_ONLY)}
                      aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}
                    >
                      {key ? (
                        <button
                          type="button"
                          onClick={() => onSort(key)}
                          className={cn(
                            "inline-flex items-center gap-1 rounded-sm outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring",
                            active && "text-foreground",
                          )}
                        >
                          {HEADERS[c]}
                          {active &&
                            (sort.dir === "asc" ? (
                              <ArrowUpIcon className="size-3" aria-hidden="true" />
                            ) : (
                              <ArrowDownIcon className="size-3" aria-hidden="true" />
                            ))}
                        </button>
                      ) : (
                        HEADERS[c]
                      )}
                    </TableHead>
                  )
                })}
                <TableHead className={cn(WIDE_ONLY, "h-8")}>
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((job) => (
                <JobRow
                  key={job.pk}
                  job={job}
                  view={view}
                  columns={cols}
                  expanded={expanded === job.pk}
                  onToggle={() => setExpanded((e) => (e === job.pk ? null : job.pk))}
                  handlers={handlers}
                />
              ))}
            </TableBody>
          </Table>
        )}
      </TabsContent>
    </Tabs>
  )
}
