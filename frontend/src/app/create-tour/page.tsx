'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import Navigation from '@/components/Navigation'
import RoomTypeReview from '@/components/RoomTypeReview'
import {
  RECONSTRUCTION_URL,
  fetchEngineHealth,
  fetchJob,
  fetchJobDiagnostics,
  floorPlanPreviewUrl,
  retryJob,
  submitManualPlan,
  type JobDiagnostics,
  type ManualDoorway,
  type ReconstructionJob,
} from '@/lib/reconstruction'

const MAX_FLOOR_PLAN_BYTES = 15 * 1024 * 1024
const TERMINAL = new Set(['completed', 'failed', 'cancelled'])

const STYLES = [
  { value: 'modern', label: 'Modern' },
  { value: 'minimalist', label: 'Minimalist' },
  { value: 'scandinavian', label: 'Scandinavian' },
]

const STAGES = [
  {
    key: 'uploading',
    label: 'Upload floor plan',
    detail: 'Original plan preserved unchanged for this job',
  },
  {
    key: 'parsing_floor_plan',
    label: 'Parse floor plan',
    detail: 'Validate the image, normalise, threshold wall candidates',
  },
  {
    key: 'detecting_rooms_openings',
    label: 'Detect rooms and openings',
    detail: 'Wall bands, enclosed regions, doorways and windows',
  },
  {
    key: 'validating_room_layout',
    label: 'Validate room layout',
    detail: 'Room connectivity, scale and footprint',
  },
  {
    key: 'generating_geometry',
    label: 'Generate 3D geometry',
    detail: 'Floors, walls, ceilings, openings and room graph',
  },
  {
    key: 'placing_furniture',
    label: 'Place furniture',
    detail: 'Wall anchoring, clearances and 3D asset bounds per room type',
  },
  {
    key: 'generating_lighting_materials',
    label: 'Generate lighting and materials',
    detail: 'Room lighting, PBR materials and shadow setup',
  },
  {
    key: 'validating_navigation_collisions',
    label: 'Validate navigation and collisions',
    detail: 'Spawn, doorway access, furniture bounds and room reachability',
  },
  {
    key: 'loading_walkthrough',
    label: 'Load walkthrough',
    detail: 'Export GLB, collision, room graph and furniture manifest',
  },
  {
    key: 'completed',
    label: 'Completed',
    detail: 'Validated scene, furniture and walkthrough ready',
  },
]

const STAGE_INDEX: Record<string, number> = {
  queued: 0,
  uploading: 0,
  parsing_floor_plan: 1,
  preprocessing: 1,
  detecting_rooms_openings: 2,
  detecting_rooms: 2,
  validating_room_layout: 3,
  validating_layout: 3,
  awaiting_room_types: 4,
  generating_geometry: 4,
  building_geometry: 4,
  texturing: 4,
  exporting: 8,
  placing_furniture: 5,
  generating_lighting_materials: 6,
  validating_navigation_collisions: 7,
  loading_walkthrough: 8,
  validating_assets: 8,
  completed: 9,
}

function stageIndex(key?: string | null): number {
  return key ? STAGE_INDEX[key] ?? -1 : -1
}

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json()
    if (typeof body?.detail === 'string') return body.detail
    if (Array.isArray(body?.detail)) {
      return body.detail.map((item: { msg?: string }) => item?.msg).filter(Boolean).join('; ')
    }
  } catch {
    // use status text
  }
  return `${response.status} ${response.statusText || 'request failed'}`
}

