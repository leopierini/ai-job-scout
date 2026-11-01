/** Lists typed or pasted as text: "a, b\nc" → ["a", "b", "c"]. */

/** Split text on commas and line breaks; trim each item and drop the empty ones. */
export function splitList(text: string): string[] {
  return text
    .split(/[,\r\n]+/)
    .map((s) => s.trim())
    .filter(Boolean)
}

/** `list` plus each of `items` it doesn't hold yet (ignoring case), in order. */
export function addUnique(list: string[], items: string[]): string[] {
  const seen = new Set(list.map((s) => s.toLowerCase()))
  const out = [...list]
  for (const item of items) {
    const key = item.toLowerCase()
    if (!seen.has(key)) {
      seen.add(key)
      out.push(item)
    }
  }
  return out
}
