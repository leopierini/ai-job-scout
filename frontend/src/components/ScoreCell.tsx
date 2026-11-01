import { cn } from "@/lib/utils"

/** Scores at or above this get the accent colour. */
export const HIGH_SCORE = 85

/** A fit score in tabular figures; accent at 85 and up, a dash when the job has no score. */
export function ScoreCell({ score }: { score: number | null }) {
  if (score === null) {
    return (
      <span className="num text-base text-muted-foreground">
        <span aria-hidden="true">–</span>
        <span className="sr-only">Not scored</span>
      </span>
    )
  }
  return (
    <span className={cn("num text-base font-medium", score >= HIGH_SCORE && "text-primary")}>
      <span className="sr-only">Fit score </span>
      {score}
    </span>
  )
}
