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
  { value: 'modern', label: 'Modern Contemporary' },
  { value: 'minimalist', label: 'Minimalist' },
  { value: 'scandinavian', label: 'Scandinavian' },
  { value: 'warm_luxury', label: 'Warm Luxury' },
]

const STYLE_PALETTES: Record<string, { note: string; swatches: string[] }> = {
  modern: {
    note: 'Charcoal fabrics, walnut, muted teal accents',
    swatches: ['#59616b', '#7d5a3c', '#2f6f6f', '#dedbd3'],
  },
  minimalist: {
    note: 'Warm white, pale oak, low-contrast stone',
    swatches: ['#d6d1c7', '#b6a187', '#e6e2da', '#c7c3b9'],
  },
  scandinavian: {
    note: 'Light timber, soft wool, quiet greens',
    swatches: ['#dfe3dc', '#c49a6c', '#4c7a55', '#f3efe6'],
  },
  warm_luxury: {
    note: 'Walnut, brushed brass, deep forest and cream bouclé',
    swatches: ['#6b4a2f', '#c0a061', '#33423a', '#efe7d6'],
  },
}

const STAGES = [
  {
    key: 'uploading',
    label: 'Upload floor plan',
    detail: 'Your original plan is kept unchanged for this project',
  },
  {
    key: 'parsing_floor_plan',
    label: 'Analyzing floor plan',
    detail: 'Reading walls, windows and scale from your upload',
  },
  {
    key: 'detecting_rooms_openings',
    label: 'Detecting rooms and connections',
    detail: 'Finding rooms, openings and how the spaces connect',
  },
  {
    key: 'validating_room_layout',
    label: 'Checking room layout',
    detail: 'Confirming rooms are enclosed and the plan is consistent',
  },
  {
    key: 'awaiting_room_types',
    label: 'Confirm room types',
    detail: 'Your review of each room before furniture is placed',
  },
  {
    key: 'optimizing_openings',
    label: 'Optimizing doors and walkways',
    detail: 'Repairing gaps, doorways and routes between rooms',
  },
  {
    key: 'generating_geometry',
    label: 'Building the 3D house',
    detail: 'Floors, walls, ceilings, openings and room graph',
  },
  {
    key: 'placing_furniture',
    label: 'Arranging furniture',
    detail: 'Furnishing each room without blocking walkways',
  },
  {
    key: 'generating_lighting_materials',
    label: 'Finishing materials and lighting',
    detail: 'Room lighting, PBR materials and shadow setup',
  },
  {
    key: 'validating_navigation_collisions',
    label: 'Testing navigation',
    detail: 'Walking every route to confirm rooms are reachable',
  },
  {
    key: 'loading_walkthrough',
    label: 'Preparing walkthrough',
    detail: 'Exporting the scene, collisions and room graph',
  },
  {
    key: 'completed',
    label: 'Ready to explore',
    detail: 'Validated walkthrough ready',
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
  optimizing_openings: 5,
  generating_geometry: 6,
  building_geometry: 6,
  texturing: 6,
  placing_furniture: 7,
  generating_lighting_materials: 8,
  validating_navigation_collisions: 9,
  exporting: 10,
  loading_walkthrough: 10,
  validating_assets: 10,
  completed: 11,
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
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)

  useEffect(() => {
    if (!floorPlan || !floorPlan.type.startsWith('image/')) {
      const frame = requestAnimationFrame(() => setPreviewUrl(null))
      return () => cancelAnimationFrame(frame)
    }
    const url = URL.createObjectURL(floorPlan)
    const frame = requestAnimationFrame(() => setPreviewUrl(url))
    return () => {
      cancelAnimationFrame(frame)
      URL.revokeObjectURL(url)
    }
  }, [floorPlan])

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
    job?.status === 'failed' || job?.status === 'completed' ? job?.job_id ?? null : null
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

  const validateFloorPlan = (file: File): string | null => {
    const validTypes = ['image/png', 'image/jpeg', 'image/jpg', 'application/pdf']
    if (!validTypes.includes(file.type)) {
      return 'Floor plan must be a PNG, JPG, JPEG or PDF file.'
    }
    if (file.size > MAX_FLOOR_PLAN_BYTES) {
      return `Floor plan is too large (${formatBytes(file.size)}). Maximum 15 MB.`
    }
    if (file.size === 0) {
      return 'That file is empty. Choose a readable floor plan image.'
    }
    return null
  }

  const applyFloorPlan = (file: File | null) => {
    setError(null)
    if (!file) return
    const problem = validateFloorPlan(file)
    if (problem) {
      setError(problem)
      if (fileInputRef.current) fileInputRef.current.value = ''
      return
    }
    setFloorPlan(file)
  }

  const handleFloorPlanUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    applyFloorPlan(event.target.files?.[0] ?? null)
  }

  const clearFloorPlan = () => {
    setFloorPlan(null)
    setError(null)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const handleDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragging(false)
    applyFloorPlan(event.dataTransfer.files?.[0] ?? null)
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
  const repair = diagnostics?.repair
  const repairAttemptCount =
    typeof repair?.attempt_count === 'number'
      ? repair.attempt_count
      : Array.isArray(repair?.attempts)
        ? repair.attempts.length
        : 0
  const repairEntries = repair?.repairs ?? []
  const repairCorrectionCount = repair?.repair_count ?? repairEntries.length
  const repairUnresolved = repair?.unresolved ?? []
  const repairArtifacts = (diagnostics?.artifacts ?? []).filter((artifact) =>
    ['repair_log.json', 'house_model.json', 'validation_report.json'].includes(artifact.name),
  )

  const palette = STYLE_PALETTES[style] ?? STYLE_PALETTES.modern
  const awaitingRoomCount = job?.room_type_review?.rooms ?? job?.room_count ?? null

  return (
    <div className="min-h-screen bg-ivory">
      <Navigation />

      <main className="shell pb-20 pt-10 sm:pt-12">
        <div className="flex flex-wrap items-end justify-between gap-5">
          <div className="animate-rise">
            <p className="eyebrow">New reconstruction</p>
            <h1 className="mt-2 text-[34px] leading-tight sm:text-[42px]">Create a walkthrough</h1>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-stone">
              Upload a 2D floor plan. The engine vectorises walls and openings, segments the rooms,
              asks you to confirm the inferred room types, then builds and validates a furnished
              first-person scene. Photographs are never required.
            </p>
          </div>
          <div className="text-xs">
            {engine === null ? (
              <span className="chip chip-neutral">Checking engine…</span>
            ) : engine.online ? (
              <span className="chip chip-success">
                <span className="h-1.5 w-1.5 rounded-full bg-success" aria-hidden="true" />
                Engine online
                <span className="font-normal opacity-70">
                  · PDF {engine.pdf ? 'supported' : 'unavailable'}
                  {engine.assets ? ` · ${engine.assets} 3D assets` : ''}
                </span>
              </span>
            ) : (
              <span className="chip chip-danger">Engine offline at {RECONSTRUCTION_URL}</span>
            )}
          </div>
        </div>

        <div className="mt-9 grid grid-cols-1 gap-7 lg:grid-cols-[minmax(0,1.02fr)_minmax(0,0.98fr)] lg:items-start">
          <section className="card animate-rise p-6 sm:p-8">
            <div className="flex items-baseline justify-between gap-3 border-b border-line pb-4">
              <h2 className="text-[22px]">Property and plan</h2>
              <span className="text-[11px] uppercase tracking-[0.14em] text-stone-light">
                Step 1 of 2
              </span>
            </div>

            <form onSubmit={handleSubmit} className="mt-6 space-y-6">
              <div>
                <label htmlFor="project-name" className="label">
                  Project name *
                </label>
                <input
                  id="project-name"
                  type="text"
                  value={projectName}
                  onChange={(event) => setProjectName(event.target.value)}
                  className="field"
                  placeholder="e.g. Oak Street apartment"
                  required
                />
              </div>

              <div>
                <span className="label">Floor plan *</span>
                <input
                  ref={fileInputRef}
                  id="floor-plan"
                  type="file"
                  accept={engine?.pdf ? '.png,.jpg,.jpeg,.pdf' : '.png,.jpg,.jpeg'}
                  onChange={handleFloorPlanUpload}
                  className="sr-only"
                />
                <div
                  onDragOver={(event) => {
                    event.preventDefault()
                    setDragging(true)
                  }}
                  onDragLeave={() => setDragging(false)}
                  onDrop={handleDrop}
                  className={`overflow-hidden rounded-[12px] border border-dashed transition-colors ${
                    dragging
                      ? 'border-forest bg-forest/[0.05]'
                      : 'border-line-strong bg-parchment/40'
                  }`}
                >
                  {previewUrl ? (
                    <div className="bg-surface">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={previewUrl}
                        alt={`Uploaded floor plan preview: ${floorPlan?.name ?? 'floor plan'}`}
                        className="mx-auto max-h-64 w-full object-contain p-4"
                      />
                    </div>
                  ) : (
                    <div className="flex flex-col items-center px-6 py-10 text-center">
                      <span className="grid h-12 w-12 place-items-center rounded-xl border border-line-strong bg-surface text-forest">
                        <svg viewBox="0 0 32 32" className="h-6 w-6" aria-hidden="true">
                          <path
                            d="M5 7h22v18H5zM5 15h9M14 15v10M21 7v8M21 15h6"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="1.3"
                            strokeLinejoin="round"
                          />
                        </svg>
                      </span>
                      <p className="mt-3 text-sm font-medium text-ink">
                        {dragging ? 'Release to attach the plan' : 'Drop a floor plan here'}
                      </p>
                      <p className="mt-1 text-xs text-stone">
                        PNG, JPG{engine?.pdf ? ', PDF' : ''} · up to 15 MB
                        {engine && !engine.pdf ? ' · PDF needs poppler in the engine' : ''}
                      </p>
                      <button
                        type="button"
                        onClick={() => fileInputRef.current?.click()}
                        className="btn btn-secondary btn-sm mt-4"
                      >
                        Choose file
                      </button>
                    </div>
                  )}
                  {floorPlan && (
                    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line bg-surface px-4 py-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-ink">{floorPlan.name}</p>
                        <p className="text-[11px] text-stone">
                          {formatBytes(floorPlan.size)} ·{' '}
                          {floorPlan.type.replace('application/', '').replace('image/', '').toUpperCase()}
                        </p>
                      </div>
                      <div className="flex gap-2">
                        <button
                          type="button"
                          onClick={() => fileInputRef.current?.click()}
                          className="btn btn-secondary btn-sm"
                        >
                          Replace
                        </button>
                        <button type="button" onClick={clearFloorPlan} className="btn btn-ghost btn-sm">
                          Remove
                        </button>
                      </div>
                    </div>
                  )}
                </div>
                <p className="mt-2 text-[11px] leading-5 text-stone-light">
                  The plan is the only geometry source and is preserved unchanged for this job.
                </p>
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                <div>
                  <label htmlFor="floors" className="label">
                    Floors
                  </label>
                  <input
                    id="floors"
                    type="number"
                    min={1}
                    max={5}
                    value={numFloors}
                    onChange={(event) => setNumFloors(parseInt(event.target.value) || 1)}
                    className="field"
                  />
                </div>
                <div>
                  <label htmlFor="known-width" className="label">
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
                    className="field"
                    placeholder="optional"
                  />
                </div>
                <div>
                  <label htmlFor="known-length" className="label">
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
                    className="field"
                    placeholder="optional"
                  />
                </div>
              </div>
              <p className="-mt-2 text-[11px] leading-5 text-stone-light">
                Only the ground floor is reconstructed. Supplying both dimensions makes the scale
                measured rather than estimated.
              </p>

              <div>
                <label htmlFor="furnishing-style" className="label">
                  Furniture style
                </label>
                <select
                  id="furnishing-style"
                  value={style}
                  onChange={(event) => setStyle(event.target.value)}
                  className="field"
                >
                  {STYLES.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <div className="mt-3 flex flex-wrap items-center gap-3 rounded-[10px] border border-line bg-parchment/50 px-3 py-2.5">
                  <div className="flex gap-1.5" aria-hidden="true">
                    {palette.swatches.map((hex) => (
                      <span
                        key={hex}
                        className="h-6 w-6 rounded-[5px] border border-black/10"
                        style={{ backgroundColor: hex }}
                      />
                    ))}
                  </div>
                  <p className="text-[11px] text-stone">{palette.note}</p>
                </div>
                <p className="mt-2 text-[11px] leading-5 text-stone-light">
                  Furniture is placed deterministically with real 3D meshes and PBR materials. No
                  external image-generation API is called, and room types are confirmed by you
                  before furnishing.
                </p>
              </div>

              {error && (
                <div className="rounded-[10px] border border-danger/30 bg-danger/[0.06] px-3.5 py-3 text-sm text-danger">
                  {error}
                </div>
              )}

              <button type="submit" disabled={busy} className="btn btn-primary btn-lg w-full">
                {submitting
                  ? 'Uploading…'
                  : busy
                    ? 'Reconstruction running…'
                    : 'Generate furnished tour'}
              </button>
              <p className="text-center text-[11px] leading-5 text-stone-light">
                The job is stored by your local engine. Progress below reflects the actual pipeline
                stage, never a simulated timer.
              </p>
            </form>
          </section>

          <section className="animate-rise delay-1 space-y-5">
            <div className="card p-6">
              <div className="flex items-center justify-between gap-3 border-b border-line pb-4">
                <h2 className="text-[22px]">Reconstruction status</h2>
                {job ? (
                  <span className="flex items-center gap-1.5 text-[11px] text-stone">
                    <span
                      className={`h-1.5 w-1.5 rounded-full ${
                        completed ? 'bg-success' : failed ? 'bg-danger' : 'animate-pulse bg-bronze'
                      }`}
                      aria-hidden="true"
                    />
                    {completed ? 'Complete' : failed ? 'Stopped' : 'Live'}
                  </span>
                ) : (
                  <span className="text-[11px] text-stone-light">Waiting for upload</span>
                )}
              </div>

              {!job && (
                <div className="mt-5">
                  <p className="text-sm leading-6 text-stone">
                    Job progress, real stage names, and validation results appear here after you
                    upload a plan. Every stage below reports engine events, not an animation.
                  </p>
                  <ol className="mt-5 space-y-3">
                    {[
                      ['Analyzing floor plan', 'Readability, wall polarity, scale'],
                      ['Detecting rooms and connections', 'Rooms, doors, windows and links'],
                      ['Optimizing doors and walkways', 'Gaps, openings and routes repaired'],
                      ['Arranging furniture', 'Placement with clear walkways'],
                      ['Testing navigation', 'Reachability checked on the real scene'],
                      ['Preparing walkthrough', 'Scene, collisions and room graph'],
                    ].map(([label, detail], index) => (
                      <li key={label} className="flex items-start gap-3">
                        <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full border border-line bg-parchment text-[11px] font-semibold text-stone">
                          {index + 1}
                        </span>
                        <div>
                          <p className="text-[13px] font-medium text-ink">{label}</p>
                          <p className="text-[11px] text-stone-light">{detail}</p>
                        </div>
                      </li>
                    ))}
                  </ol>
                </div>
              )}

              {job && (
                <div className="mt-5 space-y-4" aria-live="polite">
                  <div className="rounded-[10px] border border-line bg-parchment/60 p-4">
                    <div className="flex items-center justify-between gap-3">
                      <p className="font-medium capitalize text-ink">
                        {failed && failedStageLabel
                          ? `Failed during ${failedStageLabel}`
                          : job.stage_label || job.current_stage.replace(/_/g, ' ')}
                      </p>
                      <span className="font-mono text-sm text-stone">
                        {Math.round(job.progress)}%
                      </span>
                    </div>
                    <div className="progress-track mt-3 !h-1.5 bg-ink/10">
                      <div
                        className={
                          failed
                            ? 'h-full rounded-full bg-danger'
                            : completed
                              ? 'h-full rounded-full bg-success'
                              : 'progress-fill'
                        }
                        style={{ width: `${Math.max(3, Math.round(job.progress))}%` }}
                      />
                    </div>
                    <p className="mt-2 break-all font-mono text-[11px] text-stone-light">
                      Job {job.job_id}
                    </p>
                    {job.error && (
                      <p className="mt-3 break-words text-sm text-danger">{job.error}</p>
                    )}
                    {job.error_code && (
                      <p className="mt-1 break-all font-mono text-[11px] text-danger/70">
                        {job.error_code}
                      </p>
                    )}
                    {awaitingRoomTypes && (
                      <div className="mt-3 rounded-[10px] border border-bronze/30 bg-bronze/[0.07] p-3 text-xs">
                        <p className="font-semibold text-ink">Your review is required</p>
                        <p className="mt-1 leading-5 text-stone">
                          The plan has been parsed and{' '}
                          {awaitingRoomCount != null
                            ? `${awaitingRoomCount} room${awaitingRoomCount === 1 ? '' : 's'} have`
                            : 'rooms have'}{' '}
                          inferred types. Confirm or correct every room before furniture is placed
                          and the walkthrough is generated.
                        </p>
                        <a href="#room-type-review" className="btn btn-bronze btn-sm mt-2.5">
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
                        <li key={stage.key} className="flex items-start gap-3">
                          <span
                            className={`mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full text-[11px] font-semibold ${
                              stageFailed
                                ? 'bg-danger text-white'
                                : active
                                  ? 'bg-forest text-ivory'
                                  : reached
                                    ? 'border border-forest/30 bg-forest/[0.08] text-forest'
                                    : 'border border-line bg-parchment/60 text-stone-light'
                            }`}
                          >
                            {stageFailed ? '!' : reached && !active ? '✓' : index + 1}
                          </span>
                          <div>
                            <p
                              className={`text-[13px] font-medium ${
                                stageFailed
                                  ? 'text-danger'
                                  : active
                                    ? 'text-ink'
                                    : reached
                                      ? 'text-ink-soft'
                                      : 'text-stone'
                              }`}
                            >
                              {stage.label}
                            </p>
                            <p className="mt-0.5 text-[11px] leading-5 text-stone-light">
                              {stage.detail}
                            </p>
                          </div>
                        </li>
                      )
                    })}
                  </ol>

                  {(failed || partialNavigation) && (
                    <div className="rounded-[12px] border border-danger/30 bg-danger/[0.05] p-4">
                      <p className="text-sm font-semibold text-danger">
                        {failed
                          ? failedStageLabel
                            ? `Detection stopped at ${failedStageLabel}`
                            : 'Reconstruction did not produce a scene'
                          : 'Completed with unreachable rooms'}
                      </p>
                      <p className="mt-1 break-words text-sm leading-6 text-ink-soft">
                        {failed
                          ? job.error ||
                            'The engine rejected the inputs instead of substituting a sample.'
                          : 'The scene passed geometry and asset validation, but at least one room has no detected doorway. The walkthrough is labelled partial navigation.'}
                      </p>
                      <p className="mt-2 text-[11px] leading-5 text-stone">
                        {failed
                          ? 'Your original upload is preserved. Inspect what was detected, re-run with different preprocessing, or correct the room outlines below.'
                          : 'Inspect what was detected, re-run automatic detection with different preprocessing, or correct the room outlines below. A re-run replaces this job’s scene.'}
                      </p>

                      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                        <figure>
                          <figcaption className="mb-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-stone">
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
                              className="max-h-56 w-full rounded-[8px] border border-line bg-surface object-contain"
                            />
                          </a>
                        </figure>
                        {diagnosticImages.map((artifact) => (
                          <figure key={artifact.name}>
                            <figcaption className="mb-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-stone">
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
                                className="max-h-56 w-full rounded-[8px] border border-line bg-surface object-contain"
                              />
                            </a>
                          </figure>
                        ))}
                      </div>
                      {diagnosticsError && (
                        <p className="mt-2 text-xs text-warning">
                          Diagnostics could not be loaded: {diagnosticsError}
                        </p>
                      )}
                      {(diagnostics?.artifacts.length ?? 0) > 0 && (
                        <details className="mt-3 text-xs text-stone">
                          <summary className="cursor-pointer text-ink-soft">
                            All diagnostic files (saved for this job)
                          </summary>
                          <ul className="mt-1 space-y-1">
                            {diagnostics?.artifacts.map((artifact) => (
                              <li key={artifact.name}>
                                <a
                                  href={`${RECONSTRUCTION_URL}${artifact.url}`}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="text-forest-soft hover:text-forest"
                                >
                                  {artifact.name}
                                </a>{' '}
                                <span className="text-stone-light">
                                  {formatBytes(artifact.size_bytes)}
                                </span>
                              </li>
                            ))}
                          </ul>
                        </details>
                      )}

                      {(diagnostics?.preprocessing.options.length ?? 0) > 0 && (
                        <div className="mt-4">
                          <p className="text-[10px] font-semibold uppercase tracking-[0.13em] text-stone">
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
                                className="btn btn-secondary btn-sm flex-col !items-start !gap-0.5 text-left"
                              >
                                <span className="block font-medium">{option.label}</span>
                                <span className="block font-mono text-[10px] font-normal text-stone">
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
                        className="btn btn-secondary btn-sm mt-4"
                      >
                        {showCorrection ? 'Hide manual correction' : 'Correct room outlines manually'}
                      </button>

                      {showCorrection && (
                        <div className="mt-3 rounded-[10px] border border-line bg-surface p-3">
                          <div className="flex flex-wrap items-center gap-2 text-xs">
                            <button
                              type="button"
                              onClick={() => setManualTool('room')}
                              className={`rounded-[7px] px-3 py-1.5 font-medium transition-colors ${
                                manualTool === 'room'
                                  ? 'bg-forest text-ivory'
                                  : 'bg-parchment text-stone hover:text-ink'
                              }`}
                            >
                              Draw room rectangle
                            </button>
                            <button
                              type="button"
                              onClick={() => setManualTool('doorway')}
                              className={`rounded-[7px] px-3 py-1.5 font-medium transition-colors ${
                                manualTool === 'doorway'
                                  ? 'bg-bronze text-white'
                                  : 'bg-parchment text-stone hover:text-ink'
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
                                className="rounded-[7px] border border-line-strong bg-surface px-2 py-1.5 text-stone"
                              >
                                <option value="vertical">On a vertical wall</option>
                                <option value="horizontal">On a horizontal wall</option>
                              </select>
                            )}
                          </div>

                          <div className="relative mt-3 select-none overflow-hidden rounded-[8px] border border-line bg-surface">
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
                                    fill="rgba(35,57,46,0.20)"
                                    stroke="#23392e"
                                    strokeWidth={Math.max(2, planSize.width / 300)}
                                  />
                                ))}
                                {dragStart && dragCurrent && (
                                  <rect
                                    x={Math.min(dragStart[0], dragCurrent[0])}
                                    y={Math.min(dragStart[1], dragCurrent[1])}
                                    width={Math.abs(dragStart[0] - dragCurrent[0])}
                                    height={Math.abs(dragStart[1] - dragCurrent[1])}
                                    fill="rgba(164,123,76,0.16)"
                                    stroke="#a47b4c"
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
                                      stroke="#b0603f"
                                      strokeWidth={strokeWidth}
                                    />
                                  ) : (
                                    <line
                                      key={`manual-door-${index}`}
                                      x1={x}
                                      y1={y - half}
                                      x2={x}
                                      y2={y + half}
                                      stroke="#b0603f"
                                      strokeWidth={strokeWidth}
                                    />
                                  )
                                })}
                              </svg>
                            )}
                          </div>
                          <p className="mt-2 text-[11px] leading-5 text-stone">
                            Drag to draw one axis-aligned rectangle per room. For each doorway,
                            choose its wall orientation and click on the shared wall. Doorways may
                            sit on room boundaries or the exterior wall.
                          </p>

                          {manualRooms.length > 0 && (
                            <ul className="mt-3 space-y-1 text-[11px] text-ink-soft">
                              {manualRooms.map((polygon, index) => {
                                const xs = polygon.map((point) => point[0])
                                const ys = polygon.map((point) => point[1])
                                return (
                                  <li
                                    key={`room-row-${index}`}
                                    className="flex items-center justify-between gap-2 rounded-[6px] bg-parchment px-2 py-1"
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
                                      className="text-danger hover:opacity-70"
                                    >
                                      Remove
                                    </button>
                                  </li>
                                )
                              })}
                            </ul>
                          )}

                          {manualDoorways.length > 0 && (
                            <ul className="mt-2 space-y-1 text-[11px] text-ink-soft">
                              {manualDoorways.map((doorway, index) => (
                                <li
                                  key={`door-row-${index}`}
                                  className="flex items-center justify-between gap-2 rounded-[6px] bg-bronze/10 px-2 py-1"
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
                                    className="text-danger hover:opacity-70"
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
                              className="btn btn-primary btn-sm"
                            >
                              {manualSubmitting
                                ? 'Submitting…'
                                : 'Reconstruct from corrected outline'}
                            </button>
                            <button
                              type="button"
                              onClick={() => {
                                setManualRooms([])
                                setManualDoorways([])
                                setDragStart(null)
                                setDragCurrent(null)
                              }}
                              className="btn btn-secondary btn-sm"
                            >
                              Clear outline
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {(job.warnings?.length ?? 0) > 0 && (
                    <div className="rounded-[10px] border border-warning/30 bg-warning/[0.07] p-3">
                      <p className="text-[10px] font-semibold uppercase tracking-[0.13em] text-warning">
                        Warnings
                      </p>
                      <ul className="mt-1 list-disc space-y-1 pl-4 text-[11px] leading-5 text-ink-soft">
                        {job.warnings?.map((warning, index) => (
                          <li key={`warning-${index}`}>{warning}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {(job.limitations?.length ?? 0) > 0 && completed && (
                    <div className="rounded-[10px] border border-line bg-parchment/50 p-3">
                      <p className="text-[10px] font-semibold uppercase tracking-[0.13em] text-stone">
                        Known limitations
                      </p>
                      <ul className="mt-1 list-disc space-y-1 pl-4 text-[11px] leading-5 text-stone">
                        {job.limitations?.map((limitation, index) => (
                          <li key={`limitation-${index}`}>{limitation}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {completed && (
                    <div className="rounded-[10px] border border-success/30 bg-success/[0.07] p-3 text-xs text-ink-soft">
                      <p className="font-medium text-success">
                        {job.room_count ?? 0} room{job.room_count === 1 ? '' : 's'} ·{' '}
                        {job.furniture_count ?? 0} furniture item
                        {job.furniture_count === 1 ? '' : 's'} ·{' '}
                        <span className="capitalize">{job.furnishing_style ?? 'modern'}</span> style
                        · {job.navigation_available ? 'all rooms reachable' : 'partial navigation'}
                      </p>
                      {(job.room_types?.length ?? 0) > 0 && (
                        <p className="mt-1 leading-5 text-stone">
                          {job.room_types
                            ?.map((roomType) => roomType.name || `Room ${roomType.room_id + 1}`)
                            .join(' · ')}
                        </p>
                      )}
                      <Link href={`/tour/${job.job_id}`} className="btn btn-primary btn-sm mt-3">
                        Open 3D walkthrough
                      </Link>
                    </div>
                  )}

                  {repair &&
                    (repairAttemptCount > 0 ||
                      repairCorrectionCount > 0 ||
                      repairUnresolved.length > 0) && (
                      <details className="rounded-[10px] border border-line bg-parchment/50 p-3 text-[11px] text-stone">
                        <summary className="cursor-pointer text-ink-soft">
                          Developer diagnostics — automatic repair ({repairAttemptCount} attempt
                          {repairAttemptCount === 1 ? '' : 's'}, {repairCorrectionCount} correction
                          {repairCorrectionCount === 1 ? '' : 's'}
                          {repair?.conservative_fallback ? ', conservative fallback' : ''})
                        </summary>
                        <p className="mt-2 text-stone-light">
                          Internal validate → diagnose → repair → revalidate record for this job.
                          This panel is not shown in the buyer walkthrough.
                        </p>
                        {repair?.status && (
                          <p className="mt-2 text-stone">
                            Status: <span className="font-mono">{repair.status}</span>
                            {diagnostics?.house_model?.spawn?.room_id !== undefined && (
                              <>
                                {' '}· spawn room{' '}
                                <span className="font-mono">
                                  {diagnostics.house_model.spawn.room_id}
                                </span>
                              </>
                            )}
                            {typeof diagnostics?.house_model?.rooms === 'number' && (
                              <>
                                {' '}· {diagnostics.house_model.rooms} room(s),{' '}
                                {diagnostics.house_model.doorways ?? 0} doorway(s),{' '}
                                {diagnostics.house_model.walls ?? 0} wall segment(s)
                              </>
                            )}
                          </p>
                        )}
                        {repairCorrectionCount > 0 && (
                          <ul className="mt-2 list-disc space-y-1 pl-4">
                            {repairEntries.map((entry, index) => (
                              <li key={`repair-${entry.code ?? 'entry'}-${index}`}>
                                <span className="font-mono text-stone">
                                  {entry.code ?? 'repair'}
                                </span>
                                {entry.detail ? ` — ${entry.detail}` : ''}
                                {entry.result ? (
                                  <span className="text-stone-light"> ({entry.result})</span>
                                ) : null}
                              </li>
                            ))}
                          </ul>
                        )}
                        {repairUnresolved.length > 0 && (
                          <div className="mt-2">
                            <p className="font-semibold text-danger">Unresolved</p>
                            <ul className="mt-1 list-disc space-y-1 pl-4 text-danger/80">
                              {repairUnresolved.map((entry, index) => (
                                <li key={`unresolved-${index}`}>{entry}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {repairArtifacts.length > 0 && (
                          <p className="mt-2 flex flex-wrap gap-3">
                            {repairArtifacts.map((artifact) => (
                              <a
                                key={artifact.name}
                                href={`${RECONSTRUCTION_URL}${artifact.url}`}
                                target="_blank"
                                rel="noreferrer"
                                className="text-forest-soft hover:text-forest"
                              >
                                {artifact.name}
                              </a>
                            ))}
                          </p>
                        )}
                      </details>
                    )}

                  <div className="flex flex-wrap gap-2.5">
                    {(failed || partialNavigation) && (
                      <button
                        type="button"
                        onClick={() => handleRetry()}
                        disabled={retrying}
                        className="btn btn-secondary btn-sm"
                      >
                        {retrying
                          ? 'Re-queueing…'
                          : partialNavigation
                            ? 'Re-run automatic detection'
                            : 'Retry job (same settings)'}
                      </button>
                    )}
                    {!completed && !failed && (
                      <Link href={`/tour/${job.job_id}`} className="btn btn-secondary btn-sm">
                        Open status page
                      </Link>
                    )}
                    <a
                      href={`${RECONSTRUCTION_URL}/reconstructions/${job.job_id}/logs`}
                      target="_blank"
                      rel="noreferrer"
                      className="btn btn-ghost btn-sm"
                    >
                      Engine logs
                    </a>
                  </div>
                </div>
              )}
            </div>

            {!job && (
              <div className="rounded-[12px] border border-line bg-surface px-5 py-4">
                <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-stone">
                  What you will receive
                </p>
                <p className="mt-1.5 text-[13px] leading-6 text-ink-soft">
                  A GLB scene, collision map, room graph, furniture manifest, and a validation
                  report — all tied to the job id shown here once the upload starts.
                </p>
              </div>
            )}
          </section>
        </div>

        {awaitingRoomTypes && job && (
          <div id="room-type-review" className="mt-8 scroll-mt-24">
            <RoomTypeReview
              key={job.job_id}
              jobId={job.job_id}
              onConfirmed={handleJobUpdated}
              style={style}
              variant={job.furnishing_variant ?? 0}
            />
          </div>
        )}

        <section className="card mt-8 p-6 sm:p-8">
          <div className="max-w-2xl">
            <p className="eyebrow">Transparency</p>
            <h2 className="mt-2 text-[26px]">What the pipeline actually does</h2>
          </div>
          <ol className="mt-6 grid gap-5 sm:grid-cols-2 xl:grid-cols-5">
            {[
              'Validates the uploaded floor plan for readability, size, and resolution.',
              'Vectorises walls, segments enclosed rooms, detects door and window openings, and resolves the plan scale.',
              'Infers room types from geometry and hands uncertain rooms to you for confirmation; rooms left unspecified are not furnished at random.',
              'Builds floors, ceilings, walls with openings, collision boxes, and a room graph, then places real 3D furniture with wall anchoring, door clearances, and walkable routes.',
              'Re-opens the GLB and refuses to mark the job complete unless geometry, furniture bounds, collisions, and room reachability all validate.',
            ].map((item, index) => (
              <li key={index} className="border-t border-line pt-3">
                <p className="font-serif text-[13px] text-bronze">
                  {String(index + 1).padStart(2, '0')}
                </p>
                <p className="mt-1.5 text-[12px] leading-5 text-stone">{item}</p>
              </li>
            ))}
          </ol>
        </section>
      </main>
    </div>
  )
}
