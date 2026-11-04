'use client'

import { Suspense, useCallback, useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import GameEngine from '@/components/GameEngine'
import Navigation from '@/components/Navigation'
import { FURNISHING_STYLES, styleLabel } from '@/lib/furnishing'
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

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export default function TourViewerPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-ivory text-sm text-stone">
          Loading walkthrough…
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
      <div className="min-h-screen bg-ivory">
        <Navigation />
        <div className="flex h-64 items-center justify-center gap-3 text-sm text-stone">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-bronze" aria-hidden="true" />
          Loading walkthrough…
        </div>
      </div>
    )
  }

  if (notFound) {
    return (
      <div className="min-h-screen bg-ivory">
        <Navigation />
        <div className="shell py-24">
          <div className="card mx-auto max-w-xl px-8 py-12 text-center">
            <p className="eyebrow">Not found</p>
            <h1 className="mt-2 text-3xl">This walkthrough does not exist</h1>
            <p className="mt-3 text-sm leading-6 text-stone">
              The reconstruction ID is not stored on the local engine. It may have been created on
              a different machine, or the job store was reset.
            </p>
            <div className="mt-7 flex justify-center gap-2.5">
              <Link href="/tours" className="btn btn-primary">
                Browse walkthroughs
              </Link>
              <Link href="/create-tour" className="btn btn-secondary">
                Create a walkthrough
              </Link>
            </div>
          </div>
        </div>
      </div>
    )
  }

  if (!jobId) return null

  if (error || !job) {
    return (
      <div className="min-h-screen bg-ivory">
        <Navigation />
        <div className="shell py-24">
          <div className="card mx-auto max-w-xl px-8 py-12 text-center">
            <p className="eyebrow">Engine unreachable</p>
            <h1 className="mt-2 text-3xl">Cannot reach the reconstruction engine</h1>
            <p className="mt-3 break-words text-sm leading-6 text-stone">
              {error || 'The reconstruction engine did not return this job.'}
            </p>
            <p className="mt-4 text-xs text-stone-light">
              The engine is expected at{' '}
              <code className="rounded bg-parchment px-2 py-1 font-mono text-[11px] text-ink">
                {RECONSTRUCTION_URL.replace(/^https?:\/\//, '')}
              </code>{' '}
              — start it and try again.
            </p>
            <button
              onClick={() => {
                setLoading(true)
                setReloadKey((value) => value + 1)
              }}
              className="btn btn-primary mt-6"
            >
              Retry
            </button>
          </div>
        </div>
      </div>
    )
  }

  if (job.status !== 'completed') {
    const failed = job.status === 'failed' || job.status === 'cancelled'
    const awaitingRoomTypes = job.status === 'awaiting_room_types'
    return (
      <div className="min-h-screen bg-ivory">
        <Navigation />
        <div className="shell max-w-3xl py-14">
          <div className="card p-7 sm:p-9">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="eyebrow">Reconstruction {job.status.replace(/_/g, ' ')}</p>
                <h1 className="mt-2 text-[30px] leading-tight">
                  {job.project_name || 'Property walkthrough'}
                </h1>
              </div>
              <span className={failed ? 'chip chip-danger' : 'chip chip-progress'}>
                {Math.round(job.progress)}%
              </span>
            </div>
            <p className="mt-2 text-sm text-stone">
              {job.stage_label || job.current_stage}
            </p>
            {!failed && (
              <div className="progress-track mt-5 !h-1.5 bg-ink/10">
                <div
                  className="progress-fill"
                  style={{ width: `${Math.max(3, Math.round(job.progress))}%` }}
                />
              </div>
            )}
            {job.error && (
              <div className="mt-5 rounded-[10px] border border-danger/30 bg-danger/[0.06] p-4 text-sm leading-6 text-danger">
                {job.error}
              </div>
            )}
            {failed && (
              <p className="mt-4 text-sm leading-6 text-stone">
                No viewer is shown for failed jobs because there is no validated scene to load.
                {job.retry_count ? ` This job has been retried ${job.retry_count} time(s).` : ''}
              </p>
            )}
            {actionError && <p className="mt-4 text-sm text-danger">{actionError}</p>}
            <div className="mt-6 flex flex-wrap gap-2.5">
              {failed ? (
                <button onClick={handleRetry} disabled={actionBusy} className="btn btn-primary">
                  {actionBusy ? 'Starting…' : 'Retry reconstruction'}
                </button>
              ) : (
                <button onClick={handleCancel} disabled={actionBusy} className="btn btn-secondary">
                  {actionBusy ? 'Requesting…' : 'Cancel reconstruction'}
                </button>
              )}
              <Link href="/tours" className="btn btn-secondary">
                All tours
              </Link>
              <a
                href={`${RECONSTRUCTION_URL}/reconstructions/${jobId}/logs`}
                target="_blank"
                rel="noreferrer"
                className="btn btn-ghost"
              >
                Engine logs
              </a>
            </div>
            {!failed && (
              <p className="mt-4 text-[11px] text-stone-light">
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
    <div className="flex min-h-dvh flex-col bg-ivory">
      <Navigation />

      <header className="border-b border-line bg-surface">
        <div className="flex flex-wrap items-center justify-between gap-4 px-4 py-5 sm:px-8 sm:py-6">
          <div className="min-w-0">
            <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-bronze">
              Walkthrough
            </p>
            <h1 className="mt-2.5 truncate font-serif text-[30px] uppercase leading-[1.05] tracking-[0.01em] text-ink sm:text-[38px]">
              {job.project_name || 'Property walkthrough'}
            </h1>
          </div>
          <button
            onClick={() => setShowDetails((value) => !value)}
            className="btn btn-secondary shrink-0"
            aria-expanded={showDetails}
          >
            {showDetails ? 'Hide details' : 'Details'}
          </button>
        </div>
      </header>

      {showDetails && (
        <div className="border-b border-line bg-parchment/60 px-4 py-4 sm:px-6">
          <div className="grid gap-4 lg:grid-cols-2">
            <div
              className="rounded-[12px] border border-line bg-surface p-4"
              data-testid="furnishing-controls"
            >
              <h2 className="text-sm font-semibold text-ink">Furnishing controls</h2>
              <p className="mt-1 text-xs leading-5 text-stone">
                A style change swaps furniture models and materials without moving walls, doors or
                room boundaries. Regenerate layout picks another deterministic arrangement for the
                same rooms and confirmed types.
              </p>
              <div className="mt-3 flex flex-wrap items-end gap-2">
                <label className="text-[10px] font-semibold uppercase tracking-[0.12em] text-stone">
                  <span className="mb-1 block">Style</span>
                  <select
                    value={selectedStyle}
                    onChange={(event) => setSelectedStyle(event.target.value)}
                    data-testid="furnishing-style"
                    className="rounded-[8px] border border-line-strong bg-surface px-3 py-2 text-sm font-normal normal-case tracking-normal text-ink focus:border-forest-soft focus:outline-none focus:ring-2 focus:ring-forest/15"
                  >
                    {FURNISHING_STYLES.map((option) => (
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
                  className="btn btn-primary btn-sm"
                >
                  {actionBusy ? 'Rebuilding…' : 'Apply style'}
                </button>
                <button
                  type="button"
                  onClick={() => applyFurnishing({ regenerate: true })}
                  disabled={actionBusy}
                  className="btn btn-secondary btn-sm"
                >
                  Regenerate layout
                </button>
                <button
                  type="button"
                  onClick={() => setShowRoomTypes((value) => !value)}
                  className="btn btn-secondary btn-sm"
                  aria-expanded={showRoomTypes}
                >
                  {showRoomTypes ? 'Hide room types' : 'Edit room types'}
                </button>
              </div>
              <p className="mt-2 text-[11px] leading-5 text-stone-light">
                Current: <span className="text-ink">{styleLabel(job.furnishing_style)}</span> style ·
                variant {job.furnishing_variant ?? 0} · {job.furniture_count ?? 0} furniture items.
                Rebuilding temporarily returns this job to the progress view.
              </p>
              {actionError && (
                <p className="mt-2 rounded-[8px] border border-danger/30 bg-danger/[0.06] p-2 text-xs text-danger">
                  {actionError}
                </p>
              )}
            </div>
            <div
              className="rounded-[12px] border border-line bg-surface p-4"
              data-testid="room-summary"
            >
              <h2 className="text-sm font-semibold text-ink">Room summary</h2>
              {roomTypes.length === 0 ? (
                <p className="mt-1 text-xs text-stone">
                  Room types are not stored on this job. Open room types below to review them.
                </p>
              ) : (
                <ul className="mt-2 grid grid-cols-1 gap-1 text-xs text-ink-soft sm:grid-cols-2">
                  {roomTypes.map((roomType) => (
                    <li
                      key={roomType.room_id}
                      className="flex justify-between gap-2 rounded-[6px] bg-parchment/60 px-2 py-1"
                    >
                      <span className="truncate">
                        {roomType.name || `Room ${roomType.room_id + 1}`}
                      </span>
                      <span className="capitalize text-stone-light">
                        {(roomType.room_type ?? 'unknown').replace(/_/g, ' ')}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-2 text-[11px] leading-5 text-stone-light">
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
        <div className="border-b border-line bg-parchment/60 px-4 py-4 text-sm text-ink-soft sm:px-6">
          <div className="mb-3 flex flex-wrap gap-2">
            <a
              href={`${RECONSTRUCTION_URL}/reconstructions/${jobId}/report`}
              target="_blank"
              rel="noreferrer"
              className="btn btn-secondary btn-sm"
            >
              Validation report
            </a>
            <Link href="/create-tour" className="btn btn-primary btn-sm">
              New reconstruction
            </Link>
          </div>
          <div className="grid gap-4 md:grid-cols-3">
            <dl className="space-y-1 text-[11px]">
              <div className="flex justify-between gap-3 border-b border-line/70 py-1">
                <dt className="text-stone">Property ID</dt>
                <dd className="truncate font-mono">{job.property_id || job.job_id}</dd>
              </div>
              <div className="flex justify-between gap-3 border-b border-line/70 py-1">
                <dt className="text-stone">Floor plan</dt>
                <dd className="truncate">{job.floor_plan_filename || 'uploaded plan'}</dd>
              </div>
              <div className="flex justify-between gap-3 border-b border-line/70 py-1">
                <dt className="text-stone">Requested floors</dt>
                <dd>{job.num_floors ?? 1} (ground floor modelled)</dd>
              </div>
              <div className="flex justify-between gap-3 border-b border-line/70 py-1">
                <dt className="text-stone">Room types</dt>
                <dd>{job.room_types_confirmed ? `${roomTypes.length} confirmed` : 'not confirmed'}</dd>
              </div>
              <div className="flex justify-between gap-3 border-b border-line/70 py-1">
                <dt className="text-stone">Furnishing</dt>
                <dd className="capitalize">
                  {job.furnishing_style ?? 'modern'} · variant {job.furnishing_variant ?? 0}
                </dd>
              </div>
              <div className="flex justify-between gap-3 border-b border-line/70 py-1">
                <dt className="text-stone">Furniture items</dt>
                <dd>{job.furniture_count ?? 0}</dd>
              </div>
              <div className="flex justify-between gap-3 border-b border-line/70 py-1">
                <dt className="text-stone">Rooms / doorways</dt>
                <dd>
                  {String(stats.rooms ?? '—')} / {String(stats.doors ?? '—')}
                </dd>
              </div>
              <div className="flex justify-between gap-3 border-b border-line/70 py-1">
                <dt className="text-stone">Geometry</dt>
                <dd>{job.geometry_valid ? 'Validated GLB' : 'Not validated'}</dd>
              </div>
              <div className="flex justify-between gap-3 py-1">
                <dt className="text-stone">Spawn walkable</dt>
                <dd>{checks.spawn_walkable ? 'Yes' : 'Not verified'}</dd>
              </div>
            </dl>
            <div className="md:col-span-2">
              {warnings.length > 0 && (
                <div className="mb-3 rounded-[10px] border border-warning/30 bg-warning/[0.07] p-3">
                  <p className="text-[10px] font-semibold uppercase tracking-[0.13em] text-warning">
                    Warnings
                  </p>
                  <ul className="mt-1 list-disc space-y-1 pl-4 text-[11px] leading-5 text-ink-soft">
                    {warnings.map((warning, index) => (
                      <li key={`warning-${index}`}>{warning}</li>
                    ))}
                  </ul>
                </div>
              )}
              <div className="rounded-[10px] border border-line bg-surface p-3">
                <p className="text-[10px] font-semibold uppercase tracking-[0.13em] text-stone">
                  Method and limitations
                </p>
                <p className="mt-1 text-[11px] leading-5 text-stone">
                  Layout comes from 2D wall vectorisation and room segmentation of the uploaded
                  floor plan. Room types are inferred from geometry and confirmed by you. Furniture
                  uses real 3D meshes placed deterministically inside each room with wall anchoring,
                  doorway and window clearances, and walkable-route checks. The floor plan is the
                  only input; no photographs or external image-generation service are used.
                </p>
                <ul className="mt-2 list-disc space-y-1 pl-4 text-[11px] leading-5 text-stone">
                  {limitations.map((limitation, index) => (
                    <li key={`limitation-${index}`}>{limitation}</li>
                  ))}
                </ul>
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="flex flex-1 items-center justify-center bg-[#002105] px-4 py-6 sm:px-8 sm:py-8">
        <div className="relative aspect-video w-full max-w-[min(1180px,max(560px,calc((100dvh_-_13rem)*16/9)))] overflow-hidden rounded-[18px] border border-line bg-forest-deep shadow-lift">
          <GameEngine
            key={`${jobId}-${job.furnishing_style ?? 'modern'}-${job.furnishing_variant ?? 0}`}
            jobId={jobId}
          />
        </div>
      </div>
    </div>
  )
}
