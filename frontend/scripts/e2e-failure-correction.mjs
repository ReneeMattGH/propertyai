/**
 * End-to-end failure and manual correction test (floor-plan-only workflow).
 *
 * Uploads a deliberately open floor plan with no photographs and confirms the
 * UI surfaces the real failed stage, error code, original upload and saved
 * detection artifacts. Then it draws two axis-aligned room rectangles plus a
 * doorway on the plan, confirms the inferred room types, and verifies the
 * corrected job produces a furnished, navigable two-room scene whose
 * architecture comes from the manual outline.
 *
 * Prerequisites: engine at :8002 and the frontend at FRONTEND_URL (default
 * :3000, whose development build exposes the viewer state used for walking).
 */
import assert from 'node:assert/strict'
import crypto from 'node:crypto'
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
const PLAYER_RADIUS = 0.24

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
  return plan
}

const sha256 = (buffer) => crypto.createHash('sha256').update(buffer).digest('hex')

async function fetchJson(url) {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`${url} -> ${response.status} ${await response.text()}`)
  return response.json()
}

async function readState(page) {
  return page.evaluate(() => window.__walkthrough.getState())
}

async function waitForViewer(page, timeoutMs = 90000) {
  await page.waitForFunction(() => window.__walkthrough?.getState?.(), null, { timeout: timeoutMs })
  await page.waitForTimeout(400)
  return readState(page)
}

function roomAt(rooms, x, z) {
  return rooms.find((room) => {
    const [minX, minZ, width, height] = room.bounds
    return x >= minX && x <= minX + width && z >= minZ && z <= minZ + height
  })
}

function pointBlocked(x, z, boxes, radius = PLAYER_RADIUS + 0.015) {
  return boxes.some(
    (box) =>
      x >= box.min[0] - radius &&
      x <= box.max[0] + radius &&
      z >= box.min[1] - radius &&
      z <= box.max[1] + radius,
  )
}

function navBoxes(scene) {
  return [
    ...scene.collision.walls.map((wall) => ({ min: wall.min, max: wall.max })),
    ...scene.collision.furniture.map((item) => ({ min: item.min, max: item.max })),
  ]
}

function segmentClear(from, to, boxes, step = 0.04) {
  const length = Math.hypot(to.x - from.x, to.z - from.z)
  const samples = Math.max(1, Math.ceil(length / step))
  for (let index = 0; index <= samples; index += 1) {
    const ratio = index / samples
    const x = from.x + (to.x - from.x) * ratio
    const z = from.z + (to.z - from.z) * ratio
    if (pointBlocked(x, z, boxes)) return false
  }
  return true
}

function nearestFreeCell(free, nx, nz, x, z, cell, radius) {
  const originX = Math.floor(x / cell)
  const originZ = Math.floor(z / cell)
  let best = null
  const span = Math.ceil(radius / cell)
  for (let dz = -span; dz <= span; dz += 1) {
    for (let dx = -span; dx <= span; dx += 1) {
      const ix = originX + dx
      const iz = originZ + dz
      if (ix < 0 || iz < 0 || ix >= nx || iz >= nz) continue
      if (!free[iz * nx + ix]) continue
      const cx = (ix + 0.5) * cell
      const cz = (iz + 0.5) * cell
      const distanceToTarget = Math.hypot(cx - x, cz - z)
      if (distanceToTarget > radius) continue
      if (!best || distanceToTarget < best.distance) best = { ix, iz, distance: distanceToTarget }
    }
  }
  assert.ok(best, `No walkable cell within ${radius} m of (${x.toFixed(2)}, ${z.toFixed(2)})`)
  return best
}

