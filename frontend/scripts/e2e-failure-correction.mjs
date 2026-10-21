/**
 * End-to-end failure and correction test.
 *
 * Uploads a deliberately open floor plan, confirms the UI surfaces the real
 * failed stage, error code, original upload and saved detection artifacts,
 * then draws two axis-aligned room rectangles plus a doorway on the plan and
 * verifies the corrected job produces a navigable two-room scene.
 *
 * Prerequisites: engine at :8002 and frontend at :3000 (same as the other E2E
 * scripts).
 */
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright-core'

const here = path.dirname(fileURLToPath(import.meta.url))
const frontendDir = path.resolve(here, '..')
const engineDir = path.resolve(frontendDir, '../floor_plan_reconstruction')
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

function ensureFixtures() {
  const plan = path.join(engineDir, 'input/e2e_open_plan.png')
  if (!fs.existsSync(plan)) {
    const script = `
import cv2
import numpy as np
canvas = np.ones((600, 800, 3), dtype=np.uint8) * 255
black = (0, 0, 0)
cv2.rectangle(canvas, (60, 60), (740, 70), black, -1)
cv2.rectangle(canvas, (60, 60), (70, 540), black, -1)
cv2.rectangle(canvas, (730, 60), (740, 540), black, -1)
cv2.rectangle(canvas, (395, 70), (405, 260), black, -1)
cv2.rectangle(canvas, (395, 330), (405, 540), black, -1)
cv2.imwrite(${JSON.stringify(plan)}, canvas)
`
    const result = spawnSync(path.join(engineDir, 'venv/bin/python'), ['-c', script], {
      cwd: engineDir,
      stdio: 'inherit',
    })
    if (result.status !== 0) throw new Error('Could not generate the open-plan fixture')
  }
  const photoDir = path.join(engineDir, 'tests/assets/sfm')
  const photos = fs
    .readdirSync(photoDir)
    .filter((name) => name.startsWith('overlap_') && name.endsWith('.jpg'))
    .sort()
    .slice(0, 2)
    .map((name) => path.join(photoDir, name))
  assert.equal(photos.length, 2, `Expected two photographs in ${photoDir}`)
  return { plan, photos }
}

async function fetchJson(url) {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`${url} -> ${response.status} ${await response.text()}`)
  return response.json()
}

async function waitForStatus(jobId, statuses, timeoutMs = 300000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const job = await fetchJson(`${engineUrl}/reconstructions/${jobId}`)
    if (statuses.includes(job.status)) return job
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  throw new Error(`Timed out waiting for ${jobId} to reach ${statuses.join('/')}`)
}

async function confirmReviewViaApi(jobId) {
  const pending = await waitForStatus(jobId, ['awaiting_review', 'failed', 'cancelled'])
  if (pending.status !== 'awaiting_review') {
    throw new Error(`Corrected job did not reach review: ${pending.error || pending.status}`)
  }
  const review = await fetchJson(`${engineUrl}/reconstructions/${jobId}/review`)
  const room = [...review.rooms].sort((left, right) => left.room_id - right.room_id)[0]
  const assignments = review.photos
    .filter((photo) => photo.processing.status === 'ready')
    .map((photo) => ({ photo_id: photo.photo_id, room_id: room.room_id }))
  if (assignments.length === 0) {
    throw new Error('Corrected job has no usable photographs to assign')
  }
  const response = await fetch(`${engineUrl}/reconstructions/${jobId}/confirm-assignments`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      assignments,
      room_types: { [room.room_id]: 'bathroom' },
    }),
  })
  if (!response.ok) {
    throw new Error(`Confirm assignments failed: ${response.status} ${await response.text()}`)
  }
  return review
}

async function drawRoom(page, image, box) {
  const bounds = await image.boundingBox()
  assert.ok(bounds, 'Manual correction preview should be laid out')
  await page.mouse.move(bounds.x + bounds.width * box.x0, bounds.y + bounds.height * box.y0)
  await page.mouse.down()
  await page.mouse.move(bounds.x + bounds.width * box.x1, bounds.y + bounds.height * box.y1, {
    steps: 8,
  })
  await page.mouse.up()
}