export default function CreateTourPage() {
  const [projectName, setProjectName] = useState('')
  const [floorPlan, setFloorPlan] = useState<File | null>(null)
  const [numFloors, setNumFloors] = useState(1)
  const [knownWidth, setKnownWidth] = useState('')
  const [knownLength, setKnownLength] = useState('')
  const [style, setStyle] = useState('modern')

  const [submitting, setSubmitting] = useState(false)
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const [job, setJob] = useState<ReconstructionJob | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pollKey, setPollKey] = useState(0)
  const [engine, setEngine] = useState<{
    online: boolean
    pdf: boolean
    styles?: string[]
    assets?: number
    detail?: string
  } | null>(null)
  const [diagnostics, setDiagnostics] = useState<JobDiagnostics | null>(null)
  const [diagnosticsError, setDiagnosticsError] = useState<string | null>(null)
  const [retrying, setRetrying] = useState(false)
  const [showCorrection, setShowCorrection] = useState(false)
  const [manualRooms, setManualRooms] = useState<[number, number][][]>([])
  const [manualDoorways, setManualDoorways] = useState<ManualDoorway[]>([])
  const [manualTool, setManualTool] = useState<'room' | 'doorway'>('room')
  const [doorwayOrientation, setDoorwayOrientation] = useState<'horizontal' | 'vertical'>(
    'vertical',
  )
  const [dragStart, setDragStart] = useState<[number, number] | null>(null)
  const [dragCurrent, setDragCurrent] = useState<[number, number] | null>(null)
  const [planSize, setPlanSize] = useState<{ width: number; height: number } | null>(null)
  const [manualSubmitting, setManualSubmitting] = useState(false)
  const planCanvasRef = useRef<SVGSVGElement>(null)

  useEffect(() => {
    const controller = new AbortController()
    fetchEngineHealth(controller.signal)
      .then((health) => {
        const furnishing = health.dependencies?.furnishing
        setEngine({
          online: health.status === 'healthy',
          pdf: Boolean(health.dependencies?.pdf_floor_plans),
          styles: furnishing?.styles,
          assets: furnishing?.asset_library_size,
        })
      })
      .catch((caught) => {
        if (controller.signal.aborted) return
        setEngine({
          online: false,
          pdf: false,
          detail: caught instanceof Error ? caught.message : String(caught),
        })
      })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const requested = new URLSearchParams(window.location.search).get('job')
    if (!requested) return
    queueMicrotask(() => {
      setActiveJobId(requested)
      setPollKey((value) => value + 1)
    })
  }, [])

  useEffect(() => {
    if (!activeJobId || typeof window === 'undefined') return
    const url = new URL(window.location.href)
    if (url.searchParams.get('job') === activeJobId) return
    url.searchParams.set('job', activeJobId)
    window.history.replaceState({}, '', url)
  }, [activeJobId])

  useEffect(() => {
    if (!activeJobId) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | null = null
    let stopped = false

    const tick = async () => {
      try {
        const data = await fetchJob(activeJobId, controller.signal)
        if (stopped) return
        setJob(data)
        setError(null)
        if (!TERMINAL.has(data.status)) {
          timer = setTimeout(tick, 1500)
        }
      } catch (caught) {
        if (controller.signal.aborted || stopped) return
        setError(caught instanceof Error ? caught.message : String(caught))
      }
    }

    void tick()
    return () => {
      stopped = true
      controller.abort()
      if (timer) clearTimeout(timer)
    }
  }, [activeJobId, pollKey])

  const partialNavigation = job?.status === 'completed' && job.navigation_available === false
  const diagnosticsJobId =
    job?.status === 'failed' || partialNavigation ? job?.job_id ?? null : null
  const jobRetryCount = job?.retry_count ?? 0

  useEffect(() => {
    if (!diagnosticsJobId) return
    const controller = new AbortController()
    fetchJobDiagnostics(diagnosticsJobId, controller.signal)
      .then((data) => {
        if (controller.signal.aborted) return
        setDiagnostics(data)
        setDiagnosticsError(null)
        const previous = data.manual_outline
        if (previous) {
          setManualRooms(previous.rooms.map((room) => room.polygon_px))
          setManualDoorways(previous.doorways)
        }
      })
      .catch((caught) => {
        if (controller.signal.aborted) return
        setDiagnosticsError(caught instanceof Error ? caught.message : String(caught))
      })
    return () => controller.abort()
  }, [diagnosticsJobId, jobRetryCount])

  const handleFloorPlanUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    setError(null)
    const file = event.target.files?.[0]
    if (!file) return
    const validTypes = ['image/png', 'image/jpeg', 'image/jpg', 'application/pdf']
    if (!validTypes.includes(file.type)) {
      setError('Floor plan must be a PNG, JPG, JPEG or PDF file.')
      event.target.value = ''
      return
    }
    if (file.size > MAX_FLOOR_PLAN_BYTES) {
      setError(`Floor plan is too large (${formatBytes(file.size)}). Maximum 15 MB.`)
      event.target.value = ''
      return
    }
    setFloorPlan(file)
  }

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    setError(null)
    if (!projectName.trim()) {
      setError('Enter a project name.')
      return
    }
    if (!floorPlan) {
      setError('Upload a floor plan image.')
      return
    }

    setSubmitting(true)
    setJob(null)
    setActiveJobId(null)
    setDiagnostics(null)
    setDiagnosticsError(null)
    setShowCorrection(false)
    setManualRooms([])
    setManualDoorways([])
    try {
      const formData = new FormData()
      formData.append('project_name', projectName.trim())
      formData.append('floor_plan', floorPlan)
      formData.append('num_floors', String(numFloors))
      formData.append('style', style)
      if (knownWidth) formData.append('known_width', knownWidth)
      if (knownLength) formData.append('known_length', knownLength)

      const response = await fetch(`${RECONSTRUCTION_URL}/reconstructions`, {
        method: 'POST',
        body: formData,
      })
      if (!response.ok) {
        throw new Error(await errorMessage(response))
      }
      const created: ReconstructionJob = await response.json()
      setJob(created)
      setActiveJobId(created.job_id)
      setPollKey((value) => value + 1)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setSubmitting(false)
    }
  }

  const handleRetry = useCallback(
    async (preprocessing?: { wall_polarity?: string; sealing?: string }) => {
      if (!activeJobId) return
      setError(null)
      setRetrying(true)
      try {
        const next = await retryJob(activeJobId, preprocessing)
        setJob(next)
        setDiagnostics(null)
        setShowCorrection(false)
        setPollKey((value) => value + 1)
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught))
      } finally {
        setRetrying(false)
      }
    },
    [activeJobId],
  )

  const imagePoint = (event: React.PointerEvent<SVGSVGElement>): [number, number] | null => {
    const svg = planCanvasRef.current
    if (!svg || !planSize) return null
    const bounds = svg.getBoundingClientRect()
    if (bounds.width <= 0 || bounds.height <= 0) return null
    const x = Math.round(((event.clientX - bounds.left) / bounds.width) * planSize.width)
    const y = Math.round(((event.clientY - bounds.top) / bounds.height) * planSize.height)
    return [
      Math.max(0, Math.min(planSize.width, x)),
      Math.max(0, Math.min(planSize.height, y)),
    ]
  }

  const handlePlanPointerDown = (event: React.PointerEvent<SVGSVGElement>) => {
    const point = imagePoint(event)
    if (!point) return
    if (manualTool === 'doorway') {
      setManualDoorways((previous) => [
        ...previous,
        { orientation: doorwayOrientation, center_px: point, width_px: null },
      ])
      return
    }
    event.currentTarget.setPointerCapture(event.pointerId)
    setDragStart(point)
    setDragCurrent(point)
  }

  const handlePlanPointerMove = (event: React.PointerEvent<SVGSVGElement>) => {
    if (!dragStart) return
    const point = imagePoint(event)
    if (point) setDragCurrent(point)
  }

  const handlePlanPointerUp = (event: React.PointerEvent<SVGSVGElement>) => {
    if (!dragStart) return
    const end = imagePoint(event) ?? dragCurrent
    const start = dragStart
    setDragStart(null)
    setDragCurrent(null)
    if (!end) return
    const left = Math.min(start[0], end[0])
    const right = Math.max(start[0], end[0])
    const top = Math.min(start[1], end[1])
    const bottom = Math.max(start[1], end[1])
    if (right - left < 20 || bottom - top < 20) {
      setError('Room rectangles must be at least 20 × 20 pixels on the plan.')
      return
    }
    setManualRooms((previous) => [
      ...previous,
      [
        [left, top],
        [right, top],
        [right, bottom],
        [left, bottom],
      ],
    ])
    setError(null)
  }

  const handleManualSubmit = async () => {
    if (!activeJobId) return
    if (manualRooms.length === 0) {
      setError('Draw at least one room rectangle on the plan before submitting a correction.')
      return
    }
    setError(null)
    setManualSubmitting(true)
    try {
      const next = await submitManualPlan(activeJobId, {
        rooms: manualRooms.map((polygon) => ({ polygon_px: polygon })),
        doorways: manualDoorways,
      })
      setJob(next)
      setDiagnostics(null)
      setDiagnosticsError(null)
      setShowCorrection(false)
      setPollKey((value) => value + 1)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setManualSubmitting(false)
    }
  }

  const handleJobUpdated = useCallback((next: ReconstructionJob) => {
    setJob(next)
    setError(null)
    setPollKey((value) => value + 1)
  }, [])

  const stageKey =
    job?.status === 'failed' ? job.failed_stage ?? null : job?.current_stage ?? null
  const currentStageIndex = job ? stageIndex(stageKey) : -1
  const failedStageIndex =
    job?.status === 'failed' && job.failed_stage ? stageIndex(job.failed_stage) : -1
  const completed = job?.status === 'completed'
  const failed = job?.status === 'failed' || job?.status === 'cancelled'
  const awaitingRoomTypes = job?.status === 'awaiting_room_types'
  const busy = submitting || (Boolean(activeJobId) && !completed && !failed)
  const failedStageLabel =
    failedStageIndex >= 0 ? STAGES[failedStageIndex].label : job?.failed_stage || null
  const diagnosticImages = (diagnostics?.artifacts ?? []).filter(
    (artifact) =>
      artifact.type === 'png' &&
      ['rooms_detected.png', 'barrier_overlay.png', 'walls_mask.png'].includes(artifact.name),
  )

  return (
    <div className="min-h-screen bg-gray-900">
      <Navigation />

      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold text-white">Create Property Tour</h1>
            <p className="mt-1 max-w-2xl text-gray-400">
              Upload a 2D floor plan. The engine parses walls, rooms and openings, asks you to
              confirm the inferred room types, then builds a validated walkable scene and furnishes
              every room from a built-in 3D asset library. No photographs are required.
            </p>
          </div>
          <div className="text-xs">
            {engine === null ? (
              <span className="text-gray-500">Checking reconstruction engine...</span>
            ) : engine.online ? (
              <span className="rounded-full border border-emerald-500/40 bg-emerald-950/30 px-3 py-1 text-emerald-300">
                Engine online · PDF plans {engine.pdf ? 'supported' : 'unavailable (use PNG/JPG)'}
                {engine.assets ? ` · ${engine.assets} 3D assets` : ''}
              </span>
            ) : (
              <span className="rounded-full border border-rose-500/40 bg-rose-950/30 px-3 py-1 text-rose-300">
                Engine offline at {RECONSTRUCTION_URL}
              </span>
            )}
          </div>
        </div>

        <div className="grid grid-cols-1 gap-8 lg:grid-cols-2">
          <div className="rounded-lg bg-gray-800 p-6">
            <h2 className="mb-6 text-xl font-semibold text-white">Property information</h2>
            <form onSubmit={handleSubmit} className="space-y-6">
              <div>
                <label htmlFor="project-name" className="mb-2 block text-sm font-medium text-gray-300">
                  Project name *
                </label>
                <input
                  id="project-name"
                  type="text"
                  value={projectName}
                  onChange={(event) => setProjectName(event.target.value)}
                  className="w-full rounded-lg border border-gray-600 bg-gray-700 px-4 py-2 text-white focus:border-transparent focus:ring-2 focus:ring-blue-500"
                  placeholder="e.g. Oak Street apartment"
                  required
                />
              </div>

              <div>
                <label htmlFor="floor-plan" className="mb-2 block text-sm font-medium text-gray-300">
                  Floor plan * (PNG, JPG, JPEG{engine?.pdf ? ', PDF' : ''}, max 15 MB)
                </label>
                <input
                  id="floor-plan"
                  type="file"
                  accept={engine?.pdf ? '.png,.jpg,.jpeg,.pdf' : '.png,.jpg,.jpeg'}
                  onChange={handleFloorPlanUpload}
                  className="w-full rounded-lg border border-gray-600 bg-gray-700 px-4 py-2 text-white focus:border-transparent focus:ring-2 focus:ring-blue-500"
                  required
                />
                {floorPlan && (
                  <p className="mt-2 text-sm text-green-400">
                    {floorPlan.name} ({formatBytes(floorPlan.size)})
                  </p>
                )}
                {engine && !engine.pdf && (
                  <p className="mt-1 text-xs text-gray-500">
                    PDF plans need poppler (`pdftoppm`) installed in the engine environment.
                  </p>
                )}
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                <div>
                  <label htmlFor="floors" className="mb-2 block text-sm font-medium text-gray-300">
                    Floors
                  </label>
                  <input
                    id="floors"
                    type="number"
                    min={1}
                    max={5}
                    value={numFloors}
                    onChange={(event) => setNumFloors(parseInt(event.target.value) || 1)}
                    className="w-full rounded-lg border border-gray-600 bg-gray-700 px-4 py-2 text-white focus:border-transparent focus:ring-2 focus:ring-blue-500"
                  />
                </div>
                <div>
                  <label htmlFor="known-width" className="mb-2 block text-sm font-medium text-gray-300">
                    Width (m)
                  </label>
                  <input
                    id="known-width"
                    type="number"
                    step="0.1"
                    min="2"
                    max="100"
                    value={knownWidth}
                    onChange={(event) => setKnownWidth(event.target.value)}
                    className="w-full rounded-lg border border-gray-600 bg-gray-700 px-4 py-2 text-white focus:border-transparent focus:ring-2 focus:ring-blue-500"
                    placeholder="optional"
                  />
                </div>
                <div>
                  <label htmlFor="known-length" className="mb-2 block text-sm font-medium text-gray-300">
                    Length (m)
                  </label>
                  <input
                    id="known-length"
                    type="number"
                    step="0.1"
                    min="2"
                    max="100"
                    value={knownLength}
                    onChange={(event) => setKnownLength(event.target.value)}
                    className="w-full rounded-lg border border-gray-600 bg-gray-700 px-4 py-2 text-white focus:border-transparent focus:ring-2 focus:ring-blue-500"
                    placeholder="optional"
                  />
                </div>
              </div>
              <p className="-mt-3 text-xs text-gray-500">
                Only the ground floor is currently reconstructed. Supplying both dimensions makes
                the scale measured rather than estimated.
              </p>

              <div>
                <label htmlFor="furnishing-style" className="mb-2 block text-sm font-medium text-gray-300">
                  Furniture style
                </label>
                <select
                  id="furnishing-style"
                  value={style}
                  onChange={(event) => setStyle(event.target.value)}
                  className="w-full rounded-lg border border-gray-600 bg-gray-700 px-4 py-2 text-white focus:border-transparent focus:ring-2 focus:ring-blue-500"
                >
                  {STYLES.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <p className="mt-2 text-sm text-gray-400">
                  Furniture is placed deterministically with real 3D meshes and PBR materials.
                  Photographs are not required and no external image-generation API is called.
                  Room types are proposed from the plan and confirmed by you before furnishing.
                </p>
              </div>

              {error && (
                <div className="rounded-lg border border-rose-500/40 bg-rose-950/40 p-3 text-sm text-rose-200">
                  {error}
                </div>
              )}

              <button
                type="submit"
                disabled={busy}
                className="w-full rounded-lg bg-blue-600 py-3 font-medium text-white transition-colors hover:bg-blue-500 disabled:cursor-not-allowed disabled:bg-gray-600"
              >
                {submitting ? 'Uploading...' : busy ? 'Reconstruction running...' : 'Generate furnished tour'}
              </button>
            </form>
          </div>

          <div className="rounded-lg bg-gray-800 p-6">
            <h2 className="mb-4 text-xl font-semibold text-white">Reconstruction status</h2>

            {!job && (
              <p className="text-sm text-gray-400">
                Job progress, real stage names and validation results will appear here after upload.
              </p>
            )}

            {job && (
              <div className="space-y-4" aria-live="polite">
                <div className="rounded-lg bg-gray-700 p-4">
                  <div className="flex items-center justify-between gap-3">
                    <p className="font-medium capitalize text-white">
                      {failed && failedStageLabel
                        ? `Failed during ${failedStageLabel}`
                        : job.stage_label || job.current_stage.replace(/_/g, ' ')}
                    </p>
                    <span className="text-sm text-gray-300">{Math.round(job.progress)}%</span>
                  </div>
                  <div className="mt-3 h-2 overflow-hidden rounded-full bg-gray-600">
                    <div
                      className={`h-full rounded-full transition-[width] duration-500 ${
                        failed ? 'bg-rose-500' : completed ? 'bg-emerald-500' : 'bg-blue-500'
                      }`}
                      style={{ width: `${Math.max(3, Math.round(job.progress))}%` }}
                    />
                  </div>
                  <p className="mt-2 break-all text-xs text-gray-400">Job {job.job_id}</p>
                  {job.error && (
                    <p className="mt-3 break-words text-sm text-rose-300">{job.error}</p>
                  )}
                  {job.error_code && (
                    <p className="mt-1 break-all font-mono text-[11px] text-rose-300/70">
                      {job.error_code}
                    </p>
                  )}
                  {awaitingRoomTypes && (
                    <div className="mt-3 rounded border border-blue-500/40 bg-blue-950/30 p-3 text-xs text-blue-100">
                      <p className="font-medium">Your review is required</p>
                      <p className="mt-1 text-blue-100/80">
                        The plan has been parsed and rooms have inferred types. Confirm or correct
                        every room before furniture is placed and the walkthrough is generated.
                      </p>
                      <a
                        href="#room-type-review"
                        className="mt-2 inline-block rounded bg-blue-600 px-3 py-1.5 font-medium text-white hover:bg-blue-500"
                      >
                        Review room types
                      </a>
                    </div>
                  )}
                </div>

                <ol className="space-y-3">
                  {STAGES.map((stage, index) => {
                    const reached =
                      completed || (currentStageIndex >= 0 && index < currentStageIndex)
                    const active = !failed && !awaitingRoomTypes && currentStageIndex === index
                    const stageFailed = failedStageIndex === index
                    return (
                      <li key={stage.key} className="flex items-start">
                        <span
                          className={`mr-3 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full text-xs font-bold ${
                            stageFailed
                              ? 'bg-rose-600 text-white'
                              : active
                                ? 'bg-blue-600 text-white'
                                : reached
                                  ? 'bg-emerald-700 text-white'
                                  : 'bg-gray-700 text-gray-400'
                          }`}
                        >
                          {stageFailed ? '!' : index + 1}
                        </span>
                        <div>
                          <p
                            className={`font-medium ${
                              stageFailed
                                ? 'text-rose-300'
                                : active
                                  ? 'text-white'
                                  : 'text-gray-300'
                            }`}
                          >
                            {stage.label}
                          </p>
                          <p className="text-sm text-gray-500">{stage.detail}</p>
                        </div>
                      </li>
                    )
                  })}
                </ol>

                {(failed || partialNavigation) && (
                  <div className="rounded-lg border border-rose-500/40 bg-rose-950/20 p-4">
                    <p className="text-sm font-semibold text-rose-200">
                      {failed
                        ? failedStageLabel
                          ? `Detection stopped at ${failedStageLabel}`
                          : 'Reconstruction did not produce a scene'
                        : 'Completed with unreachable rooms'}
                    </p>
                    <p className="mt-1 break-words text-sm text-rose-100/90">
                      {failed
                        ? job.error ||
                          'The engine rejected the inputs instead of substituting a sample.'
                        : 'The scene passed geometry and asset validation, but at least one room has no detected doorway. The walkthrough is labelled partial navigation.'}
                    </p>
                    <p className="mt-2 text-xs text-rose-200/70">
                      {failed
                        ? 'Your original upload is preserved. Inspect what was detected, re-run with different preprocessing, or correct the room outlines below.'
                        : 'Inspect what was detected, re-run automatic detection with different preprocessing, or correct the room outlines below. A re-run replaces this job’s scene.'}
                    </p>

                    <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <figure>
                        <figcaption className="mb-1 text-[11px] uppercase tracking-wide text-gray-400">
                          Original upload
                        </figcaption>
                        <a
                          href={floorPlanPreviewUrl(job.job_id)}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img
                            src={floorPlanPreviewUrl(job.job_id)}
                            alt="Original floor plan uploaded for this job"
                            className="max-h-56 w-full rounded border border-gray-600 bg-gray-950 object-contain"
                          />
                        </a>
                      </figure>
                      {diagnosticImages.map((artifact) => (
                        <figure key={artifact.name}>
                          <figcaption className="mb-1 text-[11px] uppercase tracking-wide text-gray-400">
                            {artifact.name.replace(/_/g, ' ').replace(/\.png$/i, '')}
                          </figcaption>
                          <a
                            href={`${RECONSTRUCTION_URL}${artifact.url}`}
                            target="_blank"
                            rel="noreferrer"
                          >
                            {/* eslint-disable-next-line @next/next/no-img-element */}
                            <img
                              src={`${RECONSTRUCTION_URL}${artifact.url}`}
                              alt={`Detection artifact ${artifact.name}`}
                              className="max-h-56 w-full rounded border border-gray-600 bg-gray-950 object-contain"
                            />
                          </a>
                        </figure>
                      ))}
                    </div>
                    {diagnosticsError && (
                      <p className="mt-2 text-xs text-amber-300">
                        Diagnostics could not be loaded: {diagnosticsError}
                      </p>
                    )}
                    {(diagnostics?.artifacts.length ?? 0) > 0 && (
                      <details className="mt-3 text-xs text-gray-300">
                        <summary className="cursor-pointer text-gray-400">
                          All diagnostic files (saved for this job)
                        </summary>
                        <ul className="mt-1 space-y-1">
                          {diagnostics?.artifacts.map((artifact) => (
                            <li key={artifact.name}>
                              <a
                                href={`${RECONSTRUCTION_URL}${artifact.url}`}
                                target="_blank"
                                rel="noreferrer"
                                className="text-blue-300 hover:text-blue-200"
                              >
                                {artifact.name}
                              </a>{' '}
                              <span className="text-gray-500">
                                {formatBytes(artifact.size_bytes)}
                              </span>
                            </li>
                          ))}
                        </ul>
                      </details>
                    )}

                    {(diagnostics?.preprocessing.options.length ?? 0) > 0 && (
                      <div className="mt-4">
                        <p className="text-xs font-semibold uppercase tracking-wide text-gray-300">
                          Retry with different preprocessing
                        </p>
                        <div className="mt-2 flex flex-wrap gap-2">
                          {diagnostics?.preprocessing.options.map((option) => (
                            <button
                              key={`${option.wall_polarity}-${option.sealing}-${option.label}`}
                              type="button"
                              disabled={retrying}
                              onClick={() =>
                                handleRetry({
                                  wall_polarity: option.wall_polarity,
                                  sealing: option.sealing,
                                })
                              }
                              className="rounded border border-gray-600 bg-gray-800 px-3 py-2 text-left text-xs text-gray-200 hover:bg-gray-700 disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              <span className="block font-medium">{option.label}</span>
                              <span className="mt-0.5 block font-mono text-[10px] text-gray-400">
                                {option.wall_polarity} · {option.sealing}
                              </span>
                            </button>
                          ))}
                        </div>
                      </div>
                    )}

                    <button
                      type="button"
                      onClick={() => setShowCorrection((value) => !value)}
                      className="mt-4 rounded-lg border border-gray-600 px-4 py-2 text-sm font-medium text-gray-100 hover:bg-gray-700"
                    >
                      {showCorrection ? 'Hide manual correction' : 'Correct room outlines manually'}
                    </button>

                    {showCorrection && (
                      <div className="mt-3 rounded-lg border border-gray-600 bg-gray-900/70 p-3">
                        <div className="flex flex-wrap items-center gap-2 text-xs">
                          <button
                            type="button"
                            onClick={() => setManualTool('room')}
                            className={`rounded px-3 py-1.5 font-medium ${
                              manualTool === 'room'
                                ? 'bg-blue-600 text-white'
                                : 'bg-gray-800 text-gray-300 hover:bg-gray-700'
                            }`}
                          >
                            Draw room rectangle
                          </button>
                          <button
                            type="button"
                            onClick={() => setManualTool('doorway')}
                            className={`rounded px-3 py-1.5 font-medium ${
                              manualTool === 'doorway'
                                ? 'bg-amber-600 text-white'
                                : 'bg-gray-800 text-gray-300 hover:bg-gray-700'
                            }`}
                          >
                            Place doorway
                          </button>
                          {manualTool === 'doorway' && (
                            <select
                              value={doorwayOrientation}
                              onChange={(event) =>
                                setDoorwayOrientation(
                                  event.target.value === 'horizontal'
                                    ? 'horizontal'
                                    : 'vertical',
                                )
                              }
                              className="rounded border border-gray-600 bg-gray-800 px-2 py-1.5 text-gray-200"
                            >
                              <option value="vertical">On a vertical wall</option>
                              <option value="horizontal">On a horizontal wall</option>
                            </select>
                          )}
                        </div>

                        <div className="relative mt-3 select-none overflow-hidden rounded border border-gray-600 bg-gray-950">
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img
                            src={floorPlanPreviewUrl(job.job_id)}
                            alt="Floor plan for manual room-outline correction"
                            draggable={false}
                            className="block h-auto w-full"
                            onLoad={(event) =>
                              setPlanSize({
                                width: event.currentTarget.naturalWidth,
                                height: event.currentTarget.naturalHeight,
                              })
                            }
                          />
                          {planSize && (
                            <svg
                              ref={planCanvasRef}
                              viewBox={`0 0 ${planSize.width} ${planSize.height}`}
                              preserveAspectRatio="none"
                              className={`absolute inset-0 h-full w-full touch-none ${
                                manualTool === 'room' ? 'cursor-crosshair' : 'cursor-cell'
                              }`}
                              onPointerDown={handlePlanPointerDown}
                              onPointerMove={handlePlanPointerMove}
                              onPointerUp={handlePlanPointerUp}
                              onPointerCancel={() => {
                                setDragStart(null)
                                setDragCurrent(null)
                              }}
                            >
                              {manualRooms.map((polygon, index) => (
                                <polygon
                                  key={`manual-room-${index}`}
                                  points={polygon.map(([x, y]) => `${x},${y}`).join(' ')}
                                  fill="rgba(59,130,246,0.28)"
                                  stroke="#60a5fa"
                                  strokeWidth={Math.max(2, planSize.width / 300)}
                                />
                              ))}
                              {dragStart && dragCurrent && (
                                <rect
                                  x={Math.min(dragStart[0], dragCurrent[0])}
                                  y={Math.min(dragStart[1], dragCurrent[1])}
                                  width={Math.abs(dragStart[0] - dragCurrent[0])}
                                  height={Math.abs(dragStart[1] - dragCurrent[1])}
                                  fill="rgba(59,130,246,0.2)"
                                  stroke="#93c5fd"
                                  strokeDasharray="6 4"
                                  strokeWidth={Math.max(2, planSize.width / 300)}
                                />
                              )}
                              {manualDoorways.map((doorway, index) => {
                                const half = Math.max(12, planSize.width * 0.045)
                                const [x, y] = doorway.center_px
                                const strokeWidth = Math.max(3, planSize.width / 220)
                                return doorway.orientation === 'horizontal' ? (
                                  <line
                                    key={`manual-door-${index}`}
                                    x1={x - half}
                                    y1={y}
                                    x2={x + half}
                                    y2={y}
                                    stroke="#fbbf24"
                                    strokeWidth={strokeWidth}
                                  />
                                ) : (
                                  <line
                                    key={`manual-door-${index}`}
                                    x1={x}
                                    y1={y - half}
                                    x2={x}
                                    y2={y + half}
                                    stroke="#fbbf24"
                                    strokeWidth={strokeWidth}
                                  />
                                )
                              })}
                            </svg>
                          )}
                        </div>
                        <p className="mt-2 text-xs text-gray-400">
                          Drag to draw one axis-aligned rectangle per room. For each doorway,
                          choose its wall orientation and click on the shared wall. Doorways may
                          sit on room boundaries or the exterior wall.
                        </p>

                        {manualRooms.length > 0 && (
                          <ul className="mt-3 space-y-1 text-xs text-gray-300">
                            {manualRooms.map((polygon, index) => {
                              const xs = polygon.map((point) => point[0])
                              const ys = polygon.map((point) => point[1])
                              return (
                                <li
                                  key={`room-row-${index}`}
                                  className="flex items-center justify-between gap-2 rounded bg-gray-800 px-2 py-1"
                                >
                                  <span>
                                    Room {index + 1}: x {Math.min(...xs)}–{Math.max(...xs)} px, y{' '}
                                    {Math.min(...ys)}–{Math.max(...ys)} px
                                  </span>
                                  <button
                                    type="button"
                                    onClick={() =>
                                      setManualRooms((previous) =>
                                        previous.filter((_, itemIndex) => itemIndex !== index),
                                      )
                                    }
                                    className="text-rose-300 hover:text-rose-200"
                                  >
                                    Remove
                                  </button>
                                </li>
                              )
                            })}
                          </ul>
                        )}

                        {manualDoorways.length > 0 && (
                          <ul className="mt-2 space-y-1 text-xs text-amber-100/90">
                            {manualDoorways.map((doorway, index) => (
                              <li
                                key={`door-row-${index}`}
                                className="flex items-center justify-between gap-2 rounded bg-amber-950/40 px-2 py-1"
                              >
                                <span>
                                  Doorway {index + 1}: {doorway.orientation} wall at (
                                  {doorway.center_px[0]}, {doorway.center_px[1]}) px
                                </span>
                                <button
                                  type="button"
                                  onClick={() =>
                                    setManualDoorways((previous) =>
                                      previous.filter((_, itemIndex) => itemIndex !== index),
                                    )
                                  }
                                  className="text-rose-300 hover:text-rose-200"
                                >
                                  Remove
                                </button>
                              </li>
                            ))}
                          </ul>
                        )}

                        <div className="mt-3 flex flex-wrap gap-2">
                          <button
                            type="button"
                            onClick={handleManualSubmit}
                            disabled={manualSubmitting || manualRooms.length === 0}
                            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:cursor-not-allowed disabled:bg-gray-600"
                          >
                            {manualSubmitting ? 'Submitting...' : 'Reconstruct from corrected outline'}
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              setManualRooms([])
                              setManualDoorways([])
                              setDragStart(null)
                              setDragCurrent(null)
                            }}
                            className="rounded-lg border border-gray-600 px-4 py-2 text-sm font-medium text-gray-200 hover:bg-gray-700"
                          >
                            Clear outline
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {(job.warnings?.length ?? 0) > 0 && (
                  <div className="rounded-lg border border-amber-500/40 bg-amber-950/30 p-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-amber-300">
                      Warnings
                    </p>
                    <ul className="mt-1 list-disc space-y-1 pl-4 text-xs text-amber-100/90">
                      {job.warnings?.map((warning, index) => (
                        <li key={`warning-${index}`}>{warning}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {(job.limitations?.length ?? 0) > 0 && completed && (
                  <div className="rounded-lg border border-gray-600 bg-gray-900/60 p-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                      Known limitations
                    </p>
                    <ul className="mt-1 list-disc space-y-1 pl-4 text-xs text-gray-400">
                      {job.limitations?.map((limitation, index) => (
                        <li key={`limitation-${index}`}>{limitation}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {completed && (
                  <div className="rounded-lg border border-emerald-500/30 bg-emerald-950/20 p-3 text-xs text-emerald-100">
                    <p className="font-medium">
                      {job.room_count ?? 0} room{job.room_count === 1 ? '' : 's'} ·{' '}
                      {job.furniture_count ?? 0} furniture item
                      {job.furniture_count === 1 ? '' : 's'} ·{' '}
                      <span className="capitalize">{job.furnishing_style ?? 'modern'}</span> style ·{' '}
                      {job.navigation_available ? 'all rooms reachable' : 'partial navigation'}
                    </p>
                    {(job.room_types?.length ?? 0) > 0 && (
                      <p className="mt-1 text-emerald-100/75">
                        {job.room_types
                          ?.map((roomType) => roomType.name || `Room ${roomType.room_id + 1}`)
                          .join(' · ')}
                      </p>
                    )}
                  </div>
                )}

                <div className="flex flex-wrap gap-3">
                  {completed && (
                    <Link
                      href={`/tour/${job.job_id}`}
                      className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500"
                    >
                      Open 3D walkthrough
                    </Link>
                  )}
                  {(failed || partialNavigation) && (
                    <button
                      type="button"
                      onClick={() => handleRetry()}
                      disabled={retrying}
                      className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:cursor-not-allowed disabled:bg-gray-600"
                    >
                      {retrying
                        ? 'Re-queueing...'
                        : partialNavigation
                          ? 'Re-run automatic detection'
                          : 'Retry job (same settings)'}
                    </button>
                  )}
                  {!completed && !failed && (
                    <Link
                      href={`/tour/${job.job_id}`}
                      className="rounded-lg border border-gray-600 px-4 py-2 text-sm font-medium text-gray-200 hover:bg-gray-700"
                    >
                      Open status page
                    </Link>
                  )}
                  <a
                    href={`${RECONSTRUCTION_URL}/reconstructions/${job.job_id}/logs`}
                    target="_blank"
                    rel="noreferrer"
                    className="rounded-lg border border-gray-600 px-4 py-2 text-sm font-medium text-gray-200 hover:bg-gray-700"
                  >
                    Engine logs
                  </a>
                </div>
              </div>
            )}
          </div>
        </div>

        {awaitingRoomTypes && job && (
          <div id="room-type-review" className="mt-8 scroll-mt-6">
            <RoomTypeReview
              key={job.job_id}
              jobId={job.job_id}
              onConfirmed={handleJobUpdated}
              style={style}
              variant={job.furnishing_variant ?? 0}
            />
          </div>
        )}

        <div className="mt-8 rounded-lg border border-blue-500/40 bg-blue-950/20 p-6">
          <h3 className="mb-2 font-semibold text-white">What the pipeline actually does</h3>
          <ul className="space-y-2 text-sm text-gray-300">
            <li>1. Validates the uploaded floor plan for readability, size and resolution.</li>
            <li>
              2. Vectorises walls, segments enclosed rooms, detects doorway and window openings,
              and resolves the plan scale.
            </li>
            <li>
              3. Infers room types from geometry and hands uncertain rooms to you for confirmation;
              rooms you leave unspecified are not furnished at random.
            </li>
            <li>
              4. Builds floors, ceilings, walls with openings, collision boxes and a room graph,
              then places real 3D furniture with wall anchoring, door clearances and walkable routes.
            </li>
            <li>
              5. Re-opens the GLB and refuses to mark the job complete unless geometry, furniture
              bounds, collisions and room reachability all validate.
            </li>
          </ul>
        </div>
      </main>
    </div>
  )
}
