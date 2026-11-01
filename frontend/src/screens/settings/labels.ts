import type { KeyStatus, RescorePreview } from "@/api/types"

/** "Set (…wxyz)", "Set" (a short key shows nothing of itself) or "Not set". A saved key is never shown. */
export function keyStatus(k: KeyStatus): string {
  if (!k.set) return "Not set"
  return k.hint ? `Set (${k.hint})` : "Set"
}

const tokens = new Intl.NumberFormat("en-US")

/** "12 jobs will be scored again (about 48,000 tokens). Apify isn't used." */
export function rescoreLine(p: RescorePreview): string {
  const jobs = p.count === 1 ? "1 job will be" : `${p.count} jobs will be`
  return `${jobs} scored again (about ${tokens.format(p.approx_tokens)} tokens). Apify isn't used.`
}
