---
name: AI Job Scout
colors:
  bg: "#F6F7F5"
  surface: "#FFFFFF"
  text: "#1B1F1D"
  text-muted: "#5E6762"
  border: "#E2E5E1"
  neutral-soft: "#EEF0ED"
  accent: "#1E6A4E"        # primary action, running state, scores ≥ 85 — nothing else
  accent-soft: "#E6F0EB"
  warn: "#9A5B00"
  warn-soft: "#FBF0DD"
  danger: "#A33A2C"
typography:
  family-ui: "Public Sans"
  family-numbers: "IBM Plex Mono"   # tabular figures for scores, money, times, counts
  scale: [12, 13, 14, 16, 20]
  weights: [400, 500, 600]
spacing: [4, 8, 12, 16, 20, 24, 32]
rounded: { sm: 4px, md: 6px, lg: 8px }
shadow: none, except popovers/dialogs: 0 4px 16px rgb(27 31 29 / 0.08)
---

# AI Job Scout design

## Overview

A work tool for one person reviewing jobs: calm, dense, readable. Light background (not white,
not cream), white surfaces, one deep green accent, amber for warnings. Public Sans for words, IBM
Plex Mono with tabular figures for every number the user compares (scores, money, times, counts).
Small radii, hairline borders, no shadows on the page. Light theme only in v1.

