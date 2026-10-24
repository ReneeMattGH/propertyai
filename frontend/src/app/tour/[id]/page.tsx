'use client'

import { Suspense, useCallback, useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import GameEngine from '@/components/GameEngine'
import Navigation from '@/components/Navigation'
import RoomTypeReview from '@/components/RoomTypeReview'
import {
  RECONSTRUCTION_URL,
  cancelJob,
  fetchJob,
  retryJob,
  updateFurnishings,
  type ReconstructionJob,
} from '@/lib/reconstruction'

const TERMINAL = new Set(['completed', 'failed', 'cancelled'])

const STYLES = [
  { value: 'modern', label: 'Modern' },
  { value: 'minimalist', label: 'Minimalist' },
  { value: 'scandinavian', label: 'Scandinavian' },
]

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export default function TourViewerPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-gray-900">
          <Navigation />
          <div className="flex h-64 items-center justify-center text-white">Loading tour...</div>
        </div>
      }
    >
      <TourViewerContent />
    </Suspense>
  )
}

function TourViewerContent() {
  const params = useParams()
  const jobId = Array.isArray(params.id) ? params.id[0] : params.id

  const [job, setJob] = useState<ReconstructionJob | null>(null)
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showDetails, setShowDetails] = useState(false)
  const [showFurnishing, setShowFurnishing] = useState(false)
  const [showRoomTypes, setShowRoomTypes] = useState(false)
  const [selectedStyle, setSelectedStyle] = useState('modern')
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionBusy, setActionBusy] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  const refresh = useCallback(
    async (signal?: AbortSignal): Promise<ReconstructionJob | null> => {
      if (!jobId) {
        setNotFound(true)
        setLoading(false)
        return null
      }
      try {
        const data = await fetchJob(jobId, signal)
        setJob(data)
        if (data.furnishing_style) setSelectedStyle(data.furnishing_style)
        setNotFound(false)
        setError(null)
        return data
      } catch (caught) {
        if (signal?.aborted) return null
        const message = messageOf(caught)
        if (/\b404\b|not found/i.test(message)) {
          setNotFound(true)
        } else {
          setError(message)
        }
        return null
      } finally {
        if (!signal?.aborted) setLoading(false)
      }
    },
    [jobId],
  )

  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | null = null
    let stopped = false

    const tick = async () => {
      const data = await refresh(controller.signal)
      if (!data || stopped) return
      if (!TERMINAL.has(data.status)) {
        timer = setTimeout(tick, 2000)
      }
    }

    void tick()
    return () => {
      stopped = true
      controller.abort()
      if (timer) clearTimeout(timer)
    }
  }, [refresh, reloadKey])

  const handleRetry = async () => {
    if (!jobId) return
    setActionBusy(true)
    setActionError(null)
    try {
      const next = await retryJob(jobId)
      setJob(next)
      setReloadKey((value) => value + 1)
    } catch (caught) {
      setActionError(messageOf(caught))
    } finally {
      setActionBusy(false)
    }
  }

  const handleCancel = async () => {
    if (!jobId) return
    setActionBusy(true)
    setActionError(null)
    try {
      const next = await cancelJob(jobId)
      setJob(next)
    } catch (caught) {
      setActionError(messageOf(caught))
    } finally {
      setActionBusy(false)
    }
  }

  const applyFurnishing = async (options: { style?: string; regenerate?: boolean }) => {
    if (!jobId) return
    setActionBusy(true)
    setActionError(null)
    try {
      const next = await updateFurnishings(jobId, {
        ...(options.style ? { style: options.style } : {}),
        ...(options.regenerate ? { regenerate: true } : {}),
      })
      setJob(next)
      setReloadKey((value) => value + 1)
    } catch (caught) {
      setActionError(messageOf(caught))
    } finally {
      setActionBusy(false)
    }
  }

  const handleRoomTypesConfirmed = (next: ReconstructionJob) => {
    setJob(next)
    setActionError(null)
    setReloadKey((value) => value + 1)
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-900">
        <Navigation />
        <div className="flex h-64 items-center justify-center text-white">Loading tour...</div>
      </div>
    )
  }

  if (notFound) {
    return (
      <div className="min-h-screen bg-gray-900">
        <Navigation />
        <div className="mx-auto max-w-xl px-4 py-24 text-center">
          <h1 className="text-2xl font-bold text-white">Tour not found</h1>
          <p className="mt-3 text-gray-400">
            This reconstruction ID does not exist on the local engine. It may have been created on
            a different machine or the job store was reset.
          </p>
          <div className="mt-6 flex justify-center gap-3">
            <Link
              href="/tours"
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500"
            >
              Browse tours
            </Link>
            <Link
              href="/create-tour"
              className="rounded-lg border border-gray-600 px-4 py-2 text-sm font-medium text-gray-200 hover:bg-gray-800"
            >
              Create a tour
            </Link>
          </div>
        </div>
      </div>
    )
  }

  if (!jobId) return null

  if (error || !job) {
    return (
      <div className="min-h-screen bg-gray-900">
        <Navigation />
        <div className="mx-auto max-w-xl px-4 py-24 text-center">
          <h1 className="text-2xl font-bold text-white">Cannot reach the reconstruction engine</h1>
          <p className="mt-3 break-words text-gray-400">
            {error || 'The reconstruction engine did not return this job.'}
          </p>
          <p className="mt-4 text-sm text-gray-500">
            Start the engine with{' '}
            <code className="rounded bg-gray-800 px-2 py-1 text-gray-300">
              venv/bin/uvicorn main:app --port 8002
            </code>{' '}
            and try again.
          </p>
          <button
            onClick={() => {
              setLoading(true)
              setReloadKey((value) => value + 1)
            }}
            className="mt-6 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500"
          >
            Retry
          </button>
        </div>
      </div>
    )
  }

  if (job.status !== 'completed') {
    const failed = job.status === 'failed' || job.status === 'cancelled'
    const awaitingRoomTypes = job.status === 'awaiting_room_types'
    return (
      <div className="min-h-screen bg-gray-900">
        <Navigation />
        <div className="mx-auto max-w-2xl px-4 py-16">
          <div className="rounded-2xl border border-gray-700 bg-gray-800 p-8">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-blue-400">
              Reconstruction {job.status.replace(/_/g, ' ')}
            </p>
            <h1 className="mt-2 text-2xl font-bold text-white">
              {job.project_name || 'Property tour'}
            </h1>
            <p className="mt-2 text-sm text-gray-400">
              {job.stage_label || job.current_stage} · {Math.round(job.progress)}%
            </p>
            {!failed && (
              <div className="mt-5 h-2 overflow-hidden rounded-full bg-gray-700">
                <div
                  className="h-full rounded-full bg-blue-500 transition-[width] duration-500"
                  style={{ width: `${Math.max(3, Math.round(job.progress))}%` }}
                />
              </div>
            )}
            {job.error && (
              <div className="mt-5 rounded-lg border border-rose-500/40 bg-rose-950/40 p-4 text-sm text-rose-200">
                {job.error}
              </div>
            )}
            {failed && (
              <p className="mt-4 text-sm text-gray-400">
                No viewer is shown for failed jobs because there is no validated scene to load.
                {job.retry_count ? ` This job has been retried ${job.retry_count} time(s).` : ''}
              </p>
            )}
            {actionError && (
              <p className="mt-4 text-sm text-rose-300">{actionError}</p>
            )}
            <div className="mt-6 flex flex-wrap gap-3">
              {failed ? (
                <button
                  onClick={handleRetry}
                  disabled={actionBusy}
                  className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-50"
                >
                  {actionBusy ? 'Starting...' : 'Retry reconstruction'}
                </button>
              ) : (
                <button
                  onClick={handleCancel}
                  disabled={actionBusy}
                  className="rounded-lg border border-gray-600 px-4 py-2 text-sm font-medium text-gray-200 hover:bg-gray-700 disabled:opacity-50"
                >
                  {actionBusy ? 'Requesting...' : 'Cancel reconstruction'}
                </button>
              )}
              <Link
                href="/tours"
                className="rounded-lg border border-gray-600 px-4 py-2 text-sm font-medium text-gray-200 hover:bg-gray-700"
              >
                All tours
              </Link>
              <a
                href={`${RECONSTRUCTION_URL}/reconstructions/${jobId}/logs`}
                target="_blank"
                rel="noreferrer"
                className="rounded-lg border border-gray-600 px-4 py-2 text-sm font-medium text-gray-200 hover:bg-gray-700"
              >
                Engine logs
              </a>
            </div>
            {!failed && (
              <p className="mt-4 text-xs text-gray-500">
                This page refreshes automatically. You can safely leave and reopen it.
              </p>
            )}
          </div>
          {awaitingRoomTypes && (
            <div className="mt-6">
              <RoomTypeReview
                key={job.job_id}
                jobId={job.job_id}
                onConfirmed={handleRoomTypesConfirmed}
                style={job.furnishing_style ?? undefined}
                variant={job.furnishing_variant ?? 0}
              />
            </div>
          )}
        </div>
      </div>
    )
  }

  const warnings = job.warnings ?? []
  const limitations = job.limitations ?? []
  const diagnostics = job.diagnostics as Record<string, unknown> | null | undefined
  const validation = diagnostics?.scene_validation as Record<string, unknown> | undefined
  const checks = (validation?.checks ?? {}) as Record<string, boolean>
  const stats = (validation?.stats ?? {}) as Record<string, unknown>
  const roomTypes = job.room_types ?? []

  return (
    <div className="flex min-h-screen flex-col bg-gray-900">
      <Navigation />

      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-800 bg-gray-900/95 px-4 py-3 sm:px-6">
        <div className="min-w-0">
          <h1 className="truncate text-lg font-semibold text-white">
            {job.project_name || 'Property walkthrough'}
          </h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-gray-400">
            <span>{job.room_count ?? 0} rooms</span>
            <span aria-hidden>·</span>
            <span>{job.furniture_count ?? 0} furniture items</span>
            <span aria-hidden>·</span>
            <span className="capitalize">{job.furnishing_style ?? 'modern'} style</span>
            <span aria-hidden>·</span>
            <span>{job.scale_verified ? 'Measured dimensions' : 'Estimated scale'}</span>
            <span aria-hidden>·</span>
            <span>{job.navigation_available ? 'All rooms reachable' : 'Partial navigation'}</span>
            {job.retry_count ? (
              <>
                <span aria-hidden>·</span>
                <span>retry {job.retry_count}</span>
              </>
            ) : null}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => setShowFurnishing((value) => !value)}
            className="rounded-lg border border-emerald-600/60 bg-emerald-950/30 px-3 py-2 text-xs font-medium text-emerald-200 hover:bg-emerald-900/40"
            aria-expanded={showFurnishing}
          >
            {showFurnishing ? 'Hide furnishing' : 'Furnishing'}
          </button>
          <button
            onClick={() => setShowDetails((value) => !value)}
            className="rounded-lg border border-gray-700 px-3 py-2 text-xs font-medium text-gray-200 hover:bg-gray-800"
            aria-expanded={showDetails}
          >
            {showDetails ? 'Hide details' : 'Reconstruction details'}
          </button>
          <a
            href={`${RECONSTRUCTION_URL}/reconstructions/${jobId}/report`}
            target="_blank"
            rel="noreferrer"
            className="rounded-lg border border-gray-700 px-3 py-2 text-xs font-medium text-gray-200 hover:bg-gray-800"
          >
            Validation report
          </a>
          <Link
            href="/create-tour"
            className="rounded-lg bg-blue-600 px-3 py-2 text-xs font-medium text-white hover:bg-blue-500"
          >
            New reconstruction
          </Link>
        </div>
      </div>

      {showFurnishing && (
        <div className="border-b border-gray-800 bg-gray-800/70 px-4 py-4 text-sm text-gray-300 sm:px-6">
          <div className="grid gap-4 lg:grid-cols-2">
            <div
              className="rounded-lg border border-gray-700 bg-gray-900/60 p-4"
              data-testid="furnishing-controls"
            >
              <h2 className="text-sm font-semibold text-white">Furnishing controls</h2>
              <p className="mt-1 text-xs text-gray-400">
                A style change swaps furniture models and materials without moving walls, doors or
                room boundaries. Regenerate layout picks another deterministic arrangement for the
                same rooms and confirmed types.
              </p>
              <div className="mt-3 flex flex-wrap items-end gap-2">
                <label className="text-xs text-gray-400">
                  <span className="mb-1 block">Style</span>
                  <select
                    value={selectedStyle}
                    onChange={(event) => setSelectedStyle(event.target.value)}
                    data-testid="furnishing-style"
                    className="rounded border border-gray-600 bg-gray-800 px-3 py-2 text-sm text-white"
                  >
                    {STYLES.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  onClick={() => applyFurnishing({ style: selectedStyle })}
                  disabled={actionBusy || selectedStyle === (job.furnishing_style ?? 'modern')}
                  className="rounded-lg bg-emerald-600 px-3 py-2 text-xs font-medium text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:bg-gray-600"
                >
                  {actionBusy ? 'Rebuilding...' : 'Apply style'}
                </button>
                <button
                  type="button"
                  onClick={() => applyFurnishing({ regenerate: true })}
                  disabled={actionBusy}
                  className="rounded-lg border border-gray-600 px-3 py-2 text-xs font-medium text-gray-100 hover:bg-gray-700 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Regenerate layout
                </button>
                <button
                  type="button"
                  onClick={() => setShowRoomTypes((value) => !value)}
                  className="rounded-lg border border-gray-600 px-3 py-2 text-xs font-medium text-gray-100 hover:bg-gray-700"
                  aria-expanded={showRoomTypes}
                >
                  {showRoomTypes ? 'Hide room types' : 'Edit room types'}
                </button>
              </div>
              <p className="mt-2 text-xs text-gray-500">
                Current: <span className="capitalize">{job.furnishing_style ?? 'modern'}</span> style ·
                variant {job.furnishing_variant ?? 0} · {job.furniture_count ?? 0} furniture items.
                Rebuilding temporarily returns this job to the progress view.
              </p>
              {actionError && (
                <p className="mt-2 rounded border border-rose-500/40 bg-rose-950/40 p-2 text-xs text-rose-200">
                  {actionError}
                </p>
              )}
            </div>
            <div
              className="rounded-lg border border-gray-700 bg-gray-900/60 p-4"
              data-testid="room-summary"
            >
              <h2 className="text-sm font-semibold text-white">Room summary</h2>
              {roomTypes.length === 0 ? (
                <p className="mt-1 text-xs text-gray-400">
                  Room types are not stored on this job. Open room types below to review them.
                </p>
              ) : (
                <ul className="mt-2 grid grid-cols-1 gap-1 text-xs text-gray-300 sm:grid-cols-2">
                  {roomTypes.map((roomType) => (
                    <li key={roomType.room_id} className="flex justify-between gap-2">
                      <span className="truncate">{roomType.name || `Room ${roomType.room_id + 1}`}</span>
                      <span className="text-gray-500">
                        {(roomType.room_type ?? 'unknown').replace(/_/g, ' ')}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-2 text-xs text-gray-500">
                Walls, doors and room sizes always come from the uploaded floor plan. Furniture
                never rewrites the architecture.
              </p>
            </div>
          </div>
          {showRoomTypes && (
            <div className="mt-4">
              <RoomTypeReview
                key={job.job_id}
                jobId={job.job_id}
                onConfirmed={handleRoomTypesConfirmed}
                style={selectedStyle}
                variant={job.furnishing_variant ?? 0}
              />
            </div>
          )}
        </div>
      )}

      {showDetails && (
        <div className="border-b border-gray-800 bg-gray-800/70 px-4 py-4 text-sm text-gray-300 sm:px-6">
          <div className="grid gap-4 md:grid-cols-3">
            <dl className="space-y-1 text-xs">
              <div className="flex justify-between gap-3">
                <dt className="text-gray-500">Property ID</dt>
                <dd className="truncate">{job.property_id || job.job_id}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-gray-500">Floor plan</dt>
                <dd className="truncate">{job.floor_plan_filename || 'uploaded plan'}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-gray-500">Requested floors</dt>
                <dd>{job.num_floors ?? 1} (ground floor modelled)</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-gray-500">Room types</dt>
                <dd>{job.room_types_confirmed ? `${roomTypes.length} confirmed` : 'not confirmed'}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-gray-500">Furnishing</dt>
                <dd className="capitalize">
                  {job.furnishing_style ?? 'modern'} · variant {job.furnishing_variant ?? 0}
                </dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-gray-500">Furniture items</dt>
                <dd>{job.furniture_count ?? 0}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-gray-500">Rooms / doorways</dt>
                <dd>
                  {String(stats.rooms ?? '—')} / {String(stats.doors ?? '—')}
                </dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-gray-500">Geometry</dt>
                <dd>{job.geometry_valid ? 'Validated GLB' : 'Not validated'}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-gray-500">Spawn walkable</dt>
                <dd>{checks.spawn_walkable ? 'Yes' : 'Not verified'}</dd>
              </div>
            </dl>
            <div className="md:col-span-2">
              {warnings.length > 0 && (
                <div className="mb-3 rounded-lg border border-amber-500/40 bg-amber-950/30 p-3">
                  <p className="text-xs font-semibold uppercase tracking-wide text-amber-300">
                    Warnings
                  </p>
                  <ul className="mt-1 list-disc space-y-1 pl-4 text-xs text-amber-100/90">
                    {warnings.map((warning, index) => (
                      <li key={`warning-${index}`}>{warning}</li>
                    ))}
                  </ul>
                </div>
              )}
              <div className="rounded-lg border border-gray-700 bg-gray-900/60 p-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                  Method and limitations
                </p>
                <p className="mt-1 text-xs text-gray-400">
                  Layout comes from 2D wall vectorisation and room segmentation of the uploaded
                  floor plan. Room types are inferred from geometry and confirmed by you. Furniture
                  uses real 3D meshes placed deterministically inside each room with wall anchoring,
                  doorway and window clearances, and walkable-route checks. The floor plan is the
                  only input; no photographs or external image-generation service are used.
                </p>
                <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-gray-400">
                  {limitations.map((limitation, index) => (
                    <li key={`limitation-${index}`}>{limitation}</li>
                  ))}
                </ul>
              </div>
            </div>
          </div>
        </div>
      )}

      {warnings.length > 0 && !showDetails && (
        <div className="border-b border-amber-500/30 bg-amber-950/30 px-4 py-2 text-xs text-amber-200 sm:px-6">
          {warnings[0]}
          {warnings.length > 1 ? ` (+${warnings.length - 1} more in details)` : ''}
        </div>
      )}

      <div className="relative min-h-[70vh] flex-1">
        <GameEngine
          key={`${jobId}-${job.furnishing_style ?? 'modern'}-${job.furnishing_variant ?? 0}`}
          jobId={jobId}
        />
      </div>
    </div>
  )
}
