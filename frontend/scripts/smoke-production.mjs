/**
 * Production smoke test: loads an existing completed tour from the production
 * build, confirms the WebGL canvas renders, and checks the main pages respond.
 * Movement is covered by the dev E2E suite, which uses the debug state hook.
 */
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright-core'

const here = path.dirname(fileURLToPath(import.meta.url))
const frontendDir = path.resolve(here, '..')
const engineUrl = process.env.NEXT_PUBLIC_RECONSTRUCTION_URL || 'http://localhost:8002'
const frontendUrl = process.env.FRONTEND_URL || 'http://localhost:3000'
const screenshotDir = process.env.E2E_SCREENSHOT_DIR || path.join(frontendDir, 'test-results')

function findChromium() {
  if (process.env.CHROMIUM_PATH) return process.env.CHROMIUM_PATH
  for (const candidate of [
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
  ]) {
    if (fs.existsSync(candidate)) return candidate
  }
  const cache = path.join(os.homedir(), 'Library/Caches/ms-playwright')
  if (fs.existsSync(cache)) {
    for (const revision of fs
      .readdirSync(cache)
      .filter((name) => name.startsWith('chromium-'))
      .sort()
      .reverse()) {
      const candidate = path.join(
        cache,
        revision,
        'chrome-mac-arm64',
        'Google Chrome for Testing.app',
        'Contents/MacOS/Google Chrome for Testing',
      )
      if (fs.existsSync(candidate)) return candidate
    }
  }
  throw new Error('No Chromium found. Run `npx playwright install chromium` or set CHROMIUM_PATH.')
}

async function main() {
  const jobsResponse = await fetch(`${engineUrl}/reconstructions?limit=20`)
  assert.ok(jobsResponse.ok, 'Reconstruction engine should be reachable')
  const jobs = await jobsResponse.json()
  const job = jobs.find(
    (item) => item.status === 'completed' && item.scale_verified === true,
  )
  assert.ok(job, 'A completed reconstruction with a verified scale is required; run the dev E2E first')

  const browser = await chromium.launch({
    executablePath: findChromium(),
    headless: true,
    args: ['--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
  })
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  const consoleErrors = []
  const failedResponses = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('pageerror', (error) => consoleErrors.push(String(error)))
  page.on('response', (response) => {
    if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`)
  })

  try {
    for (const route of ['/', '/create-tour', '/tours']) {
      const response = await page.goto(`${frontendUrl}${route}`, {
        waitUntil: 'domcontentloaded',
        timeout: 60000,
      })
      assert.equal(response?.status(), 200, `${route} should return 200`)
    }

    const response = await page.goto(`${frontendUrl}/tour/${job.job_id}`, {
      waitUntil: 'networkidle',
      timeout: 90000,
    })
    assert.equal(response?.status(), 200)

    const canvas = page.getByTestId('walkthrough-canvas')
    await canvas.waitFor({ state: 'visible', timeout: 90000 })
    await page.waitForTimeout(2500)
    const dimensions = await canvas.evaluate((element) => ({
      width: element.width,
      height: element.height,
    }))
    assert.ok(dimensions.width > 0 && dimensions.height > 0, 'Canvas should have a drawing buffer')

    const body = await page.locator('body').innerText()
    assert.ok(body.includes('Measured dimensions'), 'Tour header should show measured scale')
    assert.ok(/\d+ rooms/.test(body), 'Tour overlay should report the detected room count')

    fs.mkdirSync(screenshotDir, { recursive: true })
    const screenshot = path.join(screenshotDir, 'tour-production.png')
    await page.screenshot({ path: screenshot })

    const fatalErrors = consoleErrors.filter(
      (message) =>
        !/Download the React DevTools|WebGL|Warning: React does not recognize/i.test(message),
    )
    assert.deepEqual(
      fatalErrors,
      [],
      `Console errors: ${fatalErrors.join('\n')}\nFailed responses: ${failedResponses.join('\n')}`,
    )

    console.log(
      JSON.stringify(
        {
          status: 'passed',
          build: 'production',
          jobId: job.job_id,
          canvas: dimensions,
          screenshot,
          consoleErrors: fatalErrors,
        },
        null,
        2,
      ),
    )
  } finally {
    await browser.close()
  }
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