**In code** (`frontend/src/index.css` maps the tokens onto shadcn's variables):

| Token | Tailwind |
|---|---|
| bg, surface, text, text-muted, border | `bg-background`, `bg-card`, `text-foreground`, `text-muted-foreground`, `border` |
| accent, accent-soft | `bg-primary` / `text-primary`, `bg-accent-soft` |
| neutral-soft | `bg-neutral-soft` (also shadcn's `muted`, `secondary` and `accent`: the neutral hover surface, **not** the green) |
| warn, warn-soft, danger | `text-warn`, `bg-warn-soft`, `text-destructive` |
| scale 12 / 13 / 14 / 16 / 20 | `text-xs` / `text-13` / `text-sm` (body) / `text-base` / `text-xl` |
| spacing 4 … 32 | `1`, `2`, `3`, `4`, `5`, `6`, `8` (e.g. `gap-2`, `px-4`) |
| rounded sm / md / lg | `rounded-sm` / `rounded-md` / `rounded-lg` (larger ones are capped at 8px) |
| numbers font | the `num` class |

Controls inside the generated shadcn parts keep their own heights and inner padding.

## Layout

Main screen, layout A: the run on the left, the results table as the main area.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ AI Job Scout   Software engineer, early career · Europe and the UK           │
│                         Next run up to $1.80   History  Settings [Find jobs] │
│                         Apify credit left $3.20                              │
├────────────────────────┬─────────────────────────────────────────────────────┤
│ Run started 14:02      │ To review 23  Interested 4  Applied 6  …  More ▾    │
│ 1 Clean up old jobs    │ [Filter by role, company or place] [All ▾] [min]    │
│   12 archived   Done   │ Fit  Role                Why it fits   Starts Posted│
│ 2 Search for jobs      │ 91   Junior Software En… First role at Jan    2 days│
│   UK: 14 of 33 Running │      Kestrel Payments    a payments c… 2027   [Open]│
│   ▬▬▬▬▬▬▬──────  2:31  │ 88   Graduate Software…  Rotational    —      1 week│
│ 3 Remove duplicates    │      Lumen Freight       programme…                 │
│ 4 Score against your … │                                                     │
│ 5 Save results         │                                                     │
│ (300px, surface)       │ (fills the rest, bg)                                │
└────────────────────────┴─────────────────────────────────────────────────────┘
```

- Below 768px the run panel stacks above the table and the top bar wraps.
- Page gutters: 16px on phones, 20px from 768px up.
- Secondary screens (setup, settings, history) use one centred column, at most 768px wide,
  under the same top bar.
- One primary action per screen.

## Components

Built from `frontend/src/components/ui` (shadcn) and composed in `frontend/src/components`:

- **StatusPill:** status in words, 12px semibold, 4px radius, 20px high. Done: accent-soft with
  accent text. Running: accent with white text. Pending: neutral-soft with muted text. Error: danger
  text and a danger hairline. Skipped and Cancelled: muted text and a border hairline.
- **StepItem:** number (mono, muted), title (500), the step's message (12px muted; text colour on
  an error), the pill and the elapsed time (mono) on the right, a 4px progress bar on the running
  step. When it has details it is a button with a chevron and `aria-expanded`.
- **ScoreCell:** mono, 16px, 500. Accent at 85 and up; text colour below; a dash when unscored.
- **FlagChip:** amber text on warn-soft, 12px semibold, 4px radius. Never the accent.
- **EmptyState:** a title, one sentence of what to do, and at most one action. Left aligned, no
  illustration; centred only on the main screen before the first run.
- **ErrorNote:** a danger icon, what happened (500), what to do next (muted), Retry. White surface
  with a hairline border; no coloured side border.
- **KeyGuide:** a collapsible "How to get …" with numbered steps and one link that opens in a new tab.
- **AppBar:** the app name (a link home) and the screen's own items on the right.

Buttons: primary is accent with white text; everything else is outline or ghost. Links are text
colour and underlined, never green.

## States

- **Loading:** specific text, not a spinner alone: "Loading jobs…", "Reading your CV and drafting
  your profile… this can take a minute". No skeleton shimmer.
- **Empty:** say what will be here and the one thing to do: "No jobs yet. Press Find jobs to search
  LinkedIn. A run can cost up to $1.80 on Apify." An empty tab: "Nothing in Applied yet."
- **Error:** what happened, then what to do next, in plain words, with Retry when retrying can help:
  "Apify refused the token. Check it in Settings, then try again." Never a code or a stack trace.
  If the app can't load its state at all, the whole page is an ErrorNote with "Try again".

## Do's and Don'ts

Do:
- Sentence case everywhere, plain words, real-looking (fictional) content.
- Statuses from `STATUS_LABELS` (To review, Interested, Not interested, Applied, Interviewing, Offer,
  Rejected); step pills Pending, Running, Done, Error, Skipped; step titles from `STEP_TITLES`.
- Motion only on a state change (a step finishing, a row moving), and none under
  `prefers-reduced-motion` (the global rule in `index.css` handles it).
- Visible focus rings on everything focusable (`focus-visible:ring-3 ring-ring`, at full strength for 3:1 contrast).
- WCAG AA contrast: body text on bg and surface, muted text at 12px and up, white on accent.
- Follow the [Vercel Web Interface Guidelines](https://github.com/vercel-labs/web-interface-guidelines).

Don't:
- Purple or blue gradients, gradient text, glow, glassmorphism (no backdrop blur).
- Emoji as icons (use lucide), a badge above a headline, three icon cards in a row.
- Coloured side borders, nested cards, fade-in on scroll.
- Inter, Roboto, Arial, Space Grotesk or Geist; an untouched shadcn theme.
- Buzzword copy ("supercharge", "seamless", "AI-powered magic").
- The accent for anything but the primary action, the running state and scores ≥ 85.
- The middle dot "·" anywhere but these six places: step messages ("14 new · 1 duplicates"), the
  top-bar headline, the table's meta line ("Kestrel Payments · Amsterdam, Netherlands"), the run panel's header and
  summary ("Last run · today 14:02", "Done · 12 kept · $0.84"), the setup wizard's progress line
  ("Step 2 of 5 · You") and the list of what a search still needs ("Before your first search: add your
  Apify token · choose where to search.").
- Colours, sizes, radii or spacing that aren't tokens above.

## Checking the UI

After a UI change, look at it in a browser before you commit:

1. `npm run build` in `frontend/`, then start the app with sample data:
   `JOBSCOUT_HOME=$(mktemp -d) PYTHONPATH=src uv run python -m jobscout.cli start --sample --no-browser --port 8799`
2. `node scripts/shoot.mjs http://127.0.0.1:8799 ../.artifacts/screens [--start-sample-run] [routes…]`
   takes a screenshot of each route at 390×844 and 1440×900 and prints console errors and warnings.
3. Compare the screenshots with this file: tokens, bans, copy, states, phone layout. The console must be clean.
4. Stop the server.
