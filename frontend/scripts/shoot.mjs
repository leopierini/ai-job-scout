// Screenshots for the visual check loop and the smoke test.
//
//   node scripts/shoot.mjs <baseUrl> <outDir> [--start-sample-run] [routes...]
//
// Each route is shot at 390x844 and 1440x900 into <outDir>/<route-slug>-<w>x<h>.png (full page).
// console.error, console.warn, page errors and requests to other hosts are printed; the exit code is 1
// when there was any error (warnings alone don't fail). Default routes: #/ #/setup #/settings/profile #/history.
// --start-sample-run first starts a run (POST /api/runs) and waits until no run is active.

import { mkdir } from "node:fs/promises"
import path from "node:path"
import { chromium } from "playwright"

const VIEWPORTS = [
  { width: 390, height: 844 },
  { width: 1440, height: 900 },
]
const DEFAULT_ROUTES = ["#/", "#/setup", "#/settings/profile", "#/history"]
const APP_HEADER = { "X-Requested-By": "ai-job-scout" }
const RUN_TIMEOUT_MS = 180_000

function usage(message) {
  console.error(message)
  console.error("Usage: node scripts/shoot.mjs <baseUrl> <outDir> [--start-sample-run] [routes...]")
  process.exit(2)
}

/** "#/" -> "main", "#/settings/profile" -> "settings-profile", "#/setup?step=3" -> "setup-step-3". */
function slug(route) {
  const s = route
    .replace(/^#?\/?/, "")
    .replace(/[^a-zA-Z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
  return s || "main"
}

async function startSampleRun(baseUrl) {
  const res = await fetch(`${baseUrl}/api/runs`, {
    method: "POST",
    headers: { ...APP_HEADER, "Content-Type": "application/json" },
    body: JSON.stringify({ kind: "run" }),
  })
  if (!res.ok) throw new Error(`Starting a run failed: ${res.status} ${await res.text()}`)
  const deadline = Date.now() + RUN_TIMEOUT_MS
  while (Date.now() < deadline) {
    const active = await (await fetch(`${baseUrl}/api/runs/active`)).json()
    if (active.run_id === null) return
    await new Promise((r) => setTimeout(r, 500))
  }
  throw new Error(`The run didn't finish within ${RUN_TIMEOUT_MS / 1000} s.`)
}

async function main() {
  const args = process.argv.slice(2)
  const startRun = args.includes("--start-sample-run")
  const [baseArg, outDir, ...rest] = args.filter((a) => a !== "--start-sample-run")
  if (!baseArg || !outDir) usage("Missing <baseUrl> or <outDir>.")
  const baseUrl = baseArg.replace(/\/+$/, "")
  const routes = rest.length ? rest : DEFAULT_ROUTES
  const origin = new URL(baseUrl).origin

  await mkdir(outDir, { recursive: true })
  if (startRun) await startSampleRun(baseUrl)

  const errors = []
  const warnings = []
  const browser = await chromium.launch()
  try {
    for (const viewport of VIEWPORTS) {
      const context = await browser.newContext({ viewport, deviceScaleFactor: 1 })
      for (const route of routes) {
        const where = `${route} @ ${viewport.width}x${viewport.height}`
        const page = await context.newPage()
        page.on("console", (msg) => {
          if (msg.type() === "error") errors.push(`[console.error] ${where}: ${msg.text()}`)
          if (msg.type() === "warning") warnings.push(`[console.warn] ${where}: ${msg.text()}`)
        })
        page.on("pageerror", (err) => errors.push(`[pageerror] ${where}: ${err.message}`))
        page.on("request", (req) => {
          const url = req.url()
          if (/^https?:/.test(url) && new URL(url).origin !== origin) {
            errors.push(`[external request] ${where}: ${url}`)
          }
        })
        await page.goto(`${baseUrl}/${route.startsWith("#") ? route : `#${route}`}`, { waitUntil: "networkidle" })
        await page.evaluate(() => document.fonts.ready)
        const file = path.join(outDir, `${slug(route)}-${viewport.width}x${viewport.height}.png`)
        await page.screenshot({ path: file, fullPage: true })
        console.log(`saved ${file}`)
        await page.close()
      }
      await context.close()
    }
  } finally {
    await browser.close()
  }

  for (const line of [...warnings, ...errors]) console.log(line)
  if (errors.length) {
    console.log(`${errors.length} error(s).`)
    process.exit(1)
  }
  console.log(warnings.length ? `Clean of errors (${warnings.length} warning(s)).` : "Console clean.")
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
