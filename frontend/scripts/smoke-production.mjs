/**
 * Production smoke test for the floor-plan-only furnished walkthrough.
 *
 * Loads a completed job from the production build, confirms the upload page no
 * longer asks for photographs, verifies the header/room summary/furnishing panel
 * report the job's real rooms, furniture, style and variant, checks the WebGL
 * canvas and the job's GLB request, toggles real furniture visibility, and
 * asserts the main pages respond. Movement is covered by the dev E2E suite.
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
  const jobsResponse = await fetch(`${engineUrl}/reconstructions?limit=30`)
  assert.ok(jobsResponse.ok, 'Reconstruction engine should be reachable')
  const jobs = await jobsResponse.json()
  const job = jobs.find(
    (item) =>
      item.status === 'completed' &&
      item.scale_verified === true &&
      item.photo_count === 0 &&
      item.navigation_available === true &&
      (item.furniture_count ?? 0) > 0,
  )
  assert.ok(
    job,
    'A completed, navigable, furnished floor-plan-only job is required; run the dev E2E first',
  )
  const jobResponse = await fetch(`${engineUrl}/reconstructions/${job.job_id}`)
  assert.ok(jobResponse.ok, 'Job details should load')
  const detail = await jobResponse.json()
  const sceneResponse = await fetch(`${engineUrl}/reconstructions/${job.job_id}/scene`)
  assert.ok(sceneResponse.ok, 'Scene metadata should load')
  const scene = await sceneResponse.json()
  assert.equal(scene.metadata.job_id, job.job_id)
  assert.equal(scene.photo_manifest, null, 'Floor-plan-only scenes must not have a photo manifest')
  assert.equal(scene.metadata.method.textures.applied, false)
  assert.equal(scene.furniture.length, job.furniture_count)
  assert.ok(scene.collision.furniture.length > 0, 'Furniture collision must be present')
  assert.equal(scene.room_graph.nodes.length, job.room_count)
  assert.ok(
    scene.collision.walls.length > 0,
    'Wall collision from the floor plan must be present',
  )

  const browser = await chromium.launch({
    executablePath: findChromium(),
    headless: true,
    args: ['--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
  })
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  const consoleErrors = []
  const failedResponses = []
  let sceneGlbStatus = null
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('pageerror', (error) => consoleErrors.push(String(error)))
  page.on('response', (response) => {
    if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`)
    if (response.url().includes('/assets/scene.glb')) sceneGlbStatus = response.status()
  })

  try {
    for (const route of ['/', '/create-tour', '/tours']) {
      const response = await page.goto(`${frontendUrl}${route}`, {
        waitUntil: 'domcontentloaded',
        timeout: 60000,
      })
      assert.equal(response?.status(), 200, `${route} should return 200`)
    }

    await page.goto(`${frontendUrl}/create-tour`, { waitUntil: 'networkidle', timeout: 60000 })
    assert.equal(await page.locator('#floor-plan').count(), 1, 'The floor plan input must exist')
    assert.equal(await page.locator('#photos').count(), 0, 'The upload form must not ask for photos')
    await page.getByRole('button', { name: 'Generate furnished tour' }).waitFor({ state: 'visible' })

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
    assert.ok(body.includes(`${job.room_count} rooms`), 'Tour should report the detected rooms')
    assert.ok(
      body.includes(`${job.furniture_count} furniture items`),
      'Tour should report the real furniture count',
    )
    assert.ok(
      new RegExp(`${job.furnishing_style} style`, 'i').test(body),
      'Tour should report the style',
    )
    assert.ok(body.includes('All rooms reachable'), 'Tour should report navigation validation')
    assert.equal(
      await page.locator('[data-testid="photo-review"]').count(),
      0,
      'The removed photo review must not appear',
    )
    assert.equal(sceneGlbStatus, 200, 'The viewer must load the job-specific GLB')

    await page.getByRole('button', { name: 'Furnishing' }).click()
    const controls = page.getByTestId('furnishing-controls')
    await controls.waitFor({ state: 'visible', timeout: 15000 })
    await page.getByTestId('furnishing-style').waitFor({ state: 'visible' })
    assert.equal(await page.getByTestId('furnishing-style').inputValue(), job.furnishing_style)
    const controlsText = await controls.innerText()
    assert.ok(
      controlsText.includes(
        `variant ${job.furnishing_variant} · ${job.furniture_count} furniture items`,
      ),
      'Furnishing panel should report the job variant and furniture count',
    )
    const roomSummary = page.getByTestId('room-summary')
    const summaryText = await roomSummary.innerText()
    for (const roomType of detail.room_types ?? []) {
      assert.ok(
        summaryText.includes(String(roomType.room_type).replace(/_/g, ' ')),
        `Room summary should list the ${roomType.room_type} room type`,
      )
    }
    await page.getByRole('button', { name: 'Hide furnishing' }).click()

    const hideFurniture = page.getByRole('button', { name: 'Hide furniture' })
    await hideFurniture.waitFor({ state: 'visible', timeout: 15000 })
    assert.equal(await hideFurniture.getAttribute('aria-pressed'), 'true')
    await hideFurniture.click()
    const showFurniture = page.getByRole('button', { name: 'Show furniture' })
    await showFurniture.waitFor({ state: 'visible', timeout: 15000 })
    assert.equal(await showFurniture.getAttribute('aria-pressed'), 'false')
    await page.waitForTimeout(300)

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
          rooms: job.room_count,
          furniture: job.furniture_count,
          furnitureCollision: scene.collision.furniture.length,
          style: job.furnishing_style,
          variant: job.furnishing_variant,
          canvas: dimensions,
          sceneGlbStatus,
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
