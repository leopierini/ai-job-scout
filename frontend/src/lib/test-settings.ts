import type { ApifyBoardSettings, Settings } from "@/api/types"

const board = (actor: string): ApifyBoardSettings => ({
  enabled: false,
  actor,
  queries: [],
  locations: [],
  max_results_per_search: 25,
  price_per_result: 0.001,
})

/** The startup sources, all off: spread into a test's Settings. */
export const OFF_STARTUP_SOURCES: Pick<
  Settings,
  "builtineurope" | "jobgether" | "vcboards" | "careers" | "wttj" | "wellfound" | "yc" | "atsindex" | "startup_places" | "startup_queries"
> = {
  startup_places: [],
  startup_queries: [],
  builtineurope: { enabled: false, queries: [], countries: [], max_results_per_query: 50 },
  jobgether: { enabled: false, queries: [], locations: [], experience: [], max_results_per_query: 50 },
  vcboards: { enabled: false, boards: [], queries: [], locations: [], max_results_per_board: 100 },
  careers: { enabled: false, pages: [], title_words: [], locations: [] },
  wttj: board("w"),
  wellfound: board("f"),
  yc: board("y"),
  atsindex: board("a"),
}
