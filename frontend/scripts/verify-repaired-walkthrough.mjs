/**
 * Verify a completed walkthrough against its real reconstruction data.
 *
 * - loads the tour in a real browser and checks the GLB actually renders
 * - routes from the spawn to a room through the real collision geometry,
 *   forcing the route through a chosen doorway centre
 * - walks there with the real keyboard controls and reports the room reached
 * - captures screenshots before and after passing through the doorway
 *
 * Usage:
 *   node scripts/verify-repaired-walkthrough.mjs <jobId> [doorId]
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
const PLAYER_RADIUS = 0.24

const jobId = process.argv[2]
const doorId = process.argv[3] ? Number(process.argv[3]) : null
assert.ok(jobId, 'Pass the job id to verify')

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
  throw new Error('No Chromium executable found. Run `npx playwright install chromium`.')
}

const fetchJson = async (url) => {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`${url} -> ${response.status}`)
  return response.json()
}

function pointBlocked(x, z, boxes) {
  // Keep a control margin beyond the physical capsule so the walker is not
  // threading 3 cm gaps that keyboard steering cannot reliably follow.
  const radius = PLAYER_RADIUS + 0.08
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
    if (pointBlocked(from.x + (to.x - from.x) * ratio, from.z + (to.z - from.z) * ratio, boxes)) {
      return false
    }
  }
  return true
}

function nearestFreeCell(free, nx, nz, x, z, cell, radius) {
  const originX = Math.floor(x / cell)
  const originZ = Math.floor(z / cell)
  let best = null
  const reach = Math.ceil(radius / cell)
  for (let dz = -reach; dz <= reach; dz += 1) {
    for (let dx = -reach; dx <= reach; dx += 1) {
      const ix = originX + dx
      const iz = originZ + dz
      if (ix < 0 || iz < 0 || ix >= nx || iz >= nz || !free[iz * nx + ix]) continue
      const cx = (ix + 0.5) * cell
      const cz = (iz + 0.5) * cell
      const distance = Math.hypot(cx - x, cz - z)
      if (distance > radius) continue
      if (!best || distance < best.distance) best = { ix, iz, distance }
    }
  }
  assert.ok(best, `No walkable cell near (${x.toFixed(2)}, ${z.toFixed(2)})`)
  return best
}

function doorCrossings(scene, from, to) {
  const crossings = []
  for (const door of scene.collision.doors ?? []) {
    const [centerX, centerZ] = door.position ?? []
    const width = Number(door.width_m ?? 0)
    if (!Number.isFinite(centerX) || !Number.isFinite(centerZ) || width <= 0) continue
    const horizontal = door.orientation === 'horizontal'
    const lineStart = horizontal ? from.z : from.x
    const lineEnd = horizontal ? to.z : to.x
    const lineValue = horizontal ? centerZ : centerX
    if ((lineStart - lineValue) * (lineEnd - lineValue) >= 0) continue
    const ratio = (lineValue - lineStart) / (lineEnd - lineStart)
    const runStart = horizontal ? from.x : from.z
    const runEnd = horizontal ? to.x : to.z
    const runValue = runStart + (runEnd - runStart) * ratio
    if (Math.abs(runValue - (horizontal ? centerX : centerZ)) > width / 2 - 0.02) continue
    crossings.push({ x: centerX, z: centerZ, ratio, doorId: door.id })
  }
  return crossings.sort((left, right) => left.ratio - right.ratio)
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
  const start = nearestFreeCell(free, nx, nz, spawn[0], spawn[2], cell, 1.0)
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
    for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
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
  const points = cells.map((entry) => ({ x: (entry.ix + 0.5) * cell, z: (entry.iz + 0.5) * cell }))
  const waypoints = []
  // Follow the A* corridor closely. Long line-of-sight shortcuts can graze a
  // wall corner that keyboard steering then wedges against, so cap each step
  // while still collapsing runs of collinear cells.
  const maxStep = 0.45
  let anchor = 0
  while (anchor < points.length - 1) {
    let next = anchor + 1
    while (
      next + 1 < points.length &&
      Math.hypot(points[next + 1].x - points[anchor].x, points[next + 1].z - points[anchor].z) <=
        maxStep &&
      segmentClear(points[anchor], points[next + 1], boxes)
    ) {
      next += 1
    }
    waypoints.push(points[next])
    anchor = next
  }
  const routed = []
  let previous = points[0]
  for (const waypoint of waypoints) {
    for (const crossing of doorCrossings(scene, previous, waypoint)) {
      if (segmentClear(previous, crossing, boxes) && segmentClear(crossing, waypoint, boxes)) {
        routed.push({ x: crossing.x, z: crossing.z, doorId: crossing.doorId })
        previous = crossing
      }
    }
    routed.push(waypoint)
    previous = waypoint
  }
  return routed
}

async function readState(page) {
  return page.evaluate(() => window.__walkthrough.getState())
}

async function setLook(page, yaw, pitch) {
  if (!(await page.evaluate(() => Boolean(document.pointerLockElement)))) {
    await page.locator('canvas').click({ position: { x: 700, y: 500 } })
    await page.waitForTimeout(200)
  }
  let cursor = { x: 700, y: 500 }
  for (let index = 0; index < 16; index += 1) {
    const state = await readState(page)
    const deltaYaw = ((yaw - state.yaw + Math.PI * 3) % (Math.PI * 2)) - Math.PI
    const deltaPitch = pitch - state.pitch
    if (Math.abs(deltaYaw) < 0.02 && Math.abs(deltaPitch) < 0.02) return
    cursor = {
      x: cursor.x + Math.max(-600, Math.min(600, -deltaYaw / 0.002)),
      y: cursor.y + Math.max(-400, Math.min(400, -deltaPitch / 0.002)),
    }
    await page.mouse.move(cursor.x, cursor.y, { steps: 3 })
    await page.waitForTimeout(90)
  }
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
    let recoveries = 0
    while (Date.now() < deadline) {
      const state = await readState(page)
      const dx = target.x - state.x
      const dz = target.z - state.z
      if (Math.hypot(dx, dz) < 0.15) break
      if (Math.hypot(dx, dz) < closest - 0.04) {
        closest = Math.hypot(dx, dz)
        progressAt = Date.now()
      }
      if (Date.now() - progressAt > 1200 && recoveries < 3) {
        // A straight push can wedge the capsule on a wall corner; back straight
        // out before steering at the target again.
        recoveries += 1
        await setKeys(new Set(['KeyS']))
        await page.waitForTimeout(380)
        await setKeys(new Set())
        await page.waitForTimeout(100)
        progressAt = Date.now()
        continue
      }
      const forward = { x: -Math.sin(state.yaw), z: -Math.cos(state.yaw) }
      const right = { x: Math.cos(state.yaw), z: -Math.sin(state.yaw) }
      const alongForward = dx * forward.x + dz * forward.z
      const alongRight = dx * right.x + dz * right.z
      // Drop a negligible secondary component so a target that is almost due
      // east does not drift north into furniture; keep both when the target
      // genuinely needs a diagonal (e.g. walking around a wall end to a door).
      const next = new Set()
      const absForward = Math.abs(alongForward)
      const absRight = Math.abs(alongRight)
      const dominant = Math.max(absForward, absRight)
      if (dominant < 0.08) break
      const both = Math.min(absForward, absRight) > dominant * 0.35
      if (both || absForward === dominant) {
        if (absForward > 0.05) next.add(alongForward > 0 ? 'KeyW' : 'KeyS')
      }
      if (both || absRight === dominant) {
        if (absRight > 0.05) next.add(alongRight > 0 ? 'KeyD' : 'KeyA')
      }
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

async function main() {
  const [job, scene] = await Promise.all([
    fetchJson(`${engineUrl}/reconstructions/${jobId}`),
    fetchJson(`${engineUrl}/reconstructions/${jobId}/scene`),
  ])
  assert.equal(job.status, 'completed', job.error)
  assert.equal(job.geometry_valid, true)
  assert.equal(job.navigation_available, true)
  const targetDoor =
    doorId != null
      ? scene.collision.doors.find((door) => door.id === doorId)
      : [...scene.collision.doors].sort((left, right) => left.width_m - right.width_m)[0]
  assert.ok(targetDoor, `Door ${doorId ?? ''} not found`)
  const [roomA, roomB] = targetDoor.rooms ?? []
  assert.ok(roomA != null && roomB != null, `Door ${targetDoor.id} must connect two rooms`)
  const spawnRoom = scene.collision.spawn.room_id
  const targetRoomId = spawnRoom === roomB ? roomA : roomB
  const targetNode = scene.room_graph.nodes.find((node) => node.id === targetRoomId)
  assert.ok(targetNode, `Target room ${targetRoomId} missing`)

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
    await page.goto(`${frontendUrl}/tour/${jobId}`, { waitUntil: 'networkidle', timeout: 120000 })
    await page.waitForFunction(() => window.__walkthrough?.getState?.(), null, { timeout: 90000 })
    await page.waitForTimeout(1500)
    const start = await readState(page)
    assert.equal(start.jobId, jobId)
    assert.ok(Math.abs(start.y - 1.63) < 0.06, `Eye height ${start.y}`)
    const stats = await page.evaluate(() => window.__walkthrough.getStats())
    assert.ok(stats.drawCalls > 20, `Scene should render many draw calls, got ${stats.drawCalls}`)
    assert.ok(stats.triangles > 5000, `Scene should render real geometry, got ${stats.triangles}`)
    await page.screenshot({ path: path.join(screenshotDir, `repair-${jobId}-spawn.png`) })

    const doorCenter = { x: targetDoor.position[0], z: targetDoor.position[1] }
    const horizontalDoor = targetDoor.orientation === 'horizontal'
    const crossAxis = horizontalDoor ? 'z' : 'x'
    const crossValue = doorCenter[crossAxis]
    const targetCross = targetNode.center[horizontalDoor ? 1 : 0]
    const direction = Math.sign(targetCross - crossValue) || 1
    const approach = { ...doorCenter }
    approach[crossAxis] = crossValue - direction * 0.55
    const farSide = { ...doorCenter }
    farSide[crossAxis] = crossValue + direction * 0.5
    const route = [
      ...findWalkPath(scene, approach),
      { ...doorCenter, doorId: targetDoor.id },
      farSide,
      { x: targetNode.center[0], z: targetNode.center[1] },
    ]
    let passedDoor = null
    let approachShot = false
    let closestToDoor = Number.POSITIVE_INFINITY
    for (const waypoint of route) {
      if (waypoint.doorId === targetDoor.id && !approachShot) {
        approachShot = true
        await setLook(page, Math.PI, -0.05)
        await page.screenshot({
          path: path.join(screenshotDir, `repair-${jobId}-doorway-${targetDoor.id}-approach.png`),
        })
      }
      const state = await walkTo(page, waypoint)
      const distanceToDoor = Math.hypot(
        state.x - targetDoor.position[0],
        state.z - targetDoor.position[1],
      )
      console.error(
        `waypoint (${waypoint.x.toFixed(2)}, ${waypoint.z.toFixed(2)}) -> ` +
          `(${state.x.toFixed(2)}, ${state.z.toFixed(2)}) dist ${Math.hypot(state.x - waypoint.x, state.z - waypoint.z).toFixed(2)}`,
      )
      if (distanceToDoor < closestToDoor) closestToDoor = distanceToDoor
      if (waypoint.doorId === targetDoor.id) {
        passedDoor = state
        await page.screenshot({
          path: path.join(screenshotDir, `repair-${jobId}-doorway-${targetDoor.id}.png`),
        })
      }
    }
    assert.ok(passedDoor, 'The route must cross the selected doorway')
    assert.ok(
      closestToDoor < targetDoor.width_m / 2 + 0.1,
      `Player should pass through door ${targetDoor.id}; closest ${closestToDoor.toFixed(2)} m`,
    )
    const end = await readState(page)
    assert.equal(end.roomId, targetRoomId, `Player should end in room ${targetRoomId}`)
    await setLook(page, 0, -0.05)
    await page.waitForTimeout(400)
    await page.screenshot({
      path: path.join(screenshotDir, `repair-${jobId}-room-${targetRoomId}.png`),
    })

    const fatal = consoleErrors.filter(
      (message) => !/Download the React DevTools|WebGL|Warning: React does not recognize/i.test(message),
    )
    assert.deepEqual(fatal, [], `Console errors: ${fatal.join('\n')}`)

    console.log(
      JSON.stringify(
        {
          status: 'passed',
          jobId,
          rooms: scene.room_graph.nodes.length,
          doorways: scene.collision.doors.length,
          targetDoor: {
            id: targetDoor.id,
            rooms: targetDoor.rooms,
            widthM: targetDoor.width_m,
            inferred: targetDoor.id === 14,
          },
          spawn: { room: spawnRoom, x: start.x, z: start.z },
          route: route.map((waypoint) => ({
            x: Number(waypoint.x.toFixed(2)),
            z: Number(waypoint.z.toFixed(2)),
            doorId: waypoint.doorId ?? null,
          })),
          passedDoorAt: {
            x: Number(passedDoor.x.toFixed(2)),
            z: Number(passedDoor.z.toFixed(2)),
            distanceToDoorCentre: Number(closestToDoor.toFixed(2)),
          },
          end: { room: end.roomId, x: Number(end.x.toFixed(2)), z: Number(end.z.toFixed(2)) },
          render: stats,
          screenshots: screenshotDir,
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