function findWalkPath(scene, target) {
  const cell = 0.1
  const { width, height } = scene.collision.footprint
  const nx = Math.ceil(width / cell)
  const nz = Math.ceil(height / cell)
  const boxes = navBoxes(scene)
  const free = new Uint8Array(nx * nz)
  for (let iz = 0; iz < nz; iz += 1) {
    for (let ix = 0; ix < nx; ix += 1) {
      free[iz * nx + ix] = pointBlocked((ix + 0.5) * cell, (iz + 0.5) * cell, boxes) ? 0 : 1
    }
  }
  const spawn = scene.collision.spawn.position
  const start = nearestFreeCell(free, nx, nz, spawn[0], spawn[2], cell, 0.8)
  const goal = nearestFreeCell(free, nx, nz, target.x, target.z, cell, 2.0)
  const index = (ix, iz) => iz * nx + ix
  const startIndex = index(start.ix, start.iz)
  const goalIndex = index(goal.ix, goal.iz)
  const open = [{ index: startIndex, score: 0 }]
  const cameFrom = new Map()
  const best = new Map([[startIndex, 0]])
  const heuristic = (ix, iz) => Math.abs(ix - goal.ix) + Math.abs(iz - goal.iz)
  const visited = new Uint8Array(nx * nz)
  let found = false
  while (open.length > 0) {
    open.sort((left, right) => right.score - left.score)
    const current = open.pop()
    if (visited[current.index]) continue
    visited[current.index] = 1
    if (current.index === goalIndex) {
      found = true
      break
    }
    const cx = current.index % nx
    const cz = Math.floor(current.index / nx)
    for (const [dx, dz] of [
      [1, 0],
      [-1, 0],
      [0, 1],
      [0, -1],
    ]) {
      const ix = cx + dx
      const iz = cz + dz
      if (ix < 0 || iz < 0 || ix >= nx || iz >= nz) continue
      const nextIndex = index(ix, iz)
      if (!free[nextIndex] || visited[nextIndex]) continue
      const cost = (best.get(current.index) ?? Infinity) + 1
      if (cost >= (best.get(nextIndex) ?? Infinity)) continue
      best.set(nextIndex, cost)
      cameFrom.set(nextIndex, current.index)
      open.push({ index: nextIndex, score: cost + heuristic(ix, iz) })
    }
  }
  assert.ok(found, `No walkable route to (${target.x.toFixed(2)}, ${target.z.toFixed(2)})`)
  const cells = []
  let cursor = goalIndex
  while (cursor !== startIndex) {
    cells.push({ ix: cursor % nx, iz: Math.floor(cursor / nx) })
    cursor = cameFrom.get(cursor)
    assert.ok(cursor !== undefined, 'Path reconstruction failed')
  }
  cells.push(start)
  cells.reverse()
  const points = cells.map((entry) => ({
    x: (entry.ix + 0.5) * cell,
    z: (entry.iz + 0.5) * cell,
  }))
  const waypoints = []
  let anchor = 0
  while (anchor < points.length - 1) {
    let next = points.length - 1
    while (next > anchor + 1 && !segmentClear(points[anchor], points[next], boxes)) next -= 1
    waypoints.push(points[next])
    anchor = next
  }
  return { waypoints, cells: points.length }
}

async function walkTo(page, target, timeoutMs = 20000) {
  const pressed = new Set()
  const setKeys = async (next) => {
    for (const key of ['KeyW', 'KeyS', 'KeyA', 'KeyD']) {
      if (pressed.has(key) && !next.has(key)) {
        await page.keyboard.up(key)
        pressed.delete(key)
      }
      if (!pressed.has(key) && next.has(key)) {
        await page.keyboard.down(key)
        pressed.add(key)
      }
    }
  }
  try {
    const deadline = Date.now() + timeoutMs
    let closest = Number.POSITIVE_INFINITY
    let progressAt = Date.now()
    while (Date.now() < deadline) {
      const state = await readState(page)
      const dx = target.x - state.x
      const dz = target.z - state.z
      const remaining = Math.hypot(dx, dz)
      if (remaining < 0.3) break
      if (remaining < closest - 0.04) {
        closest = remaining
        progressAt = Date.now()
      }
      const forward = { x: -Math.sin(state.yaw), z: -Math.cos(state.yaw) }
      const right = { x: Math.cos(state.yaw), z: -Math.sin(state.yaw) }
      const alongForward = dx * forward.x + dz * forward.z
      const alongRight = dx * right.x + dz * right.z
      const next = new Set()
      if (Math.abs(alongForward) > 0.2) next.add(alongForward > 0 ? 'KeyW' : 'KeyS')
      if (Math.abs(alongRight) > 0.2) next.add(alongRight > 0 ? 'KeyD' : 'KeyA')
      if (next.size === 0) break
      await setKeys(next)
      await page.waitForTimeout(110)
      if (Date.now() - progressAt > 3200) break
    }
  } finally {
    await setKeys(new Set())
  }
  await page.waitForTimeout(120)
  return readState(page)
}

