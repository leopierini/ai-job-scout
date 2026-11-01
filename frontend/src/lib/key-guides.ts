/** What each KeyGuide says: numbered steps for getting one key, and the page to open. */

export type KeyGuideProvider = "anthropic" | "openai" | "gemini" | "openrouter" | "apify" | "notion"

interface Guide {
  title: string
  steps: string[]
  link: { label: string; href: string }
}

// Links checked on 2026-11-01 (each one resolves to the provider's key page or its sign-in).
export const KEY_GUIDES: Record<KeyGuideProvider, Guide> = {
  anthropic: {
    title: "How to get an Anthropic API key",
    steps: [
      "Sign in to the Claude Platform. API use is billed separately from a Claude subscription, so add credit under Billing first.",
      "Open Settings, then API keys, and choose Create key.",
      "Copy the key (it starts with sk-ant-) and paste it here.",
    ],
    link: { label: "Open Claude Platform API keys", href: "https://platform.claude.com/settings/keys" },
  },
  openai: {
    title: "How to get an OpenAI API key",
    steps: [
      "Sign in to the OpenAI platform and add a payment method under Billing.",
      "Open API keys and choose Create new secret key.",
      "Copy the key (it starts with sk-) and paste it here. OpenAI shows it only once.",
    ],
    link: { label: "Open OpenAI API keys", href: "https://platform.openai.com/api-keys" },
  },
  gemini: {
    title: "How to get a Gemini API key",
    steps: [
      "Sign in to Google AI Studio with a Google account.",
      "Choose Get API key, then Create API key.",
      "Copy the key and paste it here.",
    ],
    link: { label: "Open Google AI Studio", href: "https://aistudio.google.com/apikey" },
  },
  openrouter: {
    title: "How to get an OpenRouter API key",
    steps: [
      "Sign in to OpenRouter and add credits.",
      "Open Keys and choose Create key.",
      "Copy the key (it starts with sk-or-) and paste it here.",
    ],
    link: { label: "Open OpenRouter keys", href: "https://openrouter.ai/settings/keys" },
  },
  apify: {
    title: "How to get your Apify token",
    steps: [
      "Sign up at Apify. The free plan includes $5 of usage a month.",
      "In the Apify Console, open Settings, then API & Integrations.",
      "Copy your personal API token and paste it here.",
    ],
    link: { label: "Open Apify Console", href: "https://console.apify.com/settings/integrations" },
  },
  notion: {
    title: "How to connect Notion",
    steps: [
      "Create an integration at notion.so/my-integrations and copy its secret.",
      "In Notion, open the page that should hold your jobs. In its … menu, choose Connections and add your integration.",
      "Paste the secret here.",
    ],
    link: { label: "Open Notion integrations", href: "https://www.notion.so/my-integrations" },
  },
}
