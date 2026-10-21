'use client'

import { Suspense, useCallback, useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import GameEngine from '@/components/GameEngine'
import Navigation from '@/components/Navigation'
import {
  RECONSTRUCTION_URL,
  cancelJob,
  fetchJob,
  retryJob,
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
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionBusy, setActionBusy] = useState(false)
  const [showDetails, setShowDetails] = useState(false)
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
  const photogrammetry = diagnostics?.photogrammetry as Record<string, unknown> | undefined
  const textureSummary = (
    diagnostics?.geometry as Record<string, unknown> | undefined
  )?.textures as Record<string, unknown> | undefined
  const sfmStatus = typeof photogrammetry?.status === 'string' ? photogrammetry.status : null
  const sfmRegistered =
    typeof photogrammetry?.registered_images === 'number'
      ? photogrammetry.registered_images
      : null
  const sfmInputs =
    typeof photogrammetry?.input_images === 'number' ? photogrammetry.input_images : null
  const textureCount =
    typeof textureSummary?.count === 'number' ? textureSummary.count : null

  return (
    <div className="flex min-h-screen flex-col bg-gray-900">
      <Navigation />

      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-800 bg-gray-900/95 px-4 py-3 sm:px-6">
        <div className="min-w-0">
          <h1 className="truncate text-lg font-semibold text-white">
            {job.project_name || 'Property walkthrough'}
          </h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-gray-400">
            <span>{job.photo_count ?? 0} photos</span>
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
                <dt className="text-gray-500">Photogrammetry</dt>
                <dd className="truncate">
                  {sfmStatus
                    ? `${sfmStatus.replace(/_/g, ' ')}${
                        sfmRegistered !== null && sfmInputs !== null
                          ? ` · ${sfmRegistered}/${sfmInputs} photos`
                          : ''
                      }`
                    : 'not run'}
                </dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-gray-500">Photo textures</dt>
                <dd>{textureCount !== null ? `${textureCount} sets` : 'none'}</dd>
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
                  Layout comes from 2D wall vectorisation and room segmentation. Wall and floor
                  materials are real crops from the uploaded photographs, embedded in the GLB.
                  COLMAP estimates camera poses from the same photographs when they overlap; dense
                  image-derived geometry requires a CUDA COLMAP build. Surfaces not visible in any
                  photograph are inferred and listed below.
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
        <GameEngine jobId={jobId} />
      </div>
    </div>
  )
}
