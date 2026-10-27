/**
 * Mobile walkthrough test for a floor-plan-only furnished reconstruction.
 *
 * Opens the most furnished completed job in a touch-emulated Chromium context,
 * verifies no photo flow is involved, confirms the viewer reports the job's real
 * furniture count and current-room contents, drives the joystick with real CDP
 * touch events, checks the player moved, drags across the canvas to rotate the
 * view, and toggles real furniture visibility with taps. Saves a screenshot.
 *
 * Prerequisites: reconstruction engine on :8002 and Next.js on :3000.
 * Usage: node scripts/e2e-mobile.mjs [jobId]
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

async function resolveJob() {
  if (process.argv[2]) {
    const response = await fetch(`${engineUrl}/reconstructions/${process.argv[2]}`)
    if (!response.ok) throw new Error(`Job ${process.argv[2]} not found`)
    return response.json()
  }
  const response = await fetch(`${engineUrl}/reconstructions?limit=30`)
  if (!response.ok) throw new Error(`Engine not reachable at ${engineUrl}`)
  const jobs = await response.json()
  const completed = jobs.filter(
    (job) =>
      job.status === 'completed' &&
      job.photo_count === 0 &&
      job.navigation_available === true &&
      (job.furniture_count ?? 0) > 0,
  )
  completed.sort((a, b) => (b.furniture_count ?? 0) - (a.furniture_count ?? 0))
  if (!completed.length) {
    throw new Error('No furnished completed floor-plan-only job found; run the desktop E2E first')
  }
  return completed[0]
}

async function fetchJson(url) {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`${url} -> ${response.status}`)
  return response.json()
}

async function readState(page) {
  return page.evaluate(() => window.__walkthrough.getState())
}

async function touchDrag(cdp, from, to, holdMs = 0, steps = 8, durationMs = 24) {
  await cdp.send('Input.dispatchTouchEvent', {
    type: 'touchStart',
    touchPoints: [{ x: from.x, y: from.y, id: 1 }],
  })
  for (let step = 1; step <= steps; step += 1) {
    const x = from.x + ((to.x - from.x) * step) / steps
    const y = from.y + ((to.y - from.y) * step) / steps
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchMove',
      touchPoints: [{ x, y, id: 1 }],
    })
    await new Promise((resolve) => setTimeout(resolve, durationMs))
  }
  if (holdMs > 0) await new Promise((resolve) => setTimeout(resolve, holdMs))
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
}

async function main() {
  const job = await resolveJob()
  const jobId = job.job_id
  assert.equal(job.status, 'completed')
  assert.equal(job.photo_count, 0, 'Floor-plan-only jobs must not require photographs')
  assert.ok(job.furniture_count > 0, 'The selected job must contain real furniture')
  assert.equal(job.navigation_available, true, 'The selected job must be navigable')
  fs.mkdirSync(screenshotDir, { recursive: true })

  const browser = await chromium.launch({
    executablePath: findChromium(),
    headless: true,
    args: ['--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
  })
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    screen: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
    deviceScaleFactor: 2,
  })
  const page = await context.newPage()
  const consoleErrors = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('pageerror', (error) => consoleErrors.push(String(error)))

  try {
    await page.goto(`${frontendUrl}/tour/${jobId}`, { waitUntil: 'networkidle', timeout: 120000 })
    await page.waitForFunction(() => window.__walkthrough?.getState?.(), null, { timeout: 90000 })
    await page.waitForTimeout(500)

    const coarse = await page.evaluate(() => window.matchMedia('(pointer: coarse)').matches)
    assert.ok(coarse, 'Touch-emulated context should report a coarse pointer')
    assert.equal(
      await page.locator('[data-testid="photo-review"]').count(),
      0,
      'A floor-plan-only tour must not show the removed photo review',
    )

    const start = await readState(page)
    assert.equal(start.jobId, jobId, 'The viewer must load the selected job')
    const scene = await fetchJson(`${engineUrl}/reconstructions/${jobId}/scene`)
    assert.equal(scene.metadata.job_id, jobId)
    const startNode = scene.room_graph.nodes.find((node) => node.id === start.roomId)
    assert.ok(startNode, 'The player should spawn inside a detected room')
    assert.ok(startNode.furniture.length > 0, 'The spawn room should contain real furniture')

    const body = await page.locator('body').innerText()
    assert.ok(
      new RegExp(`\\b${job.furniture_count} furniture\\b`).test(body),
      `The viewer header should report ${job.furniture_count} furniture items`,
    )
    const overlay = await page.getByTestId('room-info-overlay').innerText()
    assert.ok(
      new RegExp(`\\b${startNode.furniture_count} items?\\b`).test(overlay),
      `The room overlay should report ${startNode.furniture_count} furniture items`,
    )

    const joystick = page.getByRole('group', { name: 'Movement joystick' })
    await joystick.waitFor({ state: 'visible', timeout: 15000 })
    await joystick.scrollIntoViewIfNeeded()
    const box = await joystick.boundingBox()
    assert.ok(box, 'Joystick should have a bounding box')

    const cdp = await context.newCDPSession(page)
    const center = { x: box.x + box.width / 2, y: box.y + box.height / 2 }
    await touchDrag(cdp, center, { x: center.x, y: center.y - box.height * 0.42 }, 1200, 10, 30)
    await page.waitForTimeout(1600)
    const afterJoystick = await readState(page)
    const moved = Math.hypot(afterJoystick.x - start.x, afterJoystick.z - start.z)
    assert.ok(moved > 0.5, `Joystick should move the player; moved ${moved.toFixed(2)} m`)

    const canvas = page.getByTestId('walkthrough-canvas')
    const canvasBox = await canvas.boundingBox()
    assert.ok(canvasBox, 'Canvas should have a bounding box')
    const lookFrom = { x: canvasBox.x + canvasBox.width / 2, y: canvasBox.y + canvasBox.height / 2 }
    await touchDrag(cdp, lookFrom, { x: lookFrom.x + 120, y: lookFrom.y }, 0, 10, 30)
    await page.waitForTimeout(400)
    const afterLook = await readState(page)
    const yawDelta = Math.abs(afterLook.yaw - afterJoystick.yaw)
    assert.ok(yawDelta > 0.1, `Touch drag should rotate the view; yaw changed ${yawDelta.toFixed(3)}`)

    await page.getByRole('button', { name: 'Options' }).tap()
    const hideFurniture = page.getByRole('button', { name: 'Furniture on' })
    await hideFurniture.scrollIntoViewIfNeeded()
    assert.equal(await hideFurniture.getAttribute('aria-pressed'), 'true')
    await hideFurniture.tap()
    const showFurniture = page.getByRole('button', { name: 'Furniture off' })
    await showFurniture.waitFor({ state: 'visible', timeout: 10000 })
    assert.equal(await showFurniture.getAttribute('aria-pressed'), 'false')
    await page.waitForTimeout(300)
    await showFurniture.tap()
    await page.getByRole('button', { name: 'Furniture on' }).waitFor({ state: 'visible' })
    await page.getByRole('button', { name: 'Close' }).tap()

    await page.screenshot({ path: path.join(screenshotDir, 'tour-mobile.png') })

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
          viewport: '390x844 touch',
          furnitureCount: job.furniture_count,
          roomFurniture: startNode.furniture_count,
          furnitureToggled: true,
          joystickDistance: Number(moved.toFixed(2)),
          yawDelta: Number(yawDelta.toFixed(3)),
          eyeHeight: Number(afterLook.y.toFixed(2)),
          screenshot: path.join(screenshotDir, 'tour-mobile.png'),
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
