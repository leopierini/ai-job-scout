import type { ProviderId, SecretName } from "@/api/types"
import { KEY_GUIDES, type KeyGuideProvider } from "@/lib/key-guides"

/** An AI provider the user can pick (the test-only "fake" one is left out). */
export interface ProviderInfo {
  id: Exclude<ProviderId, "fake">
  label: string
  /** One line under the label: what it is and what it costs. */
  note: string
  needsKey: boolean
  /** The secret that holds its API key; null when it needs none. */
  keyField: SecretName | null
  /** The page where the key is made (the KeyGuide's link); null when it needs none. */
  keyUrl: string | null
  /** Which KeyGuide explains getting the key; null when it needs none. */
  guide: KeyGuideProvider | null
  /** Model ids to suggest; the user can type any other. Empty means free text only. */
  models: string[]
  /** Shown in the model field when there's nothing to suggest. */
  modelPlaceholder?: string
}

export const PROVIDERS: ProviderInfo[] = [
  {
    id: "anthropic",
    label: "Anthropic",
    note: "Claude through the API. Paid per use, billed separately from a Claude subscription.",
    needsKey: true,
    keyField: "anthropic_api_key",
    keyUrl: KEY_GUIDES.anthropic.link.href,
    guide: "anthropic",
    models: ["claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5"],
  },
  {
    id: "openai",
    label: "OpenAI",
    note: "GPT models through the API. Paid per use.",
    needsKey: true,
    keyField: "openai_api_key",
    keyUrl: KEY_GUIDES.openai.link.href,
    guide: "openai",
    // checked 2026-11-01 (developers.openai.com/api/docs/models: Astra is the suggested start, Sol mid, Luna smallest)
    models: ["gpt-6.1-sol", "gpt-6-luna", "gpt-6-astra"],
  },
  {
    id: "gemini",
    label: "Google Gemini",
    note: "Gemini models through Google AI Studio. Has a free tier with limits.",
    needsKey: true,
    keyField: "gemini_api_key",
    keyUrl: KEY_GUIDES.gemini.link.href,
    guide: "gemini",
    // checked 2026-11-01 (ai.google.dev/gemini-api/docs/models, stable models)
    models: ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-4-argon"],
  },
  {
    id: "openrouter",
    label: "OpenRouter",
    note: "One key for models from many companies. Paid per use.",
    needsKey: true,
    keyField: "openrouter_api_key",
    keyUrl: KEY_GUIDES.openrouter.link.href,
    guide: "openrouter",
    // checked 2026-11-01 (openrouter.ai/api/v1/models)
    models: ["openai/gpt-6.1-sol", "google/gemini-3.8-flash", "anthropic/claude-sonnet-5.5"],
  },
  {
    id: "ollama",
    label: "Ollama",
    note: "Runs a model on this computer. Free, but slower and less accurate than the others.",
    needsKey: false,
    keyField: null,
    keyUrl: null,
    guide: null,
    models: [],
    modelPlaceholder: "the name of a model you pulled, e.g. llama3.1",
  },
  {
    id: "claude_code",
    label: "Claude Code",
    note: "Runs through the claude command on this computer. No API key needed.",
    needsKey: false,
    keyField: null,
    keyUrl: null,
    guide: null,
    models: ["sonnet", "haiku", "opus"],
  },
]

export function providerInfo(id: ProviderId): ProviderInfo {
  return PROVIDERS.find((p) => p.id === id) ?? PROVIDERS[0]
}

/** The label in the wizard's list; Claude Code says when it was found. */
export function providerLabel(p: ProviderInfo, claudeCodeFound: boolean): string {
  if (p.id === "claude_code" && claudeCodeFound) return "Claude Code (found on this computer, uses your Claude subscription)"
  return p.label
}

/** The list as the wizard shows it: Claude Code first when it was found on this computer. */
export function providersInOrder(claudeCodeFound: boolean): ProviderInfo[] {
  if (!claudeCodeFound) return PROVIDERS
  return [...PROVIDERS.filter((p) => p.id === "claude_code"), ...PROVIDERS.filter((p) => p.id !== "claude_code")]
}
