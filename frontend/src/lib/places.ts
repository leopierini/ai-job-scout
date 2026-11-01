/** Place names: which ones are countries, and how Indeed's country codes read. */

const norm = (s: string) => s.trim().replace(/\s+/g, " ").toLowerCase()

/**
 * Split the places to avoid into countries and cities. A name that matches one of LinkedIn's region names
 * (ignoring case and spaces) is a country, written as the region list spells it; everything else is a city.
 */
export function splitPlaces(names: string[], regionNames: string[]): { countries: string[]; cities: string[] } {
  const regions = new Map(regionNames.map((r) => [norm(r), r]))
  const countries: string[] = []
  const cities: string[] = []
  for (const name of names) {
    const key = norm(name)
    if (!key) continue
    const region = regions.get(key)
    if (region) countries.push(region)
    else cities.push(name.trim())
  }
  return { countries, cities }
}

let displayNames: Intl.DisplayNames | null | undefined

/** "de" → "Germany". Indeed says "uk" where ISO says "gb". An unknown code is shown upper-cased. */
export function indeedCountryName(code: string): string {
  const iso = code.toLowerCase() === "uk" ? "GB" : code.toUpperCase()
  if (displayNames === undefined) {
    try {
      displayNames = new Intl.DisplayNames(["en"], { type: "region", fallback: "none" })
    } catch {
      displayNames = null
    }
  }
  try {
    return displayNames?.of(iso) ?? code.toUpperCase()
  } catch {
    return code.toUpperCase() // not a well-formed region code
  }
}
