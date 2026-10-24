/**
 * End-to-end floor-plan-only walkthrough test.
 *
 * Drives the real upload form without any photographs, confirms the inferred
 * room types, and verifies the real reconstruction: the uploaded plan hash,
 * furnished scene, real 3D furniture meshes, collision data and navigation
 * graph. Then it opens the walkthrough, checks the room overlay, walks through
 * a doorway using a path computed from the actual collision boxes, checks that
 * furniture blocks the player when visible and no longer blocks when hidden,
 * reloads the tour, changes the furnishing style (materials only), regenerates
 * the layout (positions only), leaves a room unspecified (must stay empty),
 * then restores it. A second, visibly different property proves jobs do not
 * share geometry or assets.
 *
 * Prerequisites:
 *   - reconstruction engine at NEXT_PUBLIC_RECONSTRUCTION_URL (default :8002)
 *   - Next.js dev/prod server at FRONTEND_URL (default :3000)
 *   - Playwright Chromium available (npx playwright install chromium, or set
 *     CHROMIUM_PATH to a Chrome/Chromium executable)
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
const FLOW_STAGES = [
  'input_validation',
  'floor_plan_parse',
  'room_type_inference',
  'room_types',
  'furnishing_plan',
  'geometry_build',
  'scene_validation',
  'manifest',
]

function findChromium() {
  if (process.env.CHROMIUM_PATH) return process.env.CHROMIUM_PATH
  const systemCandidates = [
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
  ]
  for (const candidate of systemCandidates) {
    if (fs.existsSync(candidate)) return candidate
  }
  const cache = path.join(os.homedir(), 'Library/Caches/ms-playwright')
  if (fs.existsSync(cache)) {
    const revisions = fs
      .readdirSync(cache)
      .filter((name) => name.startsWith('chromium-'))
      .sort()
      .reverse()
    for (const revision of revisions) {
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
  throw new Error(
    'No Chromium executable found. Run `npx playwright install chromium` or set CHROMIUM_PATH.',
  )
}

function ensureFixtures() {
  const plan = path.join(engineDir, 'input/test_floor_plan.png')
  const secondPlan = path.join(engineDir, 'input/e2e_two_room_plan.png')
  if (!fs.existsSync(plan) || !fs.existsSync(secondPlan)) {
    const result = spawnSync(
      path.join(engineDir, 'venv/bin/python'),
      ['create_e2e_properties.py'],
      { cwd: engineDir, stdio: 'inherit' },
    )
    if (result.status !== 0) throw new Error('Could not generate the floor plan fixtures')
  }
  if (!fs.existsSync(plan)) {
    throw new Error(`Missing primary fixture ${plan}`)
  }
  return { plan, secondPlan }
}

async function waitForEngine() {
  const response = await fetch(`${engineUrl}/health`).catch(() => null)
  if (!response?.ok) throw new Error(`Reconstruction engine is not reachable at ${engineUrl}`)
  const health = await response.json()
  assert.equal(
    health.dependencies.furnishing?.requires_external_vision_api,
    false,
    'Furnishing must not require an external vision API',
  )
  assert.ok(health.dependencies.furnishing?.asset_library_size > 10)
  return health
}

async function fetchJson(url) {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`${url} -> ${response.status} ${await response.text()}`)
  return response.json()
}

const sha256 = (buffer) => crypto.createHash('sha256').update(buffer).digest('hex')
const distance = (a, b) => Math.hypot(a.x - b.x, a.z - b.z)

function readGlbJson(buffer) {
  assert.equal(buffer.readUInt32LE(0), 0x46546c67, 'GLB magic')
  assert.equal(buffer.readUInt32LE(4), 2, 'GLB version')
  const chunkLength = buffer.readUInt32LE(12)
  assert.equal(buffer.readUInt32LE(16), 0x4e4f534a, 'First GLB chunk should be JSON')
  return JSON.parse(buffer.subarray(20, 20 + chunkLength).toString('utf8'))
}

async function fetchSceneAsset(jobId) {
  const response = await fetch(`${engineUrl}/reconstructions/${jobId}/assets/scene.glb`)
  assert.ok(response.ok, `GLB request failed for ${jobId}`)
  const buffer = Buffer.from(await response.arrayBuffer())
  return { buffer, json: readGlbJson(buffer), hash: sha256(buffer) }
}

async function fetchJob(jobId) {
  return fetchJson(`${engineUrl}/reconstructions/${jobId}`)
}

async function pollJob(jobId, statuses = ['completed', 'failed', 'cancelled'], timeoutMs = 120000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const job = await fetchJob(jobId)
    if (statuses.includes(job.status)) return job
    await new Promise((resolve) => setTimeout(resolve, 400))
  }
  throw new Error(`Timed out waiting for job ${jobId} to reach ${statuses.join('/')}`)
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

function geometrySignature(scene) {
  return JSON.stringify({
    nodes: scene.room_graph.nodes
      .map((node) => ({
        id: node.id,
        polygon: node.polygon,
        bounds: node.bounds,
        center: node.center,
        area: node.area_m2,
      }))
      .sort((left, right) => left.id - right.id),
    edges: scene.room_graph.edges,
    spawn: scene.collision.spawn,
    walls: scene.collision.walls,
    doors: scene.collision.doors,
  })
}

function furnitureSignature(scene) {
  return JSON.stringify(
    [...scene.furniture]
      .map((item) => ({
        id: item.id,
        room_id: item.room_id,
        asset_id: item.asset_id,
        position: item.position,
        rotation_deg: item.rotation_deg,
      }))
      .sort((left, right) => left.id.localeCompare(right.id)),
  )
}

function requireAssets(items, prefixes, label) {
  const assets = new Set(items.map((item) => item.asset_id))
  const found = prefixes.some((prefix) =>
    [...assets].some((asset) => asset === prefix || asset.startsWith(`${prefix}_`)),
  )
  assert.ok(
    found,
    `${label} should contain one of ${prefixes.join('/')}; found ${[...assets].join(', ')}`,
  )
}

function assertFurnitureInsideRooms(scene) {
  const nodes = new Map(scene.room_graph.nodes.map((node) => [node.id, node]))
  const rooms = new Map(scene.collision.rooms.map((room) => [room.id, room]))
  for (const item of scene.furniture) {
    const node = nodes.get(item.room_id)
    const room = rooms.get(item.room_id)
    assert.ok(node, `${item.id} must reference a real room`)
    assert.ok(room, `${item.id} room ${item.room_id} must have collision bounds`)
    assert.equal(node.room_type, item.room_type, `${item.id} room type must match its room`)
    const [minX, minZ, width, height] = room.bounds
    const tolerance = 0.06
    assert.ok(
      item.footprint.min[0] >= minX - tolerance &&
        item.footprint.min[1] >= minZ - tolerance &&
        item.footprint.max[0] <= minX + width + tolerance &&
        item.footprint.max[1] <= minZ + height + tolerance,
      `${item.id} ${JSON.stringify(item.footprint)} must stay inside room ${item.room_id} bounds ${room.bounds}`,
    )
  }
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

function nearestFreeCell(free, nx, nz, x, z, cell, footprint, radius = 0.8) {
  const originX = Math.floor(x / cell)
  const originZ = Math.floor(z / cell)
  let best = null
  for (let dz = -Math.ceil(radius / cell); dz <= Math.ceil(radius / cell); dz += 1) {
    for (let dx = -Math.ceil(radius / cell); dx <= Math.ceil(radius / cell); dx += 1) {
      const ix = originX + dx
      const iz = originZ + dz
      if (ix < 0 || iz < 0 || ix >= nx || iz >= nz) continue
      if (!free[iz * nx + ix]) continue
      const cx = (ix + 0.5) * cell
      const cz = (iz + 0.5) * cell
      const distanceToTarget = Math.hypot(cx - x, cz - z)
      if (distanceToTarget > radius) continue
      if (!best || distanceToTarget < best.distance) {
        best = { ix, iz, distance: distanceToTarget }
      }
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
      const x = (ix + 0.5) * cell
      const z = (iz + 0.5) * cell
      free[iz * nx + ix] = pointBlocked(x, z, boxes) ? 0 : 1
    }
  }
  const spawn = scene.collision.spawn.position
  const start = nearestFreeCell(free, nx, nz, spawn[0], spawn[2], cell, scene.collision.footprint)
  const goal = nearestFreeCell(
    free,
    nx,
    nz,
    target.x,
    target.z,
    cell,
    scene.collision.footprint,
    2.0,
  )

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
  assert.ok(found, `No walkable route from spawn to (${target.x.toFixed(2)}, ${target.z.toFixed(2)})`)
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
    while (next > anchor + 1 && !segmentClear(points[anchor], points[next], boxes)) {
      next -= 1
    }
    waypoints.push(points[next])
    anchor = next
  }
  return { waypoints, cells: points.length }
}

async function walkTo(page, target, timeoutMs = 15000) {
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

async function walkInto(page, target, durationMs) {
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
  const states = []
  const deadline = Date.now() + durationMs
  try {
    while (Date.now() < deadline) {
      const state = await readState(page)
      states.push(state)
      const dx = target.x - state.x
      const dz = target.z - state.z
      const forward = { x: -Math.sin(state.yaw), z: -Math.cos(state.yaw) }
      const right = { x: Math.cos(state.yaw), z: -Math.sin(state.yaw) }
      const alongForward = dx * forward.x + dz * forward.z
      const alongRight = dx * right.x + dz * right.z
      const next = new Set()
      if (alongForward > 0.08) next.add('KeyW')
      else if (alongForward < -0.08) next.add('KeyS')
      if (alongRight > 0.08) next.add('KeyD')
      else if (alongRight < -0.08) next.add('KeyA')
      await setKeys(next)
      await page.waitForTimeout(100)
    }
  } finally {
    await setKeys(new Set())
  }
  await page.waitForTimeout(120)
  return states
}

function distanceToBox(point, box) {
  const dx = Math.max(box.min[0] - point.x, 0, point.x - box.max[0])
  const dz = Math.max(box.min[1] - point.z, 0, point.z - box.max[1])
  return Math.hypot(dx, dz)
}

function furnitureBox(item) {
  return item.collision_bounds ?? { min: item.footprint.min, max: item.footprint.max }
}

function itemCenter(item) {
  const box = furnitureBox(item)
  return { x: (box.min[0] + box.max[0]) / 2, z: (box.min[1] + box.max[1]) / 2 }
}

async function uploadViaUi(page, { name, plan, width, length, style }) {
  await page.goto(`${frontendUrl}/create-tour`, { waitUntil: 'networkidle', timeout: 120000 })
  assert.equal(
    await page.locator('#photos').count(),
    0,
    'The floor-plan-only form must not ask for photographs',
  )
  await page.fill('#project-name', name)
  if (width) await page.fill('#known-width', String(width))
  if (length) await page.fill('#known-length', String(length))
  if (style) await page.selectOption('#furnishing-style', style)
  await page.setInputFiles('#floor-plan', plan)

  const body = await page.locator('body').innerText()
  assert.doesNotMatch(body, /upload (room )?photos/i)
  await page.click('button[type="submit"]')

  const panel = page.locator('[data-testid="room-type-review"]')
  await panel.waitFor({ state: 'visible', timeout: 180000 })
  const jobId = await panel.getAttribute('data-job-id')
  assert.ok(jobId, 'The room type panel should expose its job id')
  const review = await fetchJson(`${engineUrl}/reconstructions/${jobId}/room-types`)
  return { jobId, panel, review }
}

async function confirmRoomTypesViaUi(page, mapping) {
  const panel = page.locator('[data-testid="room-type-review"]')
  await panel.waitFor({ state: 'visible', timeout: 30000 })
  for (const [roomId, roomType] of Object.entries(mapping)) {
    await page.selectOption(`#room-type-${roomId}`, roomType)
  }
  await panel
    .getByRole('button', { name: 'Confirm room types and generate walkthrough' })
    .click()
  const link = page.getByRole('link', { name: 'Open 3D walkthrough' })
  await link.waitFor({ state: 'visible', timeout: 180000 })
  return link
}

async function waitForCreateSummary(page, job) {
  const body = await page.locator('body').innerText()
  assert.match(body, new RegExp(`${job.room_count} room`))
  assert.match(body, new RegExp(`${job.furniture_count} furniture item`))
  assert.match(body, new RegExp(`${job.furnishing_style} style`, 'i'))
}

async function verifyProperty(jobId, { planPath, expectedTypes, requiredAssets, minFurniture }) {
  const job = await fetchJob(jobId)
  assert.equal(job.status, 'completed', job.error)
  assert.equal(job.geometry_valid, true)
  assert.equal(job.navigation_available, true)
  assert.equal(job.room_types_confirmed, true)
  assert.equal(job.photo_count, 0, 'Floor-plan-only jobs must not require photographs')
  assert.deepEqual(job.inputs.photos, [])
  assert.equal(job.inputs.floor_plan.size_bytes, fs.statSync(planPath).size)
  assert.equal(job.inputs.floor_plan.sha256, sha256(fs.readFileSync(planPath)))
  assert.deepEqual(
    job.stage_records.map((record) => record.stage),
    FLOW_STAGES,
    'Every pipeline stage must run in order',
  )
  assert.notEqual(job.property_id, undefined)
  assert.ok(job.furniture_count >= minFurniture)

  const confirmed = new Map(job.room_types.map((entry) => [entry.room_id, entry]))
  for (const [roomId, roomType] of Object.entries(expectedTypes)) {
    const entry = confirmed.get(Number(roomId))
    assert.ok(entry, `Room ${roomId} should be in the confirmed room types`)
    assert.equal(entry.room_type, roomType)
    assert.equal(entry.source, 'user')
  }

  const validation = job.diagnostics?.scene_validation
  assert.ok(validation, 'Scene validation diagnostics should exist')
  assert.equal(validation.status, 'completed')
  assert.deepEqual(validation.errors, [])
  for (const [check, passed] of Object.entries(validation.checks)) {
    assert.equal(passed, true, `Scene validation check ${check} must pass`)
  }

  const scene = await fetchJson(`${engineUrl}/reconstructions/${jobId}/scene`)
  assert.equal(scene.workflow, 'floor_plan_only')
  assert.equal(scene.metadata.job_id, jobId)
  assert.equal(scene.job_id, jobId)
  assert.equal(scene.property_id, job.property_id)
  assert.equal(scene.model_url, `/reconstructions/${jobId}/assets/scene.glb`)
  assert.equal(scene.room_graph.connected, true)
  assert.equal(scene.room_graph.navigation_available, true)
  assert.equal(scene.room_graph.nodes.length, Object.keys(expectedTypes).length)
  assert.equal(scene.room_graph.edges.length, scene.collision.doors.length)
  const nodes = new Map(scene.room_graph.nodes.map((node) => [node.id, node]))
  for (const [roomId, roomType] of Object.entries(expectedTypes)) {
    const node = nodes.get(Number(roomId))
    assert.ok(node, `Room ${roomId} should exist in the scene graph`)
    assert.equal(node.room_type, roomType)
    assert.equal(node.photo_count, 0)
    assert.deepEqual(node.photos, [])
    assert.equal(node.furniture_count, node.furniture.length)
    assert.equal(
      node.area_m2 > 0,
      true,
      `Room ${roomId} should have a positive measured area`,
    )
  }

  const items = scene.furniture
  assert.ok(Array.isArray(items))
  assert.equal(items.length, job.furniture_count)
  assert.equal(new Set(items.map((item) => item.id)).size, items.length)
  for (const item of items) {
    for (const key of [
      'id',
      'room_id',
      'asset_id',
      'model_id',
      'position',
      'rotation_deg',
      'scale',
      'size_m',
      'footprint',
      'bbox',
      'collision',
      'collision_bounds',
      'materials',
      'material_style',
      'status',
    ]) {
      assert.ok(key in item, `${item.id} is missing ${key}`)
    }
    assert.equal(item.status, 'placed')
    assert.equal(nodes.get(item.room_id).furniture.some((entry) => entry.id === item.id), true)
  }
  assertFurnitureInsideRooms(scene)

  const collisionIds = new Set(scene.collision.furniture.map((entry) => entry.item_id))
  const expectedCollisionIds = new Set(items.filter((item) => item.collision).map((item) => item.id))
  assert.deepEqual([...collisionIds].sort(), [...expectedCollisionIds].sort())
  const spawn = scene.collision.spawn.position
  for (const box of scene.collision.furniture) {
    assert.ok(
      distanceToBox({ x: spawn[0], z: spawn[2] }, box) >= PLAYER_RADIUS - 0.02,
      `Spawn must not start inside furniture ${box.item_id}`,
    )
  }

  for (const [roomId, type] of Object.entries(expectedTypes)) {
    if (!requiredAssets[type]) continue
    const roomItems = items.filter((item) => item.room_id === Number(roomId))
    assert.ok(roomItems.length > 0, `Room ${roomId} (${type}) must be furnished`)
    for (const [label, prefixes] of Object.entries(requiredAssets[type])) {
      requireAssets(roomItems, prefixes, `${type} ${label}`)
    }
  }

  const furnitureDoc = await fetchJson(`${engineUrl}${job.furniture_url}`)
  assert.equal(furnitureDoc.status, 'completed')
  assert.equal(furnitureDoc.style, job.furnishing_style)
  assert.equal(furnitureDoc.variant, job.furnishing_variant)
  assert.equal(furnitureDoc.items.length, items.length)
  assert.equal(furnitureDoc.summary.rooms_furnished, Object.keys(expectedTypes).length)

  const glb = await fetchSceneAsset(jobId)
  assert.ok(glb.buffer.length > 50_000, 'GLB should contain real mesh geometry')
  assert.ok(glb.json.meshes?.length > 0)
  assert.equal(glb.json.images?.length ?? 0, 0, 'Floor-plan-only scenes must not fake photo textures')
  const nodeNames = glb.json.nodes.map((node) => node.name ?? '')
  const glbFurnitureIds = new Set()
  for (const name of nodeNames) {
    const match = /^Furniture_(.+?)__/.exec(name)
    if (match) glbFurnitureIds.add(match[1])
  }
  assert.deepEqual(
    [...glbFurnitureIds].sort(),
    items.map((item) => item.id).sort(),
    'Every furniture item must exist as real GLB mesh nodes',
  )
  assert.ok(
    glb.json.materials?.length > 0,
    'Procedural furniture should use real materials',
  )
  assert.equal(
    items.every((item) => item.materials.length > 0),
    true,
    'Every furniture item needs a material configuration',
  )
  assert.ok(
    !job.limitations.some((limitation) => /photo/i.test(limitation)),
    `Floor-plan-only limitations must not mention photographs: ${JSON.stringify(job.limitations)}`,
  )

  return { job, scene, furnitureDoc, glb }
}

function assertGeometryUnchanged(before, after, label) {
  assert.deepEqual(
    JSON.parse(geometrySignature(after)),
    JSON.parse(geometrySignature(before)),
    `${label} must not change walls, doors, room polygons or the spawn point`,
  )
}

async function waitForCompletedTour(
  page,
  jobId,
  { style, variant, roomTypeText } = {},
  timeoutMs = 120000,
) {
  const job = await pollJob(jobId, ['completed', 'failed'], timeoutMs)
  assert.equal(job.status, 'completed', job.error)
  if (style) {
    await page
      .getByText(new RegExp(`^${style} style$`, 'i'))
      .first()
      .waitFor({ state: 'visible', timeout: 30000 })
  }
  if (typeof variant === 'number') {
    const controls = page.getByTestId('furnishing-controls')
    await controls
      .getByText(new RegExp(`variant ${variant}\\b`))
      .waitFor({ state: 'visible', timeout: 30000 })
  }
  if (roomTypeText) {
    const summary = page.getByTestId('room-summary')
    await summary.getByText(roomTypeText, { exact: true }).waitFor({
      state: 'visible',
      timeout: 30000,
    })
  }
  await page.getByTestId('walkthrough-canvas').waitFor({ state: 'visible', timeout: 15000 })
  await waitForViewer(page)
  return job
}

function chooseCollisionProbe(scene, roomId) {
  const spawn = scene.collision.spawn.position
  const boxes = navBoxes(scene)
  const candidates = scene.furniture
    .filter((item) => item.room_id === roomId && item.collision)
    .sort(
      (left, right) =>
        distance(itemCenter(left), { x: spawn[0], z: spawn[2] }) -
        distance(itemCenter(right), { x: spawn[0], z: spawn[2] }),
    )
  for (const item of candidates) {
    const center = itemCenter(item)
    const ownBox = furnitureBox(item)
    const others = boxes.filter(
      (box) => !(box.min[0] === ownBox.min[0] && box.min[1] === ownBox.min[1]),
    )
    if (segmentClear({ x: spawn[0], z: spawn[2] }, center, others)) {
      return item
    }
  }
  throw new Error(`No unobstructed collision furniture to probe in room ${roomId}`)
}

async function main() {
  const health = await waitForEngine()
  const { plan, secondPlan } = ensureFixtures()
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
    const first = await uploadViaUi(page, {
      name: 'E2E floor-plan-only house',
      plan,
      width: 12,
      length: 9,
      style: 'scandinavian',
    })
    const jobId = first.jobId
    assert.equal(first.review.status, 'pending')
    assert.equal(first.review.requires_confirmation, true)
    assert.equal(first.review.rooms.length, 4)
    assert.equal(first.review.style, 'scandinavian')
    assert.equal(
      await first.panel.getAttribute('data-status'),
      'pending',
      'The review starts pending',
    )
    const pendingBody = await page.locator('body').innerText()
    assert.match(pendingBody, /Your review is required/i)
    assert.match(pendingBody, /Review room types/i)
    assert.doesNotMatch(pendingBody, /photograph\(s\) still need a room/i)
    await page.screenshot({
      path: path.join(screenshotDir, 'room-type-review-pending.png'),
      fullPage: true,
    })

    const reviewRooms = [...first.review.rooms].sort(
      (left, right) => left.room_id - right.room_id,
    )
    for (const room of reviewRooms) {
      assert.ok(room.room_type, `Room ${room.room_id} needs an inferred type`)
      assert.equal(typeof room.confidence, 'number')
      assert.ok(Array.isArray(room.reasons))
      assert.ok(room.polygon_px?.length >= 3, `Room ${room.room_id} needs a published outline`)
      assert.ok(room.bounds_px?.length === 4, `Room ${room.room_id} needs pixel bounds`)
    }

    const planFigure = page.getByTestId('room-plan')
    await planFigure.waitFor({ state: 'visible', timeout: 30000 })
    const planImage = planFigure.locator('img')
    await planImage.waitFor({ state: 'visible', timeout: 30000 })
    assert.equal(
      await planImage.evaluate((image) => image.complete && image.naturalWidth > 0),
      true,
      'The review must render the uploaded floor plan image',
    )
    const planRooms = page.locator(
      '[data-testid="room-plan-overlay"] [data-testid^="room-plan-room-"]',
    )
    assert.equal(
      await planRooms.count(),
      reviewRooms.length,
      'Every detected room must have a selectable outline on the plan',
    )

    const planTarget = reviewRooms[1]
    await page.getByTestId(`room-plan-room-${planTarget.room_id}`).click()
    assert.equal(
      await page.getByTestId(`room-card-${planTarget.room_id}`).getAttribute('data-selected'),
      'true',
      'Clicking a room on the plan must select the matching settings card',
    )
    await page.getByTestId('room-plan-selection').waitFor({ state: 'visible' })
    assert.match(await page.getByTestId('room-plan-selection').innerText(), /Selected:/)
    await page.locator(`#room-type-${planTarget.room_id}`).waitFor({ state: 'visible' })
    await page.locator(`#room-name-${planTarget.room_id}`).waitFor({ state: 'visible' })

    const firstTypes = {
      [reviewRooms[0].room_id]: 'living_room',
      [reviewRooms[1].room_id]: 'kitchen',
      [reviewRooms[2].room_id]: 'bedroom',
      [reviewRooms[3].room_id]: 'bathroom',
    }
    const customName = 'E2E Lounge'
    for (const [roomId] of Object.entries(firstTypes)) {
      await page.fill(
        `#room-name-${roomId}`,
        roomId === String(reviewRooms[0].room_id) ? customName : '',
      )
    }
    assert.match(
      await page.getByTestId(`room-plan-room-${reviewRooms[0].room_id}`).getAttribute('aria-label'),
      new RegExp(customName),
      'The plan outline label must reflect the display name being edited',
    )
    const tourLink = await confirmRoomTypesViaUi(page, firstTypes)
    const firstJob = await pollJob(jobId)
    assert.equal(firstJob.status, 'completed', firstJob.error)
    const namedRoom = firstJob.room_types.find(
      (entry) => entry.room_id === reviewRooms[0].room_id,
    )
    assert.ok(namedRoom, 'The display-named room must be confirmed')
    assert.equal(namedRoom.name, customName, 'A user-provided display name must persist')
    await waitForCreateSummary(page, firstJob)
    await page.screenshot({
      path: path.join(screenshotDir, 'create-tour-completed.png'),
      fullPage: true,
    })

    const firstVerified = await verifyProperty(jobId, {
      planPath: plan,
      expectedTypes: firstTypes,
      requiredAssets: {
        living_room: {
          sofa: ['sofa'],
          'coffee table': ['coffee_table'],
          'TV stand': ['tv_stand'],
          television: ['television'],
        },
        kitchen: {
          cabinets: ['kitchen_counter'],
          sink: ['kitchen_sink'],
          stove: ['kitchen_stove'],
          refrigerator: ['refrigerator'],
        },
        bedroom: {
          bed: ['bed_double', 'bed_king', 'bed_queen', 'bed_single'],
          nightstand: ['nightstand'],
          wardrobe: ['wardrobe_wide', 'wardrobe'],
        },
        bathroom: {
          toilet: ['toilet'],
          basin: ['vanity', 'sink'],
          'shower or bathtub': ['bathtub', 'shower'],
        },
      },
      minFurniture: 20,
    })
    const firstScene = firstVerified.scene

    await Promise.all([
      page.waitForURL(`**/tour/${jobId}`, { timeout: 60000 }),
      tourLink.click(),
    ])
    const start = await waitForViewer(page)
    assert.equal(start.jobId, jobId)
    assert.ok(Math.abs(start.y - 1.65) < 0.05, `Eye height should be 1.65 m, got ${start.y}`)
    const header = await page.locator('body').innerText()
    assert.match(header, /4 rooms/)
    assert.match(header, new RegExp(`${firstJob.furniture_count} furniture items`))
    assert.match(header, /Scandinavian style/i)
    assert.match(header, /Measured dimensions/)
    assert.match(header, /All rooms reachable/)
    assert.doesNotMatch(header, /photograph/i)
    const startRoom = roomAt(firstScene.collision.rooms, start.x, start.z)
    assert.ok(startRoom, 'Spawn should be inside a detected room')
    assert.equal(startRoom.id, firstScene.room_graph.spawn_room)
    assert.equal(start.roomId, startRoom.id)
    await page.waitForFunction(
      (roomId) =>
        document
          .querySelector('[data-testid="room-info-overlay"]')
          ?.getAttribute('data-room-id') === String(roomId),
      startRoom.id,
      { timeout: 15000 },
    )
    const startNode = firstScene.room_graph.nodes.find((node) => node.id === startRoom.id)
    assert.equal((await page.getByTestId('current-room').innerText()).trim(), startNode.name)
    assert.ok(
      (await page.getByText(`${startNode.furniture_count} furniture items`).count()) > 0,
      'The overlay should report the furniture in the current room',
    )
    await page.screenshot({ path: path.join(screenshotDir, 'tour-initial.png') })

    // Hiding furniture must be reflected in the control and in the scene.
    const furnitureToggle = page.getByRole('button', { name: 'Hide furniture' })
    await furnitureToggle.click()
    const showFurniture = page.getByRole('button', { name: 'Show furniture' })
    await showFurniture.waitFor({ state: 'visible', timeout: 10000 })
    assert.equal(await showFurniture.getAttribute('aria-pressed'), 'false')
    await page.screenshot({ path: path.join(screenshotDir, 'tour-furniture-hidden.png') })
    await showFurniture.click()
    await page.getByRole('button', { name: 'Hide furniture' }).waitFor({ state: 'visible' })

    // Walk a real collision-aware route through a doorway.
    const targetRoom = [...firstScene.room_graph.nodes]
      .filter((node) => node.id !== firstScene.room_graph.spawn_room)
      .sort((left, right) => {
        const leftDoor = firstScene.room_graph.edges.find(
          (edge) =>
            (edge.from === startRoom.id && edge.to === left.id) ||
            (edge.to === startRoom.id && edge.from === left.id),
        )
        const rightDoor = firstScene.room_graph.edges.find(
          (edge) =>
            (edge.from === startRoom.id && edge.to === right.id) ||
            (edge.to === startRoom.id && edge.from === right.id),
        )
        return (leftDoor ? 0 : 1) - (rightDoor ? 0 : 1) || left.id - right.id
      })[0]
    assert.ok(targetRoom, 'There must be another room connected to the spawn room')
    const route = findWalkPath(firstScene, {
      x: targetRoom.center[0],
      z: targetRoom.center[1],
    })
    for (const waypoint of route.waypoints) {
      await walkTo(page, waypoint, 20000)
    }
    const afterWalk = await readState(page)
    const endRoom = roomAt(firstScene.collision.rooms, afterWalk.x, afterWalk.z)
    assert.ok(endRoom, 'Player should stay inside the reconstructed rooms')
    assert.equal(endRoom.id, targetRoom.id, 'Player should reach the connected room')
    await page.waitForFunction(
      (roomId) => window.__walkthrough.getState().roomId === roomId,
      targetRoom.id,
      { timeout: 15000 },
    )
    await page.waitForFunction(
      (roomId) =>
        document
          .querySelector('[data-testid="room-info-overlay"]')
          ?.getAttribute('data-room-id') === String(roomId),
      targetRoom.id,
      { timeout: 15000 },
    )
    assert.equal(
      (await page.getByTestId('current-room').innerText()).trim(),
      targetRoom.name,
      'The overlay should follow the player through the doorway',
    )
    assert.ok(distance(start, afterWalk) > 1.0)
    await page.screenshot({ path: path.join(screenshotDir, 'tour-second-room.png') })

    // Collision with real furniture: visible furniture stops the player, while
    // hiding it removes its collider.
    const probe = chooseCollisionProbe(firstScene, startRoom.id)
    const probeCenter = itemCenter(probe)
    const probeBox = furnitureBox(probe)
    await page.getByRole('button', { name: 'Reset', exact: true }).click()
    await page.waitForTimeout(300)
    const visibleRun = await walkInto(page, probeCenter, 3200)
    const visibleClosest = Math.min(
      ...visibleRun.map((state) => distanceToBox(state, probeBox)),
    )
    assert.ok(
      visibleClosest >= PLAYER_RADIUS - 0.03,
      `${probe.id} should block the player; closest approach ${visibleClosest.toFixed(3)} m`,
    )

    await page.getByRole('button', { name: 'Hide furniture' }).click()
    await page.getByRole('button', { name: 'Reset', exact: true }).click()
    await page.waitForTimeout(300)
    const hiddenRun = await walkInto(page, probeCenter, 3200)
    const hiddenClosest = Math.min(
      ...hiddenRun.map((state) => distanceToBox(state, probeBox)),
    )
    assert.ok(
      hiddenClosest < 0.05,
      `Hiding furniture should remove ${probe.id} collision; closest approach ${hiddenClosest.toFixed(3)} m`,
    )
    assert.ok(
      hiddenClosest < visibleClosest - 0.15,
      'Furniture collision should measurably stop the player when visible',
    )
    await page.getByRole('button', { name: 'Show furniture' }).click()
    await page.getByRole('button', { name: 'Reset', exact: true }).click()
    await page.waitForTimeout(300)

    // Walls still block movement when furniture is visible.
    await page.keyboard.down('d')
    await page.waitForTimeout(2500)
    await page.keyboard.up('d')
    await page.waitForTimeout(200)
    const afterStrafe = await readState(page)
    const footprint = firstScene.collision.footprint
    assert.ok(
      afterStrafe.x >= 0 && afterStrafe.x <= footprint.width,
      `Strafing must stay inside the building; x=${afterStrafe.x.toFixed(2)}`,
    )
    assert.ok(
      afterStrafe.z >= 0 && afterStrafe.z <= footprint.height,
      `Strafing must stay inside the building; z=${afterStrafe.z.toFixed(2)}`,
    )
    assert.ok(Math.abs(afterStrafe.y - 1.65) < 0.05)

    // Reload must reopen the same job and spawn.
    await page.reload({ waitUntil: 'networkidle', timeout: 90000 })
    const reopened = await waitForViewer(page)
    assert.equal(reopened.jobId, jobId)
    assert.equal(reopened.roomId, startRoom.id)
    assert.ok(distance(reopened, { x: start.x, z: start.z }) < 0.6)

    // Style changes swap materials/models without touching the architecture.
    await page.getByRole('button', { name: 'Furnishing', exact: true }).click()
    const controls = page.getByTestId('furnishing-controls')
    await controls.waitFor({ state: 'visible', timeout: 15000 })
    await page.selectOption('[data-testid="furnishing-style"]', 'minimalist')
    await page.getByRole('button', { name: 'Apply style' }).click()
    const styleJob = await waitForCompletedTour(page, jobId, {
      style: 'minimalist',
      variant: firstJob.furnishing_variant,
    })
    assert.equal(styleJob.furnishing_style, 'minimalist')
    const styleScene = await fetchJson(`${engineUrl}/reconstructions/${jobId}/scene`)
    assertGeometryUnchanged(firstScene, styleScene, 'A style change')
    const styleGlb = await fetchSceneAsset(jobId)
    assert.notEqual(styleGlb.hash, firstVerified.glb.hash, 'A style change must rebuild the GLB')
    assert.equal(styleScene.furniture.length, firstVerified.scene.furniture.length)
    await page.screenshot({ path: path.join(screenshotDir, 'tour-style-minimalist.png') })

    // Regenerating picks another deterministic layout for the same rooms.
    const variantBefore = styleJob.furnishing_variant
    await page.getByRole('button', { name: 'Regenerate layout' }).click()
    const regenerateJob = await waitForCompletedTour(page, jobId, {
      style: 'minimalist',
      variant: variantBefore + 1,
    })
    assert.equal(regenerateJob.furnishing_variant, variantBefore + 1)
    const regenerateScene = await fetchJson(`${engineUrl}/reconstructions/${jobId}/scene`)
    assertGeometryUnchanged(firstScene, regenerateScene, 'Regenerating furniture')
    assert.notEqual(
      furnitureSignature(regenerateScene),
      furnitureSignature(styleScene),
      'Regenerating should produce a different deterministic arrangement',
    )
    await page.screenshot({ path: path.join(screenshotDir, 'tour-regenerated.png') })

    // A room left unspecified must stay empty rather than being filled.
    await page.getByRole('button', { name: 'Edit room types' }).click()
    const editPanel = page.locator('[data-testid="room-type-review"]')
    await editPanel.waitFor({ state: 'visible', timeout: 30000 })
    assert.equal(await editPanel.getAttribute('data-status'), 'confirmed')
    const unknownRoomId = Number(Object.entries(firstTypes).find(([, type]) => type === 'bathroom')[0])
    await page.selectOption(`#room-type-${unknownRoomId}`, 'unknown')
    await page.screenshot({
      path: path.join(screenshotDir, 'tour-room-types-edited.png'),
      fullPage: true,
    })
    await page
      .getByRole('button', { name: 'Apply changes and regenerate furniture' })
      .click()
    const unknownJob = await waitForCompletedTour(page, jobId, {
      style: 'minimalist',
      roomTypeText: 'unknown',
    })
    assert.equal(unknownJob.navigation_available, true)
    const unknownScene = await fetchJson(`${engineUrl}/reconstructions/${jobId}/scene`)
    assertGeometryUnchanged(firstScene, unknownScene, 'Changing a room type')
    const unknownNode = unknownScene.room_graph.nodes.find((node) => node.id === unknownRoomId)
    assert.equal(unknownNode.room_type, 'unknown')
    assert.equal(unknownNode.furniture_count, 0, 'Unspecified rooms must stay empty')
    const unknownTypes = await fetchJson(`${engineUrl}/reconstructions/${jobId}/room-types`)
    assert.equal(
      unknownTypes.rooms.find((room) => room.room_id === unknownRoomId).room_type,
      'unknown',
    )
    await page.screenshot({
      path: path.join(screenshotDir, 'tour-unknown-room.png'),
      fullPage: true,
    })

    // Restore the bathroom and leave the final walkthrough fully furnished.
    const restorePanel = page.locator('[data-testid="room-type-review"]')
    if (!(await restorePanel.isVisible().catch(() => false))) {
      await page.getByRole('button', { name: 'Edit room types' }).click()
    }
    await restorePanel.waitFor({ state: 'visible', timeout: 30000 })
    await page.selectOption(`#room-type-${unknownRoomId}`, 'bathroom')
    await page
      .getByRole('button', { name: 'Apply changes and regenerate furniture' })
      .click()
    const restoredJob = await waitForCompletedTour(page, jobId, {
      style: 'minimalist',
      roomTypeText: 'bathroom',
    })
    const restoredScene = await fetchJson(`${engineUrl}/reconstructions/${jobId}/scene`)
    const restoredGlb = await fetchSceneAsset(jobId)
    const restoredNode = restoredScene.room_graph.nodes.find(
      (node) => node.id === unknownRoomId,
    )
    assert.equal(restoredNode.room_type, 'bathroom')
    assert.ok(restoredNode.furniture_count > 0)
    assert.ok(restoredJob.furniture_count > 0)

    // A second plan must produce an independent, furnished scene.
    const second = await uploadViaUi(page, {
      name: 'E2E second floor-plan-only property',
      plan: secondPlan,
      width: 8,
      length: 6,
      style: 'modern',
    })
    const secondId = second.jobId
    assert.notEqual(secondId, jobId)
    assert.equal(second.review.rooms.length, 2)
    assert.equal(second.review.requires_confirmation, true)
    await page.screenshot({
      path: path.join(screenshotDir, 'second-property-review.png'),
      fullPage: true,
    })
    const secondRooms = [...second.review.rooms].sort(
      (left, right) => left.room_id - right.room_id,
    )
    const secondTypes = {
      [secondRooms[0].room_id]: 'hallway',
      [secondRooms[1].room_id]: 'living_room',
    }
    const secondLink = await confirmRoomTypesViaUi(page, secondTypes)
    const secondJob = await pollJob(secondId)
    assert.equal(secondJob.status, 'completed', secondJob.error)
    const secondVerified = await verifyProperty(secondId, {
      planPath: secondPlan,
      expectedTypes: secondTypes,
      requiredAssets: {
        living_room: {
          sofa: ['sofa'],
          'coffee table': ['coffee_table'],
          television: ['television'],
        },
      },
      minFurniture: 5,
    })
    const secondScene = secondVerified.scene
    assert.equal(secondScene.room_graph.edges.length, 1)
    assert.equal(secondScene.room_graph.connected, true)
    assert.notEqual(secondVerified.glb.hash, firstVerified.glb.hash)
    assert.notEqual(secondVerified.glb.hash, restoredGlb.hash)
    const hallwayNode = secondScene.room_graph.nodes.find(
      (node) => node.room_type === 'hallway',
    )
    assert.ok(hallwayNode)
    const hallwayAssets = new Set(hallwayNode.furniture.map((item) => item.asset_id))
    for (const forbidden of ['sofa', 'bed_king', 'bed_double', 'wardrobe_wide', 'dining_table_4']) {
      assert.equal(
        hallwayAssets.has(forbidden),
        false,
        `A hallway must not contain ${forbidden}`,
      )
    }
    await Promise.all([
      page.waitForURL(`**/tour/${secondId}`, { timeout: 60000 }),
      secondLink.click(),
    ])
    const secondState = await waitForViewer(page)
    assert.equal(secondState.jobId, secondId)
    const secondBody = await page.locator('body').innerText()
    assert.match(secondBody, /2 rooms/)
    assert.match(secondBody, /Modern style/i)
    const secondSpawnRoom = roomAt(secondScene.collision.rooms, secondState.x, secondState.z)
    assert.ok(secondSpawnRoom)
    const secondTarget = secondScene.room_graph.nodes.find(
      (node) => node.id !== secondSpawnRoom.id,
    )
    const secondRoute = findWalkPath(secondScene, {
      x: secondTarget.center[0],
      z: secondTarget.center[1],
    })
    for (const waypoint of secondRoute.waypoints) {
      await walkTo(page, waypoint, 20000)
    }
    const secondEnd = await readState(page)
    assert.equal(secondEnd.roomId, secondTarget.id)
    await page.screenshot({ path: path.join(screenshotDir, 'second-property-tour.png') })

    const fatalErrors = consoleErrors.filter(
      (message) =>
        !/Download the React DevTools|WebGL|Warning: React does not recognize/i.test(message),
    )
    assert.deepEqual(fatalErrors, [], `Console errors: ${fatalErrors.join('\n')}`)

    console.log(
      JSON.stringify(
        {
          status: 'passed',
          engine: {
            assetLibrarySize: health.dependencies.furnishing.asset_library_size,
            requiresExternalVisionApi:
              health.dependencies.furnishing.requires_external_vision_api,
          },
          firstProperty: {
            jobId,
            propertyId: firstJob.property_id,
            planSha256: firstJob.inputs.floor_plan.sha256,
            rooms: firstScene.metadata.rooms,
            furniture: firstJob.furniture_count,
            collisionItems: firstScene.collision.furniture.length,
            doorways: firstScene.room_graph.edges.length,
            roomTypes: firstTypes,
            requiredAssets: Object.fromEntries(
              firstScene.room_graph.nodes.map((node) => [
                node.id,
                [...new Set(node.furniture.map((item) => item.asset_id))],
              ]),
            ),
            glbSha256: firstVerified.glb.hash,
            styleAfterChange: styleJob.furnishing_style,
            variantAfterRegenerate: regenerateJob.furnishing_variant,
            unknownRoomFurniture: unknownNode.furniture_count,
            viewer: {
              spawnRoom: startRoom.id,
              navigatedRoom: endRoom.id,
              routeCells: route.cells,
              furnitureProbe: {
                item: probe.id,
                visibleClosestMeters: Number(visibleClosest.toFixed(3)),
                hiddenClosestMeters: Number(hiddenClosest.toFixed(3)),
              },
              reloadedJob: reopened.jobId,
            },
          },
          secondProperty: {
            jobId: secondId,
            propertyId: secondJob.property_id,
            rooms: secondScene.metadata.rooms,
            furniture: secondJob.furniture_count,
            roomTypes: secondTypes,
            hallwayAssets: [...hallwayAssets].sort(),
            glbSha256: secondVerified.glb.hash,
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
