export const RECONSTRUCTION_URL = (
  process.env.NEXT_PUBLIC_RECONSTRUCTION_URL || 'http://localhost:8002'
).replace(/\/+$/, '')

export interface ReconstructionJob {
  job_id: string
  property_id?: string | null
  status: string
  current_stage: string
  stage_label?: string | null
  progress: number
  created_at: string
  updated_at: string
  error?: string | null
  error_code?: string | null
  failed_stage?: string | null
  project_name?: string | null
  floor_plan_filename?: string | null
  photo_count?: number | null
  num_floors?: number | null
  known_dimensions?: Record<string, number> | null
  inputs?: Record<string, unknown> | null
  stage_records?: Record<string, unknown>[] | null
  diagnostics?: Record<string, unknown> | null
  assets?: AssetEntry[] | null
  warnings?: string[] | null
  limitations?: string[] | null
  geometry_valid?: boolean | null
  navigation_available?: boolean | null
  scale_verified?: boolean | null
  manifest_url?: string | null
  scene_url?: string | null
  floor_plan_url?: string | null
  diagnostics_url?: string | null
  retry_count?: number | null
  cancel_requested?: boolean | null
  manual_outline?: ManualOutline | null
  review_url?: string | null
  review_required?: boolean | null
  assignments_confirmed?: boolean | null
  room_count?: number | null
  photo_review_summary?: PhotoReviewSummary | null
  classifier?: Record<string, unknown> | null
}

export interface PhotoClassification {
  status: string
  model?: string | null
  predicted_room_type?: string | null
  predicted_label?: string | null
  confidence?: number | null
  ambiguous?: boolean | null
  candidates?: { room_type: string; label?: string; score: number }[]
  error?: string | null
  classified_at?: string | null
}

export interface PhotoSuggestion {
  status: string
  room_id?: number | null
  confidence?: number | null
  reason?: string | null
}

export interface ReviewPhoto {
  photo_id: string
  original_filename: string
  mime_type?: string | null
  width?: number | null
  height?: number | null
  aspect_ratio?: number | null
  orientation?: string | null
  thumbnail_url: string
  preview_url: string
  original_url: string
  classification: PhotoClassification
  room_assignment: {
    room_id: number | null
    confirmed: boolean
    excluded: boolean
    confirmed_at?: string | null
  }
  match: {
    status: string
    room_id?: number | null
    confidence?: number | null
    reason?: string | null
  }
  quality?: {
    brightness?: number | null
    contrast?: number | null
    sharpness?: number | null
    duplicate_of?: string | null
    warnings?: string[]
  }
  camera?: Record<string, unknown>
  visible?: Record<string, unknown>
  processing: { status: string; errors: string[] }
  suggestion?: PhotoSuggestion | null
  assigned_room?: ReviewRoom | null
}

export interface ReviewRoom {
  room_id: number
  default_name: string
  area_m2?: number | null
  center_m?: [number, number] | null
  bounds_m?: [number, number, number, number] | null
  polygon_m?: [number, number][]
  name: string
  room_type?: string | null
  assigned_photo_ids: string[]
  assigned_photo_count: number
  has_photos: boolean
}

export interface PhotoReviewSummary {
  total: number
  ready: number
  assigned: number
  excluded: number
  rejected: number
  needs_review: number
  rooms?: number
  rooms_without_photos?: number
  ambiguous_photos?: number
  unavailable_photos?: number
}

export interface PhotoReview {
  job_id: string
  property_id?: string | null
  status: string
  rooms: ReviewRoom[]
  photos: ReviewPhoto[]
  summary: PhotoReviewSummary
  assignment_required: boolean
  confirmed: boolean
  classifier?: Record<string, unknown> | null
  room_types: string[]
  room_type_labels: Record<string, string>
  plan: {
    scale_pixels_per_meter?: number | null
    scale_source?: string | null
    scale_verified?: boolean | null
    footprint_m?: Record<string, number> | null
    floor_plan_url: string
  }
}

export interface PhotoAssignmentInput {
  photo_id: string
  room_id?: number | null
  excluded?: boolean
}

export interface ManualRoomOutline {
  polygon_px: [number, number][]
}

export interface ManualDoorway {
  orientation: 'horizontal' | 'vertical'
  center_px: [number, number]
  width_px?: number | null
}

export interface ManualOutline {
  rooms: ManualRoomOutline[]
  doorways: ManualDoorway[]
  wall_thickness_px?: number | null
}

export interface DiagnosticArtifact {
  name: string
  url: string
  type: string
  size_bytes: number
}

export interface JobDiagnostics {
  job_id: string
  status: string
  error?: string | null
  error_code?: string | null
  failed_stage?: string | null
  floor_plan_url: string
  preprocessing: {
    used: Record<string, string>
    options: { wall_polarity: string; sealing: string; label: string }[]
  }
  parser: Record<string, unknown>
  failure_report: Record<string, unknown> | null
  artifacts: DiagnosticArtifact[]
  correction_supported: boolean
  manual_plan_supported: boolean
  manual_outline?: ManualOutline | null
  retry_supported: boolean
}

