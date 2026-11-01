import type { ErrorBody } from "./types"

/** The server refuses any change that doesn't carry this header (see server/security.py). */
export const APP_HEADER = { "X-Requested-By": "ai-job-scout" } as const

export const OFFLINE_MESSAGE =
  "Can't reach AI Job Scout. Check that it's still running in its terminal window, then try again."
const FALLBACK_MESSAGE = "Something went wrong. Check the terminal window for details."

/** A non-2xx response, or no response at all (status 0). */
export class ApiError extends Error {
  readonly status: number
  readonly fields: Record<string, string> | null

  constructor(status: number, message: string, fields: Record<string, string> | null = null) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.fields = fields
  }
}

async function errorFrom(res: Response): Promise<ApiError> {
  try {
    const body = (await res.json()) as Partial<ErrorBody>
    if (typeof body.error === "string" && body.error) {
      return new ApiError(res.status, body.error, body.fields ?? null)
    }
  } catch {
    // Not JSON: fall through to the plain message.
  }
  return new ApiError(res.status, FALLBACK_MESSAGE)
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { ...APP_HEADER }
  if (body !== undefined) headers["Content-Type"] = "application/json"
  let res: Response
  try {
    res = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, OFFLINE_MESSAGE)
  }
  if (!res.ok) throw await errorFrom(res)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  send: <T>(method: "POST" | "PUT" | "PATCH" | "DELETE", path: string, body?: unknown) =>
    request<T>(method, path, body),
}
