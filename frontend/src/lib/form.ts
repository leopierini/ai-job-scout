/**
 * Editing a copy of saved values: a draft that knows whether it differs from what was loaded, set by dotted
 * paths ("scoring.min_score", "scoring.dealbreakers.0.text"), and the server's 422 messages by the same paths.
 */
import { useCallback, useMemo, useReducer } from "react"

// --- paths -------------------------------------------------------------------

type Indexable = Record<string, unknown> | unknown[]

function child(container: unknown, key: string): unknown {
  if (container === null || typeof container !== "object") return undefined
  return (container as Record<string, unknown>)[key]
}

/** The value at a dotted path, or undefined when part of the path is missing. */
export function getIn(obj: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>((at, key) => child(at, key), obj)
}

/** A copy of `obj` with `value` at `path`. Only the objects along the path are copied; the rest is shared. */
export function setIn<T>(obj: T, path: string, value: unknown): T {
  const [key, ...rest] = path.split(".")
  const current = child(obj, key)
  const next = rest.length ? setIn(current ?? {}, rest.join("."), value) : value
  if (Array.isArray(obj)) {
    const copy = [...obj] as unknown[]
    copy[Number(key)] = next
    return copy as T
  }
  return { ...(obj as Record<string, unknown>), [key]: next } as T
}

/** `target` with each of `paths` taken from `source`: saving one tab's fields over freshly loaded settings. */
export function mergePaths<T>(target: T, source: T, paths: readonly string[]): T {
  return paths.reduce((acc, path) => setIn(acc, path, getIn(source, path)), target)
}

/** Deep equality for plain JSON-like values (objects, arrays, strings, numbers, booleans, null). */
export function isEqual(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true
  if (a === null || b === null || typeof a !== "object" || typeof b !== "object") return false
  if (Array.isArray(a) !== Array.isArray(b)) return false
  const ka = Object.keys(a as Indexable)
  const kb = Object.keys(b as Indexable)
  if (ka.length !== kb.length) return false
  return ka.every((k) => Object.hasOwn(b, k) && isEqual(child(a, k), child(b, k)))
}

// --- the draft ---------------------------------------------------------------

export interface DraftState<T> {
  /** What was last loaded or saved: the clean state. */
  base: T
  value: T
  dirty: boolean
}

export type DraftAction<T> =
  | { type: "set"; path: string; value: unknown }
  /** Back to the saved value, or to `next` when a save just stored it. */
  | { type: "reset"; next?: T }
  /** The server changed some saved values (e.g. connecting Notion): take them, keep every other edit. */
  | { type: "rebase"; base: T; paths: readonly string[] }

export function initDraft<T>(loaded: T): DraftState<T> {
  return { base: loaded, value: loaded, dirty: false }
}

export function draftReducer<T>(state: DraftState<T>, action: DraftAction<T>): DraftState<T> {
  switch (action.type) {
    case "set": {
      const value = setIn(state.value, action.path, action.value)
      return { ...state, value, dirty: !isEqual(value, state.base) }
    }
    case "reset":
      return initDraft(action.next ?? state.base)
    case "rebase": {
      const value = mergePaths(state.value, action.base, action.paths)
      return { base: action.base, value, dirty: !isEqual(value, action.base) }
    }
  }
}

export interface Draft<T> {
  /** The saved value the draft started from. */
  base: T
  value: T
  set: (path: string, value: unknown) => void
  dirty: boolean
  /** Throw away the edits; with `next`, that becomes the saved value (after a save). */
  reset: (next?: T) => void
  rebase: (base: T, paths: readonly string[]) => void
}

/** A draft of `loaded` (read once, when the component mounts). */
export function useDraft<T>(loaded: T): Draft<T> {
  const [state, dispatch] = useReducer(draftReducer<T>, loaded, initDraft<T>)
  const set = useCallback((path: string, value: unknown) => dispatch({ type: "set", path, value }), [])
  const reset = useCallback((next?: T) => dispatch({ type: "reset", next }), [])
  const rebase = useCallback((base: T, paths: readonly string[]) => dispatch({ type: "rebase", base, paths }), [])
  return useMemo(
    () => ({ base: state.base, value: state.value, dirty: state.dirty, set, reset, rebase }),
    [state.base, state.value, state.dirty, set, reset, rebase],
  )
}

// --- errors and inputs -------------------------------------------------------

/** The server's 422 `fields`: a dotted path ("scoring.min_score") to a message. */
export type FieldErrors = Record<string, string> | null

/** The server's message for `path` or anything under it ("scoring.dealbreakers" finds "scoring.dealbreakers.1.text"). */
export function fieldError(fields: FieldErrors, path: string): string | undefined {
  if (!fields) return undefined
  for (const [key, message] of Object.entries(fields)) {
    if (key === path || key.startsWith(path + ".")) return message
  }
  return undefined
}

/** A number box's text as a value: empty is null (the server says if that's allowed), anything else a number. */
export function numberOrNull(text: string): number | null {
  return text.trim() === "" ? null : Number(text)
}

// --- defaults ----------------------------------------------------------------

/** The score bands settings.py ships with (DEFAULT_BANDS); a test keeps the two the same. */
export const DEFAULT_BANDS = [
  "85-100: an excellent match for the target roles, level and location in the profile, with no dealbreaker.",
  "70-84: a good match with minor gaps (adjacent title, slightly different focus, less known company).",
  "40-69: a partial match: wrong focus, level a bit off, or notable gaps.",
  "0-39: clearly not a match, or a dealbreaker applies.",
].join("\n")
