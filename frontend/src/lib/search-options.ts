import type { IndeedJobType, LinkedInSettings, PostingLanguage, TableColumn, UiSettings } from "@/api/types"

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

/** The languages the app can recognise a posting in, as the language filter lists them. */
export const POSTING_LANGUAGES: { value: PostingLanguage; label: string }[] = [
  { value: "en", label: "English" },
  { value: "it", label: "Italian" },
  { value: "de", label: "German" },
  { value: "fr", label: "French" },
  { value: "nl", label: "Dutch" },
  { value: "es", label: "Spanish" },
  { value: "pt", label: "Portuguese" },
  { value: "sv", label: "Swedish" },
  { value: "da", label: "Danish" },
  { value: "no", label: "Norwegian" },
  { value: "pl", label: "Polish" },
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