async function drawRoom(page, image, box) {
  const bounds = await image.boundingBox()
  assert.ok(bounds, 'Manual correction preview should be laid out')
  await page.mouse.move(bounds.x + bounds.width * box.x0, bounds.y + bounds.height * box.y0)
  await page.mouse.down()
  await page.mouse.move(bounds.x + bounds.width * box.x1, bounds.y + bounds.height * box.y1, {
    steps: 10,
  })
  await page.mouse.up()
  await page.waitForTimeout(120)
}

async function fetchAsset(jobId) {
  const response = await fetch(`${engineUrl}/reconstructions/${jobId}/assets/scene.glb`)
  assert.ok(response.ok, 'GLB should be downloadable')
  const buffer = Buffer.from(await response.arrayBuffer())
  assert.equal(buffer.readUInt32LE(0), 0x46546c67, 'GLB magic')
  const chunkLength = buffer.readUInt32LE(12)
  const glb = JSON.parse(buffer.subarray(20, 20 + chunkLength).toString('utf8'))
  return { buffer, glb, hash: sha256(buffer) }
}

async function main() {
  const plan = ensureFixtures()
  const planBytes = fs.readFileSync(plan)
  fs.mkdirSync(screenshotDir, { recursive: true })
  const health = await fetchJson(`${engineUrl}/health`)
  assert.equal(health.dependencies.furnishing.requires_external_vision_api, false)

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
    assert.equal(await page.locator('#photos').count(), 0, 'No photo input should exist')
    await page.fill('#project-name', 'E2E open plan correction')
    await page.fill('#known-width', '12')
    await page.fill('#known-length', '9')
    await page.setInputFiles('#floor-plan', plan)
    await page.click('button[type="submit"]')

    await page.getByText('Detection stopped at Detect rooms and openings').waitFor({
      state: 'visible',
      timeout: 180000,
    })
    const failureText = await page.locator('body').innerText()
    assert.match(failureText, /no_enclosed_rooms/)
    assert.match(failureText, /No enclosed rooms were found/i)
    assert.match(failureText, /Retry with different preprocessing/i)
    assert.match(failureText, /Correct room outlines manually/i)
    assert.match(failureText, /Your original upload is preserved/i)
    const jobMatch = failureText.match(/Job ([0-9a-f-]{36})/)
    assert.ok(jobMatch, 'Failed job id should be visible')
    const jobId = jobMatch[1]

    const failedJob = await fetchJson(`${engineUrl}/reconstructions/${jobId}`)
    assert.equal(failedJob.status, 'failed')
    assert.equal(failedJob.failed_stage, 'detecting_rooms_openings')
    assert.equal(failedJob.error_code, 'no_enclosed_rooms')
    assert.equal(failedJob.photo_count, 0)
    assert.equal(failedJob.inputs.floor_plan.sha256, sha256(planBytes))
    assert.equal(failedJob.furniture_count, 0)
    assert.equal(failedJob.scene_url, null)
    const refusedScene = await fetch(`${engineUrl}/reconstructions/${jobId}/scene`)
    assert.equal(refusedScene.status, 409, 'A failed job must not expose a substitute scene')

    const diagnostics = await fetchJson(`${engineUrl}/reconstructions/${jobId}/diagnostics`)
    assert.equal(diagnostics.failed_stage, 'detecting_rooms_openings')
    assert.equal(diagnostics.error_code, 'no_enclosed_rooms')
    assert.equal(diagnostics.manual_plan_supported, true)
    assert.equal(diagnostics.correction_supported, true)
    assert.equal(diagnostics.furniture_count, 0)
    assert.equal(diagnostics.photo_review, null)
    for (const name of ['walls_mask.png', 'parse_failure.json', 'barrier_overlay.png']) {
      assert.ok(
        diagnostics.artifacts.some((artifact) => artifact.name === name),
        `${name} should be saved for the failed job`,
      )
    }
    assert.match(diagnostics.error, /No enclosed rooms were found/i)

    const original = page.getByAltText('Original floor plan uploaded for this job')
    await original.scrollIntoViewIfNeeded()
    await page.waitForFunction(
      () => {
        const image = [...document.querySelectorAll('img')].find(
          (entry) => entry.alt === 'Original floor plan uploaded for this job',
        )
        return Boolean(image && image.complete && image.naturalWidth > 0)
      },
      null,
      { timeout: 15000 },
    )
    await page.getByAltText('Detection artifact walls_mask.png').waitFor({
      state: 'visible',
      timeout: 15000,
    })
    await page.screenshot({
      path: path.join(screenshotDir, 'failure-diagnostics.png'),
      fullPage: true,
    })

    await page.getByRole('button', { name: 'Correct room outlines manually' }).click()
    const preview = page.getByAltText('Floor plan for manual room-outline correction')
    await preview.scrollIntoViewIfNeeded()
    await page.waitForFunction(
      () => {
        const image = [...document.querySelectorAll('img')].find(
          (entry) => entry.alt === 'Floor plan for manual room-outline correction',
        )
        return Boolean(image && image.complete && image.naturalWidth > 0)
      },
      null,
      { timeout: 15000 },
    )
    await drawRoom(page, preview, { x0: 0.075, y0: 0.1, x1: 0.5, y1: 0.9 })
    await drawRoom(page, preview, { x0: 0.5, y0: 0.1, x1: 0.925, y1: 0.9 })
    await page.getByRole('button', { name: 'Place doorway' }).click()
    const bounds = await preview.boundingBox()
    assert.ok(bounds)
    await page.mouse.click(bounds.x + bounds.width * 0.5, bounds.y + bounds.height * 0.5)

    await page.getByText(/Room 2:/).waitFor({ state: 'visible' })
    await page.getByText(/Doorway 1:/).waitFor({ state: 'visible' })
    await page.screenshot({
      path: path.join(screenshotDir, 'manual-correction-outline.png'),
      fullPage: true,
    })

    await page.getByRole('button', { name: 'Reconstruct from corrected outline' }).click()
    const reviewPanel = page.locator('[data-testid="room-type-review"]')
    await reviewPanel.waitFor({ state: 'visible', timeout: 180000 })
    assert.equal(await reviewPanel.getAttribute('data-job-id'), jobId)
    const review = await fetchJson(`${engineUrl}/reconstructions/${jobId}/room-types`)
    assert.equal(review.rooms.length, 2)
    assert.equal(review.requires_confirmation, true)
    assert.equal(review.style, 'modern')
    const rooms = [...review.rooms].sort((left, right) => left.room_id - right.room_id)
    await page.selectOption(`#room-type-${rooms[0].room_id}`, 'bathroom')
    await page.selectOption(`#room-type-${rooms[1].room_id}`, 'living_room')
    await page.screenshot({
      path: path.join(screenshotDir, 'manual-correction-room-types.png'),
      fullPage: true,
    })
    await page
      .getByRole('button', { name: 'Confirm room types and generate walkthrough' })
      .click()
    const tourLink = page.getByRole('link', { name: 'Open 3D walkthrough' })
    await tourLink.waitFor({ state: 'visible', timeout: 180000 })
    const job = await fetchJson(`${engineUrl}/reconstructions/${jobId}`)
    assert.equal(job.status, 'completed', job.error)
    assert.equal(job.geometry_valid, true)
    assert.equal(job.navigation_available, true)
    assert.equal(job.room_types_confirmed, true)
    assert.equal(job.retry_count, 1)
    assert.ok(job.furniture_count > 0)
    assert.equal(job.manual_outline.rooms.length, 2)
    assert.equal(job.manual_outline.doorways.length, 1)
    assert.equal(job.manual_outline.doorways[0].orientation, 'vertical')
    const correctedDiagnostics = await fetchJson(`${engineUrl}/reconstructions/${jobId}/diagnostics`)
    assert.equal(correctedDiagnostics.parser.segmentation.source, 'manual_outline')
    assert.equal(correctedDiagnostics.failure_report.error_code, 'no_enclosed_rooms')
    assert.equal(correctedDiagnostics.furniture_count, job.furniture_count)
    assert.equal(correctedDiagnostics.room_types_confirmed, true)
    assert.ok(
      !job.limitations.some((limitation) => /photo/i.test(limitation)),
      `Floor-plan-only limitations must not mention photographs: ${JSON.stringify(job.limitations)}`,
    )

    const scene = await fetchJson(`${engineUrl}/reconstructions/${jobId}/scene`)
    assert.equal(scene.workflow, 'floor_plan_only')
    assert.equal(scene.room_graph.nodes.length, 2)
    assert.equal(scene.room_graph.edges.length, 1)
    assert.equal(scene.room_graph.connected, true)
    assert.equal(scene.room_graph.navigation_available, true)
    const nodeTypes = Object.fromEntries(
      scene.room_graph.nodes.map((node) => [node.id, node.room_type]),
    )
    assert.deepEqual(nodeTypes, {
      [rooms[0].room_id]: 'bathroom',
      [rooms[1].room_id]: 'living_room',
    })
    const bathroomNode = scene.room_graph.nodes.find((node) => node.id === rooms[0].room_id)
    const livingNode = scene.room_graph.nodes.find((node) => node.id === rooms[1].room_id)
    assert.ok(bathroomNode.furniture_count > 0)
    assert.ok(livingNode.furniture_count > 0)
    const bathroomAssets = new Set(bathroomNode.furniture.map((item) => item.asset_id))
    assert.ok(
      [...bathroomAssets].some((asset) => ['toilet', 'vanity', 'bathtub', 'shower'].includes(asset)),
      `Bathroom furniture should contain fixtures, found ${[...bathroomAssets]}`,
    )
    const livingAssets = new Set(livingNode.furniture.map((item) => item.asset_id))
    assert.ok(livingAssets.has('sofa'), 'Living room should contain a sofa')
    assert.ok(scene.collision.furniture.length > 0)
    assert.equal(scene.collision.doors.length, 1)
    assert.equal(scene.collision.doors[0].orientation, 'vertical')
    const glb = await fetchAsset(jobId)
    assert.ok(glb.buffer.length > 20_000)
    const glbFurniture = glb.glb.nodes.some((node) => node.name?.startsWith('Furniture_'))
    assert.ok(glbFurniture, 'Corrected scene should contain real furniture meshes')

    await page.screenshot({
      path: path.join(screenshotDir, 'manual-correction-completed.png'),
      fullPage: true,
    })

    await Promise.all([page.waitForURL(`**/tour/${jobId}`, { timeout: 60000 }), tourLink.click()])
    const start = await waitForViewer(page)
    assert.equal(start.jobId, jobId)
    assert.ok(Math.abs(start.y - 1.65) < 0.05)
    const body = await page.locator('body').innerText()
    assert.match(body, /2 rooms/)
    assert.match(body, new RegExp(`${job.furniture_count} furniture items`))
    assert.match(body, /All rooms reachable/)
    assert.doesNotMatch(body, /photograph/i)
    const startRoom = roomAt(scene.collision.rooms, start.x, start.z)
    assert.ok(startRoom, 'Spawn should be inside a corrected room')
    await page.waitForFunction(
      (roomId) =>
        document
          .querySelector('[data-testid="room-info-overlay"]')
          ?.getAttribute('data-room-id') === String(roomId),
      startRoom.id,
      { timeout: 15000 },
    )
    const startNode = scene.room_graph.nodes.find((node) => node.id === startRoom.id)
    assert.equal((await page.getByTestId('current-room').innerText()).trim(), startNode.name)
    const target = scene.room_graph.nodes.find((node) => node.id !== startRoom.id)
    const route = findWalkPath(scene, { x: target.center[0], z: target.center[1] })
    for (const waypoint of route.waypoints) {
      await walkTo(page, waypoint)
    }
    const end = await readState(page)
    assert.equal(end.roomId, target.id, 'The manual doorway must be walkable')
    const endRoom = roomAt(scene.collision.rooms, end.x, end.z)
    assert.equal(endRoom.id, target.id)
    await page.waitForFunction(
      (roomId) =>
        document
          .querySelector('[data-testid="room-info-overlay"]')
          ?.getAttribute('data-room-id') === String(roomId),
      target.id,
      { timeout: 15000 },
    )
    await page.screenshot({ path: path.join(screenshotDir, 'manual-correction-tour.png') })

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
          planSha256: sha256(planBytes),
          failure: {
            stage: diagnostics.failed_stage,
            errorCode: diagnostics.error_code,
            error: diagnostics.error,
            artifacts: diagnostics.artifacts.map((artifact) => artifact.name),
            sceneRefusedStatus: refusedScene.status,
          },
          correction: {
            rooms: job.manual_outline.rooms.length,
            doorways: job.manual_outline.doorways.length,
            roomTypes: nodeTypes,
            furniture: job.furniture_count,
            collisionItems: scene.collision.furniture.length,
            segmentationSource: correctedDiagnostics.parser.segmentation.source,
            glbSha256: glb.hash,
            viewer: {
              spawnRoom: startRoom.id,
              navigatedRoom: target.id,
              routeCells: route.cells,
            },
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