export interface AssetEntry {
  name: string
  type: string
  size_bytes: number
  sha256: string
}

export interface Doorway {
  id: number
  position: [number, number]
  orientation: 'horizontal' | 'vertical'
  width_m: number
  detected_width_m?: number
  rooms: [number | null, number | null]
  exterior?: boolean
}

export interface CollisionRoom {
  id: number
  bounds: [number, number, number, number]
  center: [number, number]
  area_m2?: number
}

export interface CollisionData {
  units: string
  walls: { min: [number, number]; max: [number, number]; exterior?: boolean }[]
  doors: Doorway[]
  rooms: CollisionRoom[]
  spawn: { position: [number, number, number]; yaw: number }
  footprint?: { width: number; height: number }
  wall_height: number
  door_height: number
}

export interface RoomGraphNode {
  id: number
  name: string
  room_type?: string | null
  center: [number, number]
  bounds: [number, number, number, number]
  area_m2?: number
  polygon?: [number, number][]
  photo_ids?: string[]
  photo_count?: number
  photos?: { photo_id: string; thumbnail_url: string | null }[]
}

export interface RoomGraph {
  nodes: RoomGraphNode[]
  edges: {
    door_id: number
    from: number
    to: number
    position: [number, number]
    width_m: number
  }[]
  spawn_room: number
  reachable_rooms: number[]
  connected: boolean
  navigation_available: boolean
}

export interface SceneMetadata {
  job_id: string
  format: string
  vertices: number
  faces: number
  rooms: number
  floor_height: number
  doorways_included: number
  method?: {
    layout?: string
    appearance?: string
    photogrammetry_used_for_geometry?: boolean
    textures?: { applied?: boolean; count?: number }
    image_reconstruction?: Record<string, unknown>
  }
  scale?: { pixels_per_meter?: number; source?: string; verified?: boolean }
  footprint_m?: { width?: number; height?: number }
}

export interface RoomAssignment {
  room_id: number
  name: string
  room_type?: string | null
  photos: string[]
  [key: string]: unknown
}

export interface SceneBundle {
  job_id: string
  model_url: string
  collision: CollisionData
  room_graph: RoomGraph
  metadata: SceneMetadata
  room_assignments?: RoomAssignment[]
  photo_manifest?: PhotoReview | Record<string, unknown> | null
  manifest: {
    generated_at?: string
    method?: Record<string, unknown>
    photogrammetry?: {
      status?: string
      registered_images?: number
      input_images?: number
      sparse_points?: number
    } | null
    textures?: { count?: number; applied?: boolean } | null
    warnings?: string[]
    limitations?: string[]
    validation?: Record<string, unknown>
  }
}

export interface EngineHealth {
  status: string
  dependencies?: Record<string, unknown>
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json()
    if (typeof body?.detail === 'string') return body.detail
    if (Array.isArray(body?.detail)) {
      return body.detail
        .map((item: { msg?: string }) => item?.msg)
        .filter(Boolean)
        .join('; ')
    }
  } catch {
    // Fall through to the HTTP status text.
  }
  return `${response.status} ${response.statusText || 'request failed'}`
}

export function reconstructionPath(jobId: string, suffix = ''): string {
  return `${RECONSTRUCTION_URL}/reconstructions/${encodeURIComponent(jobId)}${suffix}`
}

export function assetUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path
  return `${RECONSTRUCTION_URL}${path.startsWith('/') ? path : `/${path}`}`
}

export async function fetchPhotoReview(
  jobId: string,
  signal?: AbortSignal,
): Promise<PhotoReview> {
  const response = await fetch(reconstructionPath(jobId, '/review'), {
    signal,
    cache: 'no-store',
  })
  if (!response.ok) {
    throw new Error(await errorMessage(response))
  }
  return response.json()
}

export async function confirmPhotoAssignments(
  jobId: string,
  assignments: PhotoAssignmentInput[],
  roomTypes: Record<string, string>,
): Promise<ReconstructionJob> {
  const response = await fetch(reconstructionPath(jobId, '/confirm-assignments'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ assignments, room_types: roomTypes }),
  })
  if (!response.ok) {
    throw new Error(await errorMessage(response))
  }
  return response.json()
}

export async function addReviewPhoto(jobId: string, file: File): Promise<PhotoReview> {
  const form = new FormData()
  form.append('photo', file)
  const response = await fetch(reconstructionPath(jobId, '/photos'), {
    method: 'POST',
    body: form,
  })
  if (!response.ok) {
    throw new Error(await errorMessage(response))
  }
  return response.json()
}

