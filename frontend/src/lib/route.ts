import { useMemo, useSyncExternalStore } from "react"

/**
 * Hash routes: #/ (main), #/setup, #/settings/<tab>, #/history, #/history/<id>.
 * A query after "?" is allowed, e.g. #/setup?step=3.
 */
export interface Route {
  /** The path without the query, always starting with "/": "/settings/profile". */
  path: string
  /** The path's segments: ["settings", "profile"]; [] for "/". */
  params: string[]
  query: URLSearchParams
}

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#/, "")
  const q = raw.indexOf("?")
  const pathPart = q === -1 ? raw : raw.slice(0, q)
  const params = pathPart
    .split("/")
    .filter(Boolean)
    .map((s) => {
      try {
        return decodeURIComponent(s)
      } catch {
        return s
      }
    })
  return {
    path: "/" + params.join("/"),
    params,
    query: new URLSearchParams(q === -1 ? "" : raw.slice(q + 1)),
  }
}

// One hashchange listener for the whole app, so a leave guard can stop a change before any screen sees it.
type Guard = (target: string) => boolean
let guard: Guard | null = null
let accepted: string | null = null // the hash the screens show; null until first read
const listeners = new Set<() => void>()

function currentHash(): string {
  if (accepted === null) accepted = window.location.hash
  return accepted
}

function onHashChange(): void {
  const target = window.location.hash
  if (target === currentHash()) return
  if (guard && !guard(target)) {
    // Stay: put the address back without a new history entry. The guard decides what to tell the user.
    history.replaceState(history.state, "", currentHash() || window.location.pathname + window.location.search)
    return
  }
  accepted = target
  listeners.forEach((l) => l())
}

function subscribe(onChange: () => void): () => void {
  if (listeners.size === 0) {
    accepted = window.location.hash // nothing was listening: start from the address as it is now
    window.addEventListener("hashchange", onHashChange)
  }
  listeners.add(onChange)
  return () => {
    listeners.delete(onChange)
    if (listeners.size === 0) window.removeEventListener("hashchange", onHashChange)
  }
}

/**
 * Ask before leaving: while a guard is set, every change of address (a link, Back, `navigate`) first asks it.
 * It returns true to let the change happen, or false to stay (and, say, open "Discard unsaved changes?").
 * Returns a function that removes the guard.
 */
export function setLeaveGuard(check: Guard): () => void {
  guard = check
  return () => {
    if (guard === check) guard = null
  }
}

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribe, currentHash)
  return useMemo(() => parseHash(hash), [hash])
}

/** Go to a route, e.g. navigate("/settings/keys") or navigate("/setup?step=2"). */
export function navigate(path: string): void {
  const target = "#" + (path.replace(/^#/, "") || "/")
  if (window.location.hash !== target) window.location.hash = target
}