async function main() {
  const { plan, photos } = ensureFixtures()
  const health = await fetchJson(`${engineUrl}/health`)
  assert.equal(health.dependencies.photogrammetry_available, true)
  fs.mkdirSync(screenshotDir, { recursive: true })

  const browser = await chromium.launch({
    executablePath: findChromium(),
    headless: true,
    args: ['--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
  })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  const consoleErrors = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('pageerror', (error) => consoleErrors.push(String(error)))

  try {
    await page.goto(`${frontendUrl}/create-tour`, { waitUntil: 'networkidle', timeout: 120000 })
    await page.fill('#project-name', 'E2E failure and correction')
    await page.setInputFiles('#floor-plan', plan)
    await page.setInputFiles('#photos', photos)
    await page.click('button[type="submit"]')

    await page.getByText('Detection stopped at Detect rooms').waitFor({
      state: 'visible',
      timeout: 180000,
    })
    const failureText = await page.locator('body').innerText()
    assert.match(failureText, /no_enclosed_rooms/)
    assert.match(failureText, /No enclosed rooms were found/i)
    assert.match(failureText, /Retry with different preprocessing/i)
    assert.match(failureText, /Correct room outlines manually/i)
    const jobMatch = failureText.match(/Job ([0-9a-f-]{36})/)
    assert.ok(jobMatch, 'Failed job id should be visible')
    const jobId = jobMatch[1]

    const diagnostics = await fetchJson(`${engineUrl}/reconstructions/${jobId}/diagnostics`)
    assert.equal(diagnostics.failed_stage, 'detecting_rooms')
    assert.equal(diagnostics.error_code, 'no_enclosed_rooms')
    assert.equal(diagnostics.manual_plan_supported, true)
    for (const name of ['walls_mask.png', 'parse_failure.json']) {
      assert.ok(
        diagnostics.artifacts.some((artifact) => artifact.name === name),
        `${name} should be saved`,
      )
    }
    await page.screenshot({
      path: path.join(screenshotDir, 'failure-diagnostics.png'),
      fullPage: true,
    })

    await page.getByRole('button', { name: 'Correct room outlines manually' }).click()
    const preview = page.getByAltText('Floor plan for manual room-outline correction')
    await preview.scrollIntoViewIfNeeded()
    await preview.evaluate((element) => {
      if (!element.complete || !element.naturalWidth) {
        throw new Error('Manual correction preview did not load')
      }
    })
    await drawRoom(page, preview, { x0: 0.075, y0: 0.1, x1: 0.5, y1: 0.9 })
    await drawRoom(page, preview, { x0: 0.5, y0: 0.1, x1: 0.925, y1: 0.9 })
    await page.getByRole('button', { name: 'Place doorway' }).click()
    const bounds = await preview.boundingBox()
    await page.mouse.click(bounds.x + bounds.width * 0.5, bounds.y + bounds.height * 0.5)

    await page.getByText('Room 2:').waitFor({ state: 'visible' })
    await page.getByText('Doorway 1:').waitFor({ state: 'visible' })
    await page.screenshot({
      path: path.join(screenshotDir, 'manual-correction-outline.png'),
      fullPage: true,
    })

    await page.getByRole('button', { name: 'Reconstruct from corrected outline' }).click()
    await page.getByTestId('photo-review').waitFor({ state: 'visible', timeout: 300000 })
    const correctedReview = await confirmReviewViaApi(jobId)
    const tourLink = page.getByRole('link', { name: 'Open 3D walkthrough' })
    await tourLink.waitFor({ state: 'visible', timeout: 300000 })
    await page.screenshot({
      path: path.join(screenshotDir, 'manual-correction-completed.png'),
      fullPage: true,
    })

    const job = await fetchJson(`${engineUrl}/reconstructions/${jobId}`)
    assert.equal(job.status, 'completed', job.error)
    assert.equal(job.geometry_valid, true)
    assert.equal(job.navigation_available, true)
    assert.equal(job.assignments_confirmed, true)
    assert.equal(job.review_required, false)
    assert.equal(job.manual_outline.rooms.length, 2)
    const scene = await fetchJson(`${engineUrl}/reconstructions/${jobId}/scene`)
    assert.equal(scene.room_graph.nodes.length, 2)
    assert.equal(scene.room_graph.edges.length, 1)
    assert.equal(scene.room_graph.connected, true)
    const correctedDiagnostics = await fetchJson(
      `${engineUrl}/reconstructions/${jobId}/diagnostics`,
    )
    assert.equal(correctedDiagnostics.parser.segmentation.source, 'manual_outline')

    const fatalErrors = consoleErrors.filter(
      (message) =>
        !/Download the React DevTools|WebGL|Warning: React does not recognize/i.test(message),
    )
    assert.deepEqual(fatalErrors, [], `Console errors: ${fatalErrors.join('\n')}`)

    console.log(
      JSON.stringify(
        {
          status: 'passed',
          jobId,
          failedStage: diagnostics.failed_stage,
          errorCode: diagnostics.error_code,
          artifacts: diagnostics.artifacts.map((artifact) => artifact.name),
          corrected: {
            rooms: scene.room_graph.nodes.length,
            doorways: scene.room_graph.edges.length,
            navigationAvailable: job.navigation_available,
            segmentationSource: correctedDiagnostics.parser.segmentation.source,
            reviewedPhotos: correctedReview.photos.length,
            assignmentsConfirmed: job.assignments_confirmed,
          },
          screenshots: screenshotDir,
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