export async function replaceReviewPhoto(
  jobId: string,
  photoId: string,
  file: File,
): Promise<PhotoReview> {
  const form = new FormData()
  form.append('photo', file)
  const response = await fetch(
    reconstructionPath(jobId, `/photos/${encodeURIComponent(photoId)}/replace`),
    { method: 'POST', body: form },
  )
  if (!response.ok) {
    throw new Error(await errorMessage(response))
  }
  return response.json()
}

export async function removeReviewPhoto(
  jobId: string,
  photoId: string,
): Promise<PhotoReview> {
  const response = await fetch(
    reconstructionPath(jobId, `/photos/${encodeURIComponent(photoId)}`),
    { method: 'DELETE' },
  )
  if (!response.ok) {
    throw new Error(await errorMessage(response))
  }
  return response.json()
}

export async function fetchEngineHealth(signal?: AbortSignal): Promise<EngineHealth> {
  const response = await fetch(`${RECONSTRUCTION_URL}/health`, { signal, cache: 'no-store' })
  if (!response.ok) {
    throw new Error(await errorMessage(response))
  }
  return response.json()
}

export async function fetchJob(jobId: string, signal?: AbortSignal): Promise<ReconstructionJob> {
  const response = await fetch(reconstructionPath(jobId), { signal, cache: 'no-store' })
  if (!response.ok) {
    throw new Error(await errorMessage(response))
  }
  return response.json()
}

export async function fetchScene(jobId: string, signal?: AbortSignal): Promise<SceneBundle> {
  const response = await fetch(reconstructionPath(jobId, '/scene'), { signal, cache: 'no-store' })
  if (!response.ok) {
    throw new Error(await errorMessage(response))
  }
  return response.json()
}

export async function listJobs(
  signal?: AbortSignal,
  limit = 50,
): Promise<ReconstructionJob[]> {
  const response = await fetch(`${RECONSTRUCTION_URL}/reconstructions?limit=${limit}`, {
    signal,
    cache: 'no-store',
  })
  if (!response.ok) {
    throw new Error(await errorMessage(response))
  }
  return response.json()
}

export async function cancelJob(jobId: string): Promise<ReconstructionJob> {
  const response = await fetch(reconstructionPath(jobId, '/cancel'), { method: 'POST' })
  if (!response.ok) {
    throw new Error(await errorMessage(response))
  }
  return response.json()
}

export async function retryJob(
  jobId: string,
  preprocessing?: { wall_polarity?: string; sealing?: string },
): Promise<ReconstructionJob> {
  const response = await fetch(reconstructionPath(jobId, '/retry'), {
    method: 'POST',
    ...(preprocessing
      ? {
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ preprocessing }),
        }
      : {}),
  })
  if (!response.ok) {
    throw new Error(await errorMessage(response))
  }
  return response.json()
}

export async function submitManualPlan(
  jobId: string,
  outline: ManualOutline,
): Promise<ReconstructionJob> {
  const response = await fetch(reconstructionPath(jobId, '/manual-plan'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(outline),
  })
  if (!response.ok) {
    throw new Error(await errorMessage(response))
  }
  return response.json()
}

export async function fetchJobDiagnostics(
  jobId: string,
  signal?: AbortSignal,
): Promise<JobDiagnostics> {
  const response = await fetch(reconstructionPath(jobId, '/diagnostics'), {
    signal,
    cache: 'no-store',
  })
  if (!response.ok) {
    throw new Error(await errorMessage(response))
  }
  return response.json()
}

export function diagnosticArtifactUrl(jobId: string, name: string): string {
  return reconstructionPath(jobId, `/diagnostics/${encodeURIComponent(name)}`)
}

export function floorPlanPreviewUrl(jobId: string): string {
  return reconstructionPath(jobId, '/floor-plan')
}

export async function downloadSceneAsset(
  modelUrl: string,
  onProgress: (loaded: number, total: number | null) => void,
  signal?: AbortSignal,
): Promise<ArrayBuffer> {
  const url = modelUrl.startsWith('http') ? modelUrl : `${RECONSTRUCTION_URL}${modelUrl}`
  const response = await fetch(url, { signal })
  if (!response.ok) {
    throw new Error(`Scene download failed: ${await errorMessage(response)}`)
  }
  const header = Number(response.headers.get('content-length') || 0)
  const total = Number.isFinite(header) && header > 0 ? header : null
  if (!response.body) {
    const buffer = await response.arrayBuffer()
    onProgress(buffer.byteLength, buffer.byteLength)
    return buffer
  }
  const reader = response.body.getReader()
  const chunks: Uint8Array[] = []
  let loaded = 0
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    if (value) {
      chunks.push(value)
      loaded += value.byteLength
      onProgress(loaded, total)
    }
  }
  const buffer = new Uint8Array(loaded)
  let offset = 0
  for (const chunk of chunks) {
    buffer.set(chunk, offset)
    offset += chunk.byteLength
  }
  return buffer.buffer
}
