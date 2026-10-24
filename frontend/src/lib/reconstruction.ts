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
  review_required?: boolean | null
  room_count?: number | null
  room_types_url?: string | null
  room_types_confirmed?: boolean | null
  room_type_review?: RoomTypeReviewSummary | null
  room_types?: RoomTypeEntry[] | null
  furnishing_style?: string | null
  furnishing_variant?: number | null
  furniture_count?: number | null
  furniture_url?: string | null
}

export interface RoomTypeEntry {
  room_id: number
  room_type: string
  name?: string | null
  confidence?: number | null
  needs_confirmation?: boolean | null
  source?: string | null
  alternatives?: { room_type: string; score: number }[]
  reasons?: string[]
  area_m2?: number | null
  bounds_m?: [number, number, number, number] | null
  bounds_px?: [number, number, number, number] | null
  center_px?: [number, number] | null
  polygon_px?: [number, number][] | null
  polygon_m?: [number, number][] | null
  polygon?: [number, number][] | null
}

export interface RoomTypeReviewSummary {
  status?: string | null
  source?: string | null
  requires_confirmation?: boolean | null
  rooms?: number | null
  uncertain_rooms?: number[] | null
  confirmed_at?: string | null
}

export interface RoomTypeOption {
  value: string
  label: string
}

export interface RoomTypeSelection {
  room_id: number
  room_type: string
  name?: string | null
}

export interface RoomTypeReview {
  job_id: string
  status: string
  job_status: string
  confirmed_at?: string | null
  requires_confirmation: boolean
  source?: string | null
  notes?: string | null
  footprint_m?: Record<string, number> | null
  footprint_px?: { x: number; y: number; width: number; height: number } | null
  image_dimensions?: [number, number] | null
  scale_pixels_per_meter?: number | null
  rooms: RoomTypeEntry[]
  assignments: RoomTypeSelection[]
  labels: RoomTypeOption[]
  style?: string | null
  variant?: number | null
  floor_plan_url: string
  normalized_floor_plan_url?: string | null
}

export interface FurnitureItem {
  id: string
  room_id: number
  room_type?: string | null
  room_name?: string | null
  asset_id: string
  model_id?: string | null
  label: string
  position: [number, number, number]
  rotation_deg: number
  rotation?: number
  scale: [number, number, number]
  size_m?: [number, number, number]
  height_m?: number | null
  footprint?: { min: [number, number]; max: [number, number] } | null
  bbox?: { min: [number, number, number]; max: [number, number, number] } | null
  collision: boolean
  collision_bounds?: { min: [number, number]; max: [number, number] } | null
  materials?: string[]
  material_style?: string
  placement?: string
  wall?: string | null
  parent_item_id?: string | null
  status?: string
}

export interface FurnishingSummary {
  items?: number
  collision_items?: number
  rooms_total?: number
  rooms_furnished?: number
  rooms_unspecified?: number
  skipped_required?: number
  style?: string
  variant?: number
}

export interface Furnishing {
  style?: string
  style_label?: string
  variant?: number
  summary?: FurnishingSummary
  materials?: Record<string, string>
  limits?: Record<string, unknown>
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
  room_type_review?: RoomTypeReviewSummary | null
  room_types_confirmed?: boolean | null
  furnishing_style?: string | null
  furniture_count?: number | null
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

export interface CollisionFurniture {
  item_id: string
  room_id: number
  asset_id: string
  label: string
  min: [number, number]
  max: [number, number]
  height_m?: number | null
}

export interface CollisionData {
  units: string
  walls: { min: [number, number]; max: [number, number]; exterior?: boolean }[]
  doors: Doorway[]
  rooms: CollisionRoom[]
  furniture?: CollisionFurniture[]
  furniture_style?: string
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
  furniture?: {
    id: string
    asset_id: string
    label: string
    position: [number, number, number]
    rotation_deg: number
    collision: boolean
  }[]
  furniture_count?: number
  furnishing_status?: string | null
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
    furniture?: string
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
  area_m2?: number | null
  furniture_ids?: string[]
  furniture_count?: number
  furnishing_status?: string | null
  [key: string]: unknown
}

export interface SceneBundle {
  job_id: string
  workflow?: string
  model_url: string
  collision: CollisionData
  room_graph: RoomGraph
  metadata: SceneMetadata
  room_types?: RoomTypeEntry[]
  room_assignments?: RoomAssignment[]
  furniture?: FurnitureItem[]
  furnishing?: Furnishing
  manifest: {
    schema_version?: string
    workflow?: string
    generated_at?: string
    method?: Record<string, unknown>
    textures?: { count?: number; applied?: boolean } | null
    warnings?: string[]
    limitations?: string[]
    validation?: Record<string, unknown>
  }
}

export interface EngineHealth {
  status: string
  dependencies?: {
    pdf_floor_plans?: boolean
    furnishing?: {
      asset_library_size?: number
      styles?: string[]
      requires_external_vision_api?: boolean
    }
    [key: string]: unknown
  }
}

export interface FurnishingUpdate {
  style?: string
  variant?: number
  room_types?: RoomTypeSelection[]
  regenerate?: boolean
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

export async function fetchRoomTypes(
  jobId: string,
  signal?: AbortSignal,
): Promise<RoomTypeReview> {
  const response = await fetch(reconstructionPath(jobId, '/room-types'), {
    signal,
    cache: 'no-store',
  })
  if (!response.ok) {
    throw new Error(await errorMessage(response))
  }
  return response.json()
}

export async function confirmRoomTypes(
  jobId: string,
  rooms: RoomTypeSelection[],
  style?: string,
  variant?: number,
): Promise<ReconstructionJob> {
  const response = await fetch(reconstructionPath(jobId, '/room-types'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      rooms,
      ...(style ? { style } : {}),
      ...(variant !== undefined ? { variant } : {}),
    }),
  })
  if (!response.ok) {
    throw new Error(await errorMessage(response))
  }
  return response.json()
}

export async function updateFurnishings(
  jobId: string,
  update: FurnishingUpdate,
): Promise<ReconstructionJob> {
  const response = await fetch(reconstructionPath(jobId, '/furnishings'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(update),
  })
  if (!response.ok) {
    throw new Error(await errorMessage(response))
  }
  return response.json()
}

export async function updateRoomType(
  jobId: string,
  roomId: number,
  roomType: string,
  name?: string,
): Promise<ReconstructionJob> {
  const response = await fetch(
    reconstructionPath(jobId, `/rooms/${encodeURIComponent(String(roomId))}`),
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ room_type: roomType, ...(name !== undefined ? { name } : {}) }),
    },
  )
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
