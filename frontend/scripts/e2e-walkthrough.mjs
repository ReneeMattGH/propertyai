/**
 * End-to-end walkthrough test.
 *
 * Drives the real upload form twice with visibly different properties. Each
 * upload stops at the photo review stage, where the test inspects the real
 * classifier output, corrects an assignment, confirms every photograph, and
 * waits for the real reconstruction pipeline (including COLMAP SfM). It then
 * verifies uploaded bytes and job-specific assets through the API, opens both
 * tours, checks the room overlay and assigned thumbnails before and after
 * walking through a doorway, exercises collision and reload, forces a
 * thumbnail load failure to confirm the viewer reports it, and confirms
 * unsuitable duplicate photos are reported instead of hidden.
 *
 * The first property combines six real overlapping photographs with the
 * synthetic four-room test plan. The photographs exercise real SfM and photo
 * texture extraction; the plan is a development fixture. The second property
 * is a generated two-room fixture with a different palette and footprint.
 *
 * Prerequisites:
 *   - reconstruction engine at NEXT_PUBLIC_RECONSTRUCTION_URL (default :8002)
 *   - Next.js dev/prod server at FRONTEND_URL (default :3000)
 *   - Playwright Chromium available (npx playwright install chromium, or
 *     set CHROMIUM_PATH to a Chrome/Chromium executable)
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
  if (!fs.existsSync(plan)) {
    const result = spawnSync(path.join(engineDir, 'venv/bin/python'), ['create_test_floor_plan.py'], {
      cwd: engineDir,
      stdio: 'inherit',
    })
    if (result.status !== 0) throw new Error('Could not generate the floor plan fixture')
  }
  const photoDir = path.join(engineDir, 'tests/assets/sfm')
  const photos = fs
    .readdirSync(photoDir)
    .filter((name) => name.startsWith('overlap_') && name.endsWith('.jpg'))
    .sort()
    .map((name) => path.join(photoDir, name))
  if (photos.length !== 6) {
    throw new Error(`Expected six real overlap photographs in ${photoDir}`)
  }
  return { plan, photos }
}

function ensureSecondProperty() {
  const plan = path.join(engineDir, 'input/e2e_two_room_plan.png')
  const photos = [
    path.join(engineDir, 'input/e2e_beta_hall.jpg'),
    path.join(engineDir, 'input/e2e_beta_study.jpg'),
  ]
  if (!fs.existsSync(plan) || photos.some((photo) => !fs.existsSync(photo))) {
    const result = spawnSync(
      path.join(engineDir, 'venv/bin/python'),
      ['create_e2e_properties.py'],
      { cwd: engineDir, stdio: 'inherit' },
    )
    if (result.status !== 0) throw new Error('Could not generate the second property fixture')
  }
  return { plan, photos }
}

async function waitForEngine() {
  const response = await fetch(`${engineUrl}/health`).catch(() => null)
  if (!response?.ok) {
    throw new Error(`Reconstruction engine is not reachable at ${engineUrl}`)
  }
  const health = await response.json()
  assert.equal(health.dependencies.photogrammetry_available, true, 'pycolmap must be installed')
}

async function readState(page) {
  return page.evaluate(() => window.__walkthrough.getState())
}

const distance = (a, b) => Math.hypot(a.x - b.x, a.z - b.z)
const sha256 = (buffer) => crypto.createHash('sha256').update(buffer).digest('hex')

async function fetchJson(url) {
  const response = await fetch(url)
  if (!response.ok) {
    throw new Error(`${url} -> ${response.status} ${await response.text()}`)
  }
  return response.json()
}

function readGlbJson(buffer) {
  assert.equal(buffer.readUInt32LE(0), 0x46546c67, 'GLB magic')
  assert.equal(buffer.readUInt32LE(4), 2, 'GLB version')
  const chunkLength = buffer.readUInt32LE(12)
  const chunkType = buffer.readUInt32LE(16)
  assert.equal(chunkType, 0x4e4f534a, 'First GLB chunk should be JSON')
  return JSON.parse(buffer.subarray(20, 20 + chunkLength).toString('utf8'))
}

async function fetchSceneAsset(jobId) {
  const response = await fetch(`${engineUrl}/reconstructions/${jobId}/assets/scene.glb`)
  assert.ok(response.ok, `GLB request failed for ${jobId}`)
  const buffer = Buffer.from(await response.arrayBuffer())
  return { buffer, json: readGlbJson(buffer), hash: sha256(buffer) }
}

function roomAt(rooms, x, z) {
  return rooms.find((room) => {
    const [minX, minZ, width, height] = room.bounds
    return x >= minX && x <= minX + width && z >= minZ && z <= minZ + height
  })
}

function fileMatches(input, filePath) {
  const bytes = fs.readFileSync(filePath)
  return input.size_bytes === bytes.length && input.sha256 === sha256(bytes)
}

async function verifyFirstProperty(jobId, photoPaths, { roomId, roomTypes }) {
  const job = await fetchJson(`${engineUrl}/reconstructions/${jobId}`)
  assert.equal(job.status, 'completed', job.error)
  assert.ok(job.property_id?.startsWith('prop-'), 'Each upload needs its own property id')
  assert.equal(job.photo_count, photoPaths.length)
  assert.equal(job.assignments_confirmed, true)
  assert.equal(job.review_required, false)
  assert.deepEqual(
    job.inputs.photos.map((entry) => entry.original_filename),
    photoPaths.map((photo) => path.basename(photo)),
  )
  for (let index = 0; index < photoPaths.length; index += 1) {
    assert.ok(fileMatches(job.inputs.photos[index], photoPaths[index]), 'Uploaded photo hash mismatch')
  }

  const review = await fetchJson(`${engineUrl}/reconstructions/${jobId}/review`)
  assert.equal(review.confirmed, true)
  assert.equal(review.summary.assigned, photoPaths.length)
  assert.equal(review.summary.needs_review, 0)
  assert.ok(
    ['completed', 'partial'].includes(review.classifier?.status),
    `The real classifier should report a result, got ${review.classifier?.status}`,
  )
  for (const photo of review.photos) {
    assert.equal(photo.room_assignment?.confirmed, true)
    assert.equal(photo.room_assignment?.room_id, roomId)
    assert.ok(photo.classification, 'Every photograph needs a stored classification record')
    assert.ok(photo.thumbnail_url.startsWith(`/reconstructions/${jobId}/photos/`))
  }

  const report = await fetchJson(`${engineUrl}/reconstructions/${jobId}/report`)
  const photogrammetry = report.diagnostics.photogrammetry
  assert.ok(
    ['reconstructed', 'partial'].includes(photogrammetry.status),
    `Expected real SfM, got ${photogrammetry.status}`,
  )
  assert.ok(photogrammetry.registered_images >= 4, 'At least four photos should register')
  assert.ok(photogrammetry.sparse_points >= 100, 'SfM should produce sparse points')
  assert.equal(photogrammetry.engine.name, 'COLMAP')
  assert.ok(photogrammetry.mean_reprojection_error_px !== null)
  assert.equal(report.diagnostics.photo_review.assigned, photoPaths.length)
  const textures = report.diagnostics.geometry.textures
  assert.equal(textures.applied, true)
  assert.equal(textures.count, photoPaths.length)
  const textureSources = textures.sources.map((source) => source.file)
  for (const photo of photoPaths) {
    const stem = path.parse(photo).name
    assert.ok(
      textureSources.some((source) => source.includes(stem)),
      `Texture sources should include the uploaded photo ${stem}`,
    )
  }
  for (const source of textures.sources) {
    if (source.room_id !== undefined && source.room_id !== null) {
      assert.equal(source.room_id, roomId, 'Texture sources must belong to the assigned room')
    }
  }

  const scene = await fetchJson(`${engineUrl}/reconstructions/${jobId}/scene`)
  assert.equal(scene.model_url, `/reconstructions/${jobId}/assets/scene.glb`)
  assert.equal(scene.metadata.job_id, jobId)
  assert.equal(scene.metadata.method.photogrammetry_used_for_geometry, false)
  assert.equal(scene.metadata.method.textures.applied, true)
  assert.ok(scene.collision.walls.length > 0)
  assert.equal(scene.room_graph.connected, true)
  assert.equal(scene.room_graph.nodes.length, 4)
  assert.equal(scene.photo_manifest.review.status, 'confirmed')
  assert.equal(scene.metadata.job_id, scene.manifest.provenance.job_id)
  assert.equal(scene.manifest.provenance.property_id, job.property_id)

  const nodes = new Map(scene.room_graph.nodes.map((node) => [node.id, node]))
  const target = nodes.get(roomId)
  assert.ok(target, 'The room that owns the photographs should exist')
  assert.equal(target.photo_count, photoPaths.length)
  assert.equal(target.photo_ids.length, photoPaths.length)
  for (const photo of target.photos) {
    assert.ok(
      photo.thumbnail_url.startsWith(`/reconstructions/${jobId}/photos/`),
      'Room photo thumbnails must be scoped to this job',
    )
  }
  for (const [id, node] of nodes) {
    if (id === roomId) continue
    assert.equal(node.photo_count, 0, `Room ${id} must not inherit another room's photographs`)
    assert.deepEqual(node.photos, [], `Room ${id} should have no assigned photographs`)
  }
  for (const [rawRoomId, roomType] of Object.entries(roomTypes)) {
    const node = nodes.get(Number(rawRoomId))
    if (node) assert.equal(node.room_type, roomType, `Room ${rawRoomId} type should be user-confirmed`)
  }
  const assignments = new Map(scene.room_assignments.map((entry) => [entry.room_id, entry]))
  assert.equal(assignments.get(roomId).photos.length, photoPaths.length)
  for (const [id, entry] of assignments) {
    if (id === roomId) continue
    assert.deepEqual(entry.photos, [], `Room ${id} must stay untextured`)
  }

  const thumbnails = await Promise.all(
    target.photos.map(async (photo) => {
      const response = await fetch(`${engineUrl}${photo.thumbnail_url}`)
      assert.ok(response.ok, `Thumbnail ${photo.thumbnail_url} should load`)
      assert.match(response.headers.get('content-type') || '', /^image\//)
      return response.arrayBuffer()
    }),
  )
  assert.ok(thumbnails.every((buffer) => buffer.byteLength > 0))

  const glb = await fetchSceneAsset(jobId)
  assert.ok(glb.json.images?.length > 0, 'GLB should embed photo textures')
  assert.ok(glb.json.textures?.length > 0, 'GLB should embed photo texture samplers')
  assert.ok(glb.buffer.length > 100_000, 'GLB should contain real geometry and textures')
  fs.writeFileSync(path.join(screenshotDir, `scene-${jobId}.glb`), glb.buffer)

  return { job, report, scene, review, glb }
}

async function verifySecondProperty(jobId, photoPaths, firstGlbHash, { mapping, roomTypes }) {
  const job = await fetchJson(`${engineUrl}/reconstructions/${jobId}`)
  assert.equal(job.status, 'completed', job.error)
  assert.equal(job.photo_count, photoPaths.length)
  assert.equal(job.assignments_confirmed, true)
  assert.deepEqual(
    job.inputs.photos.map((entry) => entry.original_filename),
    photoPaths.map((photo) => path.basename(photo)),
  )
  for (let index = 0; index < photoPaths.length; index += 1) {
    assert.ok(fileMatches(job.inputs.photos[index], photoPaths[index]))
  }

  const review = await fetchJson(`${engineUrl}/reconstructions/${jobId}/review`)
  assert.equal(review.confirmed, true)
  assert.equal(review.summary.assigned, photoPaths.length)
  for (const photo of review.photos) {
    assert.equal(photo.room_assignment?.confirmed, true)
    assert.equal(photo.room_assignment?.room_id, mapping[photo.original_filename])
  }

  const scene = await fetchJson(`${engineUrl}/reconstructions/${jobId}/scene`)
  assert.equal(scene.model_url, `/reconstructions/${jobId}/assets/scene.glb`)
  assert.equal(scene.metadata.job_id, jobId)
  assert.equal(scene.metadata.rooms, 2)
  assert.equal(scene.room_graph.nodes.length, 2)
  assert.equal(scene.metadata.method.textures.applied, true)
  assert.equal(scene.photo_manifest.review.status, 'confirmed')
  const sources = scene.metadata.method.textures.sources.map((source) => source.file)
  assert.ok(sources.some((source) => source.includes('beta_hall')))
  assert.ok(sources.some((source) => source.includes('beta_study')))

  const nodes = new Map(scene.room_graph.nodes.map((node) => [node.id, node]))
  for (const [filename, roomId] of Object.entries(mapping)) {
    const node = nodes.get(roomId)
    assert.ok(node, `Room ${roomId} should exist`)
    const expectedPhoto = review.photos.find(
      (photo) => photo.original_filename === filename,
    )
    assert.deepEqual(node.photo_ids, [expectedPhoto.photo_id])
    assert.equal(node.photo_count, 1)
    assert.equal(node.name, review.room_type_labels[roomTypes[String(roomId)]])
  }

  const glb = await fetchSceneAsset(jobId)
  assert.ok(glb.json.images?.length > 0)
  assert.notEqual(glb.hash, firstGlbHash, 'Second property must not reuse the first GLB')
  fs.writeFileSync(path.join(screenshotDir, `scene-${jobId}.glb`), glb.buffer)
  return { job, scene, review, glb }
}

async function uploadViaApi({ name, plan, photos, width, length }) {
  const form = new FormData()
  form.append('project_name', name)
  form.append('floor_plan', new Blob([fs.readFileSync(plan)]), path.basename(plan))
  for (const photo of photos) {
    form.append('photos', new Blob([fs.readFileSync(photo)]), path.basename(photo))
  }
  if (width) form.append('known_width', String(width))
  if (length) form.append('known_length', String(length))
  const response = await fetch(`${engineUrl}/reconstructions`, { method: 'POST', body: form })
  if (response.status !== 201) {
    throw new Error(`Upload failed: ${response.status} ${await response.text()}`)
  }
  return response.json()
}

async function pollJob(
  jobId,
  statuses = ['completed', 'failed', 'cancelled'],
  timeoutMs = 300000,
) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const job = await fetchJson(`${engineUrl}/reconstructions/${jobId}`)
    if (statuses.includes(job.status)) return job
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  throw new Error(`Timed out waiting for job ${jobId} to reach ${statuses.join('/')}`)
}

async function waitForUiReview(page, timeoutMs = 300000) {
  const panel = page.locator('[data-testid="photo-review"]')
  await panel.waitFor({ state: 'visible', timeout: timeoutMs })
  const jobId = await panel.getAttribute('data-job-id')
  assert.ok(jobId, 'The review panel should expose its job id')
  const review = await fetchJson(`${engineUrl}/reconstructions/${jobId}/review`)
  return { panel, jobId, review }
}

async function setRoomType(page, roomId, roomType) {
  await page.locator(`[data-testid="room-type-${roomId}"]`).selectOption(roomType)
}

async function assignPhoto(page, review, filename, roomId) {
  const photo = review.photos.find((entry) => entry.original_filename === filename)
  assert.ok(photo, `Review should include ${filename}`)
  await page.locator(`[data-testid="assign-${photo.photo_id}"]`).selectOption(String(roomId))
  const card = page.locator(`[data-testid="review-photo-${photo.photo_id}"]`)
  assert.equal(
    await card.getAttribute('data-assigned-room'),
    String(roomId),
    `${filename} should be assigned to room ${roomId}`,
  )
  return photo
}

async function previewOriginal(page, firstPhoto) {
  const card = page.locator(`[data-testid="review-photo-${firstPhoto.photo_id}"]`)
  await card.getByTitle('Preview original photograph').click()
  const dialog = page.getByRole('dialog', { name: 'Photograph preview' })
  await dialog.waitFor({ state: 'visible' })
  const original = dialog.locator(`img[alt="Original photograph ${firstPhoto.original_filename}"]`)
  await original.waitFor({ state: 'visible' })
  const loaded = await original.evaluate(
    (element) => element.complete && element.naturalWidth > 0,
  )
  assert.ok(loaded, 'The original photograph preview should load from the engine')
  await dialog.getByRole('button', { name: 'Close' }).click()
  await dialog.waitFor({ state: 'detached' })
}

async function confirmUiAssignments(page) {
  const confirm = page.locator('[data-testid="review-confirm"]')
  await confirm.scrollIntoViewIfNeeded()
  assert.equal(
    await confirm.isDisabled(),
    false,
    'Confirm should be enabled once every usable photograph is assigned or excluded',
  )
  await confirm.click()
}

async function waitForTourLink(page, timeoutMs = 300000) {
  const link = page.getByRole('link', { name: 'Open 3D walkthrough' })
  await link.waitFor({ state: 'visible', timeout: timeoutMs })
  return link
}

async function confirmAssignmentsViaApi(jobId, review, mapping, roomTypes) {
  const assignments = review.photos
    .filter((photo) => photo.processing.status === 'ready')
    .map((photo) => {
      const roomId = mapping[photo.original_filename]
      return roomId === undefined
        ? { photo_id: photo.photo_id, excluded: true }
        : { photo_id: photo.photo_id, room_id: roomId }
    })
  const response = await fetch(`${engineUrl}/reconstructions/${jobId}/confirm-assignments`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ assignments, room_types: roomTypes }),
  })
  if (!response.ok) {
    throw new Error(
      `Confirm assignments failed: ${response.status} ${await response.text()}`,
    )
  }
  return response.json()
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
      if (remaining < 0.35) break
      if (remaining < closest - 0.05) {
        closest = remaining
        progressAt = Date.now()
      }
      const forward = { x: -Math.sin(state.yaw), z: -Math.cos(state.yaw) }
      const right = { x: Math.cos(state.yaw), z: -Math.sin(state.yaw) }
      const alongForward = dx * forward.x + dz * forward.z
      const alongRight = dx * right.x + dz * right.z
      const next = new Set()
      if (Math.abs(alongForward) > 0.25) next.add(alongForward > 0 ? 'KeyW' : 'KeyS')
      if (Math.abs(alongRight) > 0.25) next.add(alongRight > 0 ? 'KeyD' : 'KeyA')
      if (next.size === 0) break
      await setKeys(next)
      await page.waitForTimeout(120)
      if (Date.now() - progressAt > 2500) break
    }
  } finally {
    await setKeys(new Set())
  }
  return readState(page)
}

function doorwayRoute(scene, targetRoomId) {
  const spawnRoom = scene.room_graph.spawn_room
  const doors = new Map(scene.collision.doors.map((door) => [door.id, door]))
  const candidates = scene.room_graph.edges
    .filter((edge) => edge.from === spawnRoom || edge.to === spawnRoom)
    .map((edge) => ({ edge, door: doors.get(edge.door_id) }))
    .filter((entry) => entry.door)
    .sort(
      (a, b) =>
        Math.hypot(a.door.position[0] - scene.collision.spawn.position[0], a.door.position[1] - scene.collision.spawn.position[2]) -
        Math.hypot(b.door.position[0] - scene.collision.spawn.position[0], b.door.position[1] - scene.collision.spawn.position[2]),
    )
  assert.ok(candidates.length > 0, 'Spawn room should have at least one doorway')
  const chosen = targetRoomId
    ? candidates.find(
        (entry) => entry.edge.from === targetRoomId || entry.edge.to === targetRoomId,
      )
    : candidates[0]
  assert.ok(chosen, `No doorway connects the spawn room to room ${targetRoomId}`)
  const target = scene.room_graph.nodes.find(
    (node) => node.id === (chosen.edge.from === spawnRoom ? chosen.edge.to : chosen.edge.from),
  )
  assert.ok(target, 'Connected target room should exist')
  const [doorX, doorZ] = chosen.door.position
  const signX = Math.sign(target.center[0] - doorX) || 1
  const signZ = Math.sign(target.center[1] - doorZ) || 1
  const vertical = chosen.door.orientation === 'vertical'
  const approach = vertical
    ? { x: doorX - signX * 0.65, z: doorZ }
    : { x: doorX, z: doorZ - signZ * 0.65 }
  const beyond = vertical
    ? { x: doorX + signX * 0.65, z: doorZ }
    : { x: doorX, z: doorZ + signZ * 0.65 }
  return {
    targetRoom: target,
    waypoints: [approach, beyond, { x: target.center[0], z: target.center[1] }],
  }
}

async function main() {
  await waitForEngine()
  const { plan, photos } = ensureFixtures()
  const second = ensureSecondProperty()
  fs.mkdirSync(screenshotDir, { recursive: true })

  const browser = await chromium.launch({
    executablePath: findChromium(),
    headless: true,
    args: ['--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
  })
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  const consoleErrors = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('pageerror', (error) => consoleErrors.push(String(error)))

  try {
    await page.goto(`${frontendUrl}/create-tour`, { waitUntil: 'networkidle', timeout: 120000 })
    await page.fill('#project-name', 'E2E real-photo property')
    await page.fill('#known-width', '12')
    await page.fill('#known-length', '9')
    await page.setInputFiles('#floor-plan', plan)
    await page.setInputFiles('#photos', photos)
    await page.click('button[type="submit"]')

    const firstReview = await waitForUiReview(page)
    const jobId = firstReview.jobId
    const review = firstReview.review
    assert.equal(review.assignment_required, true)
    assert.equal(review.summary.ready, photos.length)
    assert.equal(review.summary.needs_review, photos.length)
    assert.ok(
      ['completed', 'partial'].includes(review.classifier?.status),
      `The real vision classifier should run; got ${review.classifier?.status}: ${review.classifier?.error}`,
    )
    const labelled = review.photos.filter((photo) =>
      ['classified', 'ambiguous'].includes(photo.classification?.status),
    )
    assert.ok(
      labelled.length >= photos.length - 1,
      `Real classification should cover the uploaded photographs; only ${labelled.length} were labelled`,
    )
    assert.ok(
      (await page.getByText('Classified as').count()) > 0,
      'The review should show the classifier category',
    )
    const confirmButton = page.locator('[data-testid="review-confirm"]')
    assert.equal(
      await confirmButton.isDisabled(),
      true,
      'Unassigned photographs must block confirmation',
    )
    assert.ok(
      (await page.getByText(/photograph\(s\) still need a room/).count()) > 0,
      'The review should state how many photographs still need a room',
    )
    await previewOriginal(page, review.photos[0])
    await page.screenshot({
      path: path.join(screenshotDir, 'photo-review-pending.png'),
      fullPage: true,
    })

    const [photoRoom, ...otherRooms] = [...review.rooms].sort(
      (left, right) => left.room_id - right.room_id,
    )
    assert.equal(otherRooms.length, 3, 'The four-room plan should expose four rooms')
    const roomTypes = {
      [photoRoom.room_id]: 'living_room',
      [otherRooms[0].room_id]: 'kitchen',
      [otherRooms[1].room_id]: 'bedroom',
      [otherRooms[2].room_id]: 'bathroom',
    }
    for (const [roomId, roomType] of Object.entries(roomTypes)) {
      await setRoomType(page, roomId, roomType)
    }

    // Deliberately mis-assign a photograph, then correct it, proving the
    // review step (not the classifier) decides the final room.
    const firstPhoto = review.photos[0]
    await assignPhoto(page, review, firstPhoto.original_filename, otherRooms[0].room_id)
    await page.screenshot({
      path: path.join(screenshotDir, 'photo-review-misassigned.png'),
      fullPage: true,
    })
    for (const photo of review.photos.filter((entry) => entry.processing.status === 'ready')) {
      await assignPhoto(page, review, photo.original_filename, photoRoom.room_id)
    }
    await page.screenshot({
      path: path.join(screenshotDir, 'photo-review-confirmed-ready.png'),
      fullPage: true,
    })
    await confirmUiAssignments(page)
    const tourLink = await waitForTourLink(page)
    await page.screenshot({ path: path.join(screenshotDir, 'create-tour-real-photos.png') })

    const first = await verifyFirstProperty(jobId, photos, {
      roomId: photoRoom.room_id,
      roomTypes,
    })
    const route = doorwayRoute(first.scene)

    await Promise.all([page.waitForURL(`**/tour/${jobId}`, { timeout: 60000 }), tourLink.click()])
    await page.waitForFunction(() => window.__walkthrough?.getState?.(), null, { timeout: 90000 })
    await page.waitForTimeout(1000)
    await page.screenshot({ path: path.join(screenshotDir, 'tour-real-photos-initial.png') })
    const start = await readState(page)
    assert.equal(start.jobId, jobId)
    assert.ok(Math.abs(start.y - 1.65) < 0.05, `Eye height should stay at 1.65 m, got ${start.y}`)
    assert.ok((await page.getByText('Measured dimensions').count()) > 0)
    assert.ok((await page.getByText(`${photos.length} photos`, { exact: false }).count()) > 0)

    const startRoom = roomAt(first.scene.collision.rooms, start.x, start.z)
    assert.ok(startRoom, 'Spawn should be inside a detected room')
    assert.equal(startRoom.id, first.scene.room_graph.spawn_room)
    assert.equal(start.roomId, startRoom.id, 'The viewer should report the current room id')
    await page.waitForFunction(
      (roomId) => window.__walkthrough.getState().roomId === roomId,
      startRoom.id,
      { timeout: 15000 },
    )
    const overlay = page.getByTestId('room-info-overlay')
    const startNode = first.scene.room_graph.nodes.find((node) => node.id === startRoom.id)
    await page.waitForFunction(
      (roomId) =>
        document
          .querySelector('[data-testid="room-info-overlay"]')
          ?.getAttribute('data-room-id') === String(roomId),
      startRoom.id,
      { timeout: 15000 },
    )
    assert.equal(await overlay.getAttribute('data-room-id'), String(startRoom.id))
    assert.equal(
      (await page.getByTestId('current-room').innerText()).trim(),
      startNode.name,
      'The overlay should name the room the player is standing in',
    )
    const overlayImages = page.locator('[data-testid="room-info-overlay"] img')
    assert.ok(
      (await overlayImages.count()) > 0,
      'The current room overlay should show its assigned photographs',
    )
    await page.waitForFunction(
      () => {
        const panel = document.querySelector('[data-testid="room-info-overlay"]')
        const images = panel ? [...panel.querySelectorAll('img')] : []
        return images.length > 0 && images.every((image) => image.complete && image.naturalWidth > 0)
      },
      null,
      { timeout: 15000 },
    )
    assert.ok(
      (await page.getByText(`${photos.length} assigned photographs`).count()) > 0,
      'The overlay should report the number of assigned photographs',
    )

    await page.getByRole('button', { name: 'Reconstruction details' }).click()
    const details = await page.locator('body').innerText()
    assert.match(details, /Photogrammetry/)
    assert.match(details, /reconstructed|partial/)
    assert.match(details, /Photo textures/)
    assert.match(details, new RegExp(`${photos.length} sets`))
    assert.match(details, /COLMAP/)
    await page.getByRole('button', { name: 'Hide details' }).click()

    for (const waypoint of route.waypoints) {
      await walkTo(page, waypoint)
    }
    const afterWalk = await readState(page)
    const endRoom = roomAt(first.scene.collision.rooms, afterWalk.x, afterWalk.z)
    assert.ok(endRoom, 'Player should remain inside the reconstructed rooms')
    assert.notEqual(endRoom.id, startRoom.id, 'Player should walk through a doorway into another room')
    assert.equal(endRoom.id, route.targetRoom.id)
    assert.equal(afterWalk.roomId, route.targetRoom.id)
    assert.ok(Math.abs(afterWalk.y - 1.65) < 0.05)
    assert.ok(
      distance(start, afterWalk) > 1.0,
      `Expected real movement; moved ${distance(start, afterWalk).toFixed(2)} m`,
    )
    await page.waitForFunction(
      (roomId) => window.__walkthrough.getState().roomId === roomId,
      route.targetRoom.id,
      { timeout: 15000 },
    )
    await page.waitForFunction(
      (roomId) =>
        document
          .querySelector('[data-testid="room-info-overlay"]')
          ?.getAttribute('data-room-id') === String(roomId),
      route.targetRoom.id,
      { timeout: 15000 },
    )
    assert.equal(
      await overlay.getAttribute('data-room-id'),
      String(route.targetRoom.id),
      'The overlay should follow the player through the doorway',
    )
    assert.equal(
      (await page.getByTestId('current-room').innerText()).trim(),
      route.targetRoom.name,
      'The overlay should name the room the player walked into',
    )
    assert.ok(
      (await page.getByText('No confirmed photo for this room').count()) > 0,
      'An unphotographed room should state that it uses neutral materials',
    )
    await page.screenshot({ path: path.join(screenshotDir, 'tour-real-photos-second-room.png') })

    await page.getByRole('button', { name: 'Reset', exact: true }).click()
    await page.waitForTimeout(200)
    const afterReset = await readState(page)
    assert.ok(distance(start, afterReset) < 0.05, 'Reset should return to the spawn point')
    await page.waitForFunction(
      (roomId) => window.__walkthrough.getState().roomId === roomId,
      startRoom.id,
      { timeout: 15000 },
    )
    await page.waitForFunction(
      (roomId) =>
        document
          .querySelector('[data-testid="room-info-overlay"]')
          ?.getAttribute('data-room-id') === String(roomId),
      startRoom.id,
      { timeout: 15000 },
    )
    assert.equal(await overlay.getAttribute('data-room-id'), String(startRoom.id))

    await page.keyboard.down('d')
    await page.waitForTimeout(2500)
    await page.keyboard.up('d')
    await page.waitForTimeout(200)
    const afterStrafe = await readState(page)
    assert.ok(
      distance(afterReset, afterStrafe) < 6.5,
      `Strafing should be stopped by the exterior wall; moved ${distance(afterReset, afterStrafe).toFixed(2)} m`,
    )
    assert.ok(Math.abs(afterStrafe.y - 1.65) < 0.05, 'Gravity should keep the player on the floor')
    await page.screenshot({ path: path.join(screenshotDir, 'tour-after-movement.png') })

    await page.reload({ waitUntil: 'networkidle', timeout: 90000 })
    await page.waitForFunction(() => window.__walkthrough?.getState?.(), null, { timeout: 90000 })
    const reopened = await readState(page)
    assert.equal(reopened.jobId, jobId, 'Reloaded tour should reopen the same job')
    assert.equal(reopened.roomId, startRoom.id, 'Reloaded tour should restart in the spawn room')
    await page.waitForFunction(
      (roomId) =>
        document
          .querySelector('[data-testid="room-info-overlay"]')
          ?.getAttribute('data-room-id') === String(roomId),
      startRoom.id,
      { timeout: 15000 },
    )
    assert.equal(await overlay.getAttribute('data-room-id'), String(startRoom.id))

    // Force thumbnail requests to fail and confirm the viewer reports the
    // failure and offers a retry rather than silently showing broken images.
    const brokenPage = await browser.newPage({ viewport: { width: 1280, height: 800 } })
    await brokenPage.route('**/photos/*/thumbnail', (route) => route.abort())
    await brokenPage.goto(`${frontendUrl}/tour/${jobId}`, {
      waitUntil: 'networkidle',
      timeout: 120000,
    })
    await brokenPage.waitForFunction(() => window.__walkthrough?.getState?.(), null, {
      timeout: 90000,
    })
    await brokenPage.getByText('preview failed').first().waitFor({ state: 'visible', timeout: 15000 })
    assert.ok(
      (await brokenPage.getByRole('button', { name: 'Reload tour' }).count()) > 0,
      'A failed photograph preview should offer a reload action',
    )
    await brokenPage.screenshot({
      path: path.join(screenshotDir, 'tour-photo-preview-failure.png'),
    })
    await brokenPage.close()

    await page.goto(`${frontendUrl}/create-tour`, { waitUntil: 'networkidle', timeout: 120000 })
    await page.fill('#project-name', 'E2E second property')
    await page.fill('#known-width', '8')
    await page.fill('#known-length', '6')
    await page.setInputFiles('#floor-plan', second.plan)
    await page.setInputFiles('#photos', second.photos)
    await page.click('button[type="submit"]')

    const secondReview = await waitForUiReview(page)
    const secondId = secondReview.jobId
    assert.notEqual(secondId, jobId)
    assert.equal(secondReview.review.assignment_required, true)
    assert.equal(secondReview.review.summary.ready, second.photos.length)
    const secondRooms = [...secondReview.review.rooms].sort(
      (left, right) => left.room_id - right.room_id,
    )
    assert.equal(secondRooms.length, 2)
    const secondMapping = {
      [path.basename(second.photos[0])]: secondRooms[0].room_id,
      [path.basename(second.photos[1])]: secondRooms[1].room_id,
    }
    const secondTypes = {
      [secondRooms[0].room_id]: 'hallway',
      [secondRooms[1].room_id]: 'living_room',
    }
    for (const [roomId, roomType] of Object.entries(secondTypes)) {
      await setRoomType(page, roomId, roomType)
    }
    for (const photo of secondReview.review.photos.filter(
      (entry) => entry.processing.status === 'ready',
    )) {
      const roomId = secondMapping[photo.original_filename]
      assert.ok(roomId !== undefined, `Unexpected second-property photograph ${photo.original_filename}`)
      await assignPhoto(page, secondReview.review, photo.original_filename, roomId)
    }
    await confirmUiAssignments(page)
    const secondLink = await waitForTourLink(page)

    const secondVerified = await verifySecondProperty(secondId, second.photos, first.glb.hash, {
      mapping: secondMapping,
      roomTypes: secondTypes,
    })
    await Promise.all([
      page.waitForURL(`**/tour/${secondId}`, { timeout: 60000 }),
      secondLink.click(),
    ])
    await page.waitForFunction(() => window.__walkthrough?.getState?.(), null, { timeout: 90000 })
    await page.waitForTimeout(1000)
    const secondState = await readState(page)
    assert.equal(secondState.jobId, secondId)
    const secondOverlay = await page.locator('body').innerText()
    assert.ok(secondOverlay.includes('2 photos'))
    assert.ok(secondOverlay.includes('2 rooms'))
    const secondOverlayPanel = page.getByTestId('room-info-overlay')
    assert.equal(await secondOverlayPanel.getAttribute('data-room-id'), String(secondState.roomId))
    const secondNode = secondVerified.scene.room_graph.nodes.find(
      (node) => node.id === secondState.roomId,
    )
    assert.ok(secondNode, 'The second tour should report a detected room')
    assert.equal(
      (await page.getByTestId('current-room').innerText()).trim(),
      secondNode.name,
    )
    assert.ok(
      (await page.getByText('1 assigned photograph', { exact: false }).count()) > 0,
      'The current room should report its single assigned photograph',
    )
    await page.screenshot({ path: path.join(screenshotDir, 'tour-second-property.png') })

    const duplicates = await uploadViaApi({
      name: 'E2E duplicate-photo limitation',
      plan,
      photos: [second.photos[0], second.photos[0], second.photos[0]],
      width: 12,
      length: 9,
    })
    const duplicatePending = await pollJob(duplicates.job_id, [
      'awaiting_review',
      'failed',
      'cancelled',
    ])
    assert.equal(duplicatePending.status, 'awaiting_review', duplicatePending.error)
    const duplicateReview = await fetchJson(
      `${engineUrl}/reconstructions/${duplicates.job_id}/review`,
    )
    assert.equal(duplicateReview.summary.ready, 3)
    assert.equal(
      duplicateReview.photos.filter((photo) => photo.quality?.duplicate_of).length,
      2,
      'The review should expose duplicate photographs instead of hiding them',
    )
    const duplicateRoom = [...duplicateReview.rooms].sort(
      (left, right) => left.room_id - right.room_id,
    )[0]
    const duplicateMapping = Object.fromEntries(
      duplicateReview.photos.map((photo) => [photo.original_filename, duplicateRoom.room_id]),
    )
    await confirmAssignmentsViaApi(
      duplicates.job_id,
      duplicateReview,
      duplicateMapping,
      { [duplicateRoom.room_id]: 'living_room' },
    )
    const duplicateJob = await pollJob(duplicates.job_id)
    assert.equal(duplicateJob.status, 'completed', duplicateJob.error)
    assert.equal(duplicateJob.assignments_confirmed, true)
    assert.ok(
      duplicateJob.warnings.some((warning) => /duplicate photograph/i.test(warning)),
      `Expected an explicit duplicate warning, got ${JSON.stringify(duplicateJob.warnings)}`,
    )
    assert.equal(duplicateJob.diagnostics.photogrammetry.status, 'insufficient_overlap')
    assert.equal(duplicateJob.diagnostics.photogrammetry.registered_images, 0)
    const duplicateTextures = duplicateJob.diagnostics.photos.textures
    assert.equal(duplicateTextures.filter((entry) => entry.duplicate_of).length, 2)
    assert.ok(
      duplicateJob.limitations.some((item) => /did not overlap enough/i.test(item)),
      'The limitation should state that the photos did not overlap enough',
    )

    const fatalErrors = consoleErrors.filter(
      (message) =>
        !/Download the React DevTools|WebGL|Warning: React does not recognize/i.test(message),
    )
    assert.deepEqual(fatalErrors, [], `Console errors: ${fatalErrors.join('\n')}`)

    console.log(
      JSON.stringify(
        {
          status: 'passed',
          firstProperty: {
            jobId,
            propertyId: first.job.property_id,
            photos: first.job.photo_count,
            classifier: {
              status: review.classifier?.status,
              model: review.classifier?.model,
              labels: labelled.length,
            },
            assignments: {
              roomId: photoRoom.room_id,
              roomType: roomTypes[photoRoom.room_id],
              counts: first.scene.room_graph.nodes.map((node) => ({
                roomId: node.id,
                roomType: node.room_type,
                photos: node.photo_count,
              })),
              correctedPhoto: firstPhoto.original_filename,
            },
            sfm: {
              status: first.report.diagnostics.photogrammetry.status,
              registered: first.report.diagnostics.photogrammetry.registered_images,
              sparsePoints: first.report.diagnostics.photogrammetry.sparse_points,
              reprojectionErrorPx:
                first.report.diagnostics.photogrammetry.mean_reprojection_error_px,
            },
            textures: first.report.diagnostics.geometry.textures.count,
            rooms: first.scene.metadata.rooms,
            glbSha256: first.glb.hash,
            viewer: {
              spawnRoom: startRoom.id,
              spawnName: startNode.name,
              navigatedRoom: endRoom.id,
              navigatedName: route.targetRoom.name,
              reloadedJob: reopened.jobId,
            },
          },
          secondProperty: {
            jobId: secondId,
            propertyId: secondVerified.job.property_id,
            rooms: secondVerified.scene.metadata.rooms,
            assignments: secondMapping,
            roomTypes: secondTypes,
            glbSha256: secondVerified.glb.hash,
          },
          duplicatePhotos: {
            jobId: duplicateJob.job_id,
            status: duplicateJob.status,
            reviewDuplicates: duplicateReview.photos.filter(
              (photo) => photo.quality?.duplicate_of,
            ).length,
            photogrammetry: duplicateJob.diagnostics.photogrammetry.status,
            warning: duplicateJob.warnings.find((warning) => /duplicate photograph/i.test(warning)),
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
