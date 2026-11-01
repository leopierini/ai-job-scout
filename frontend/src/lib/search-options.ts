import type { IndeedJobType, LinkedInSettings, TableColumn, UiSettings } from "@/api/types"

/** Indeed's job types, as the wizard and Settings list them. */
export const JOB_TYPES: { value: IndeedJobType; label: string }[] = [
  { value: "fulltime", label: "Full time" },
  { value: "parttime", label: "Part time" },
  { value: "internship", label: "Internship" },
  { value: "contract", label: "Contract" },
  { value: "temporary", label: "Temporary" },
  { value: "permanent", label: "Permanent" },
  { value: "seasonal", label: "Seasonal" },
  { value: "freelance", label: "Freelance" },
]

/** LinkedIn's sort orders. The empty value (LinkedIn's own default) is "default" in a select, which can't hold "". */
export const LINKEDIN_SORTS: { value: LinkedInSettings["sort_by"]; label: string }[] = [
  { value: "DD", label: "Most recent" },
  { value: "R", label: "Most relevant" },
  { value: "", label: "LinkedIn's default" },
]

/** The results table's columns in the order the table shows them, with their headers. */
export const TABLE_COLUMNS: { value: TableColumn; label: string }[] = [
  { value: "score", label: "Fit" },
  { value: "role", label: "Role" },
  { value: "why", label: "Why it fits" },
  { value: "starts", label: "Starts" },
  { value: "posted", label: "Posted" },
  { value: "deadline", label: "Deadline" },
  { value: "source", label: "Source" },
]

export const TABLE_SORTS: { value: UiSettings["default_sort"]; label: string }[] = [
  { value: "score", label: "Best fit first" },
  { value: "posted", label: "Newest first" },
  { value: "starts", label: "Starting soonest" },
]
