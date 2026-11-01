// Takes the README screenshots into docs/images/ from a throwaway sample-mode server.
//
//   node scripts/readme-shots.mjs [outDir]        (run from frontend/; outDir defaults to ../docs/images)
//
// It starts its own server on a free port with an empty temporary data folder, runs the sample search,
// saves main.png, phone.png, settings.png and wizard.png, then stops the server and deletes the folder.
// All content is the fictional sample person. Browser console errors and requests to other hosts fail the run.

import { spawn } from "node:child_process"
import { mkdir, mkdtemp, rm, stat } from "node:fs/promises"
import net from "node:net"
import os from "node:os"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { chromium } from "playwright"

const here = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.resolve(here, "..", "..")
const outDir = path.resolve(process.argv[2] ?? path.join(repoRoot, "docs", "images"))
const APP_HEADER = { "X-Requested-By": "ai-job-scout", "Content-Type": "application/json" }
const RUN_TIMEOUT_MS = 180_000

const SAMPLE_PROFILE = `# Rosa Achterberg (sample profile)

## Who I am

- BSc in Computer Science, graduating in 2026. Based in Rotterdam, the Netherlands.
- Six-month internship as a software developer at a logistics start-up: built and shipped features in a Python and TypeScript codebase, wrote tests, worked in a small team and shadowed one on-call rotation.
- Comfortable with Python, TypeScript and React, SQL, Git and basic Docker. Not a data scientist.
- Languages: Dutch (native), English (fluent), German (good).

## What I want

- Entry-level software roles: junior software engineer, backend developer, full-stack developer, graduate engineer programmes. Data engineering is a good second choice.
- Anywhere in Europe or the UK, on site or hybrid. Happy to relocate.
- Starting from January 2027.

## What I don't want

- Senior roles that ask for five or more years of experience.
- Pure sales, support or marketing roles.
`

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.once("error", reject)
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address()
      server.close(() => resolve(port))
    })
  })
}

async function startServer(home, port) {
  const child = spawn(
    "uv",
    ["run", "python", "-m", "jobscout.cli", "start", "--sample", "--no-browser", "--port", String(port)],
    {
      cwd: repoRoot,
      env: { ...process.env, JOBSCOUT_HOME: home, PYTHONPATH: path.join(repoRoot, "src") },
      stdio: "ignore",
      detached: true, // its own process group, so the whole tree can be stopped
    },
  )
  const baseUrl = `http://127.0.0.1:${port}`
  const deadline = Date.now() + 60_000
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error("The server stopped while starting.")
    try {
      const res = await fetch(`${baseUrl}/api/health`)
      if (res.ok) return { child, baseUrl }
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 300))
  }
  stopServer(child)
  throw new Error("The server didn't start within 60 s.")
}

function stopServer(child) {
  if (child.exitCode !== null || child.pid === undefined) return
  try {
    process.kill(-child.pid, "SIGTERM")
  } catch {
    // already gone
  }
}

async function call(baseUrl, method, route, body) {
  const res = await fetch(`${baseUrl}${route}`, {
    method,
    headers: APP_HEADER,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`${method} ${route} failed: ${res.status} ${await res.text()}`)
  return res.json()
}

async function sampleRun(baseUrl) {
  await call(baseUrl, "POST", "/api/runs", { kind: "run" })
  const deadline = Date.now() + RUN_TIMEOUT_MS
  while (Date.now() < deadline) {
    const active = await (await fetch(`${baseUrl}/api/runs/active`)).json()
    if (active.run_id === null) return
    await new Promise((r) => setTimeout(r, 500))
  }
  throw new Error("The sample run didn't finish in time.")
}

/** The sample person's own setup, written the way the wizard's Next buttons would save it. The wizard
 *  only shows the profile review once there is a profile; `complete` marks the setup as finished, which the
 *  Settings screen needs (before that it sends you to the wizard). */
async function writeSampleSetup(baseUrl, complete) {
  await call(baseUrl, "PUT", "/api/mode", { mode: "real" })
  const settings = await call(baseUrl, "GET", "/api/settings")
  settings.linkedin.keywords = ["Junior software engineer", "Backend developer", "Full-stack developer"]
  settings.linkedin.regions = [
    { name: "European Economic Area", geo_id: "91000002" },
    { name: "United Kingdom", geo_id: "101165590" },
  ]
  settings.filters.excluded_countries = ["Switzerland"]
  settings.scoring.dealbreakers = [
    { text: "the role needs five or more years of experience", consequence: "below 40" },
    { text: "the job is pure sales, support or marketing", consequence: "below 40" },
  ]
  settings.ui.setup_complete = complete
  settings.ui.headline = "Software engineer, early career · Europe and the UK"
  await call(baseUrl, "PUT", "/api/settings", settings)
  await call(baseUrl, "PUT", "/api/profile", { text: SAMPLE_PROFILE })
}

async function main() {
  await mkdir(outDir, { recursive: true })
  const home = await mkdtemp(path.join(os.tmpdir(), "jobscout-readme-"))
  const port = await freePort()
  const { child, baseUrl } = await startServer(home, port)
  const origin = new URL(baseUrl).origin
  const problems = []
  let browser
  try {
    await sampleRun(baseUrl)
    browser = await chromium.launch()

    async function shoot(file, viewport, hash, prepare) {
      const context = await browser.newContext({ viewport, deviceScaleFactor: 1 })
      const page = await context.newPage()
      page.on("console", (msg) => {
        if (msg.type() === "error" || msg.type() === "warning") problems.push(`[console.${msg.type()}] ${file}: ${msg.text()}`)
      })
      page.on("pageerror", (err) => problems.push(`[pageerror] ${file}: ${err.message}`))
      page.on("request", (req) => {
        const url = req.url()
        if (/^https?:/.test(url) && new URL(url).origin !== origin) problems.push(`[external request] ${file}: ${url}`)
      })
      await page.goto(`${baseUrl}/${hash}`, { waitUntil: "networkidle" })
      await page.evaluate(() => document.fonts.ready)
      if (prepare) await prepare(page)
      const target = path.join(outDir, file)
      await page.screenshot({ path: target })
      const { size } = await stat(target)
      console.log(`saved ${target} (${Math.round(size / 1024)} KB)`)
      await context.close()
    }

    await shoot("main.png", { width: 1440, height: 900 }, "#/")
    await shoot("phone.png", { width: 390, height: 844 }, "#/")

    await writeSampleSetup(baseUrl, false)
    await shoot("wizard.png", { width: 1100, height: 940 }, "#/setup?step=2", async (page) => {
      const heading = page.getByRole("heading", { name: "Your profile" })
      await heading.waitFor()
      await heading.evaluate((el) => el.scrollIntoView({ block: "start" }))
      await page.evaluate(() => window.scrollBy(0, -24))
    })

    await writeSampleSetup(baseUrl, true)
    await shoot("settings.png", { width: 1440, height: 900 }, "#/settings/search")
  } finally {
    if (browser) await browser.close()
    stopServer(child)
    await rm(home, { recursive: true, force: true })
  }

  for (const line of problems) console.log(line)
  if (problems.length) {
    console.log(`${problems.length} problem(s).`)
    process.exit(1)
  }
  console.log("Console clean.")
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
