'use client'

import Navigation from '@/components/Navigation'
import {
  RECONSTRUCTION_URL,
  floorPlanPreviewUrl,
  listJobs,
  retryJob,
  type ReconstructionJob,
} from '@/lib/reconstruction'
import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'

function tone(job: ReconstructionJob): string {
  if (job.status === 'completed') return 'chip chip-success'
  if (job.status === 'failed' || job.status === 'cancelled') return 'chip chip-danger'
  return 'chip chip-progress'
}

function isActive(job: ReconstructionJob): boolean {
  return job.status !== 'completed' && job.status !== 'failed' && job.status !== 'cancelled'
}

function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Unknown date'
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function Thumb({ job }: { job: ReconstructionJob }) {
  const [failed, setFailed] = useState(false)
  return (
    <div className="relative h-[88px] w-[116px] shrink-0 overflow-hidden rounded-[10px] border border-line bg-parchment">
      <div className="blueprint-grid absolute inset-0 opacity-60" aria-hidden="true" />
      {failed ? (
        <div className="absolute inset-0 grid place-items-center text-line-strong">
          <svg viewBox="0 0 48 48" className="h-8 w-8" aria-hidden="true">
            <path
              d="M7 9h34v30H7zM7 21h12M19 21v18M33 9v10M33 19h8"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
            />
          </svg>
        </div>
      ) : (
        // Engine-served previews are dynamic local URLs; next/image cannot
        // optimise them without a configured loader.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={floorPlanPreviewUrl(job.job_id)}
          alt={`Floor plan for ${job.project_name || 'walkthrough'}`}
          loading="lazy"
          onError={() => setFailed(true)}
          className="absolute inset-0 h-full w-full object-contain p-1.5 mix-blend-multiply"
        />
      )}
    </div>
  )
}

export default function ToursPage() {
  const [jobs, setJobs] = useState<ReconstructionJob[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reload, setReload] = useState(0)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [readyOnly, setReadyOnly] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    listJobs(controller.signal)
      .then((data) => {
        if (controller.signal.aborted) return
        setJobs(data)
        setError(null)
      })
      .catch((caught) => {
        if (controller.signal.aborted) return
        setError(caught instanceof Error ? caught.message : String(caught))
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [reload])

  const handleRetry = async (jobId: string) => {
    setBusyId(jobId)
    setError(null)
    try {
      await retryJob(jobId)
      setReload((value) => value + 1)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusyId(null)
    }
  }

  const visible = useMemo(
    () => (readyOnly ? jobs.filter((job) => job.status === 'completed') : jobs),
    [jobs, readyOnly],
  )

  return (
    <div className="min-h-screen bg-ivory">
      <Navigation />

      <main className="shell pb-20 pt-10 sm:pt-12">
        <div className="flex flex-wrap items-end justify-between gap-5">
          <div className="animate-rise">
            <p className="eyebrow">Gallery</p>
            <h1 className="mt-2 text-[34px] leading-tight sm:text-[42px]">All walkthroughs</h1>
            <p className="mt-3 max-w-xl text-sm leading-6 text-stone">
              Every job stored by the reconstruction engine — completed walkthroughs, live pipeline
              runs, and the failed attempts with their diagnostics intact.
            </p>
          </div>
          <div className="flex flex-wrap gap-2.5">
            <button
              type="button"
              onClick={() => {
                setLoading(true)
                setReload((value) => value + 1)
              }}
              className="btn btn-secondary"
            >
              Refresh
            </button>
            <Link href="/create-tour" className="btn btn-primary">
              New walkthrough
            </Link>
          </div>
        </div>

        {error && (
          <div className="mt-6 rounded-[12px] border border-danger/30 bg-danger/[0.06] px-4 py-3.5 text-sm text-danger">
            <p className="font-medium">Cannot reach the reconstruction engine</p>
            <p className="mt-1 break-words text-xs text-danger/80">{error}</p>
            <p className="mt-1 text-[11px] text-stone">Engine: {RECONSTRUCTION_URL}</p>
          </div>
        )}

        {!loading && jobs.length > 0 && (
          <div className="mt-8 flex flex-wrap items-center justify-between gap-3 border-b border-line pb-3">
            <p className="text-xs text-stone">
              {jobs.length} job{jobs.length === 1 ? '' : 's'} ·{' '}
              {jobs.filter((job) => job.status === 'completed').length} ready
            </p>
            <label className="flex cursor-pointer items-center gap-2 text-xs text-stone">
              <input
                type="checkbox"
                checked={readyOnly}
                onChange={(event) => setReadyOnly(event.target.checked)}
                className="h-3.5 w-3.5 rounded border-line-strong accent-forest"
              />
              Show completed only
            </label>
          </div>
        )}

        <div className="mt-6">
          {loading ? (
            <div className="space-y-3" aria-busy="true" aria-label="Loading walkthroughs">
              {[0, 1, 2, 3].map((index) => (
                <div key={index} className="card flex items-center gap-4 p-4">
                  <div className="h-[88px] w-[116px] animate-pulse rounded-[10px] bg-parchment" />
                  <div className="flex-1 space-y-2.5">
                    <div className="h-4 w-1/3 animate-pulse rounded bg-parchment" />
                    <div className="h-3 w-1/2 animate-pulse rounded bg-parchment/80" />
                    <div className="h-3 w-1/4 animate-pulse rounded bg-parchment/60" />
                  </div>
                </div>
              ))}
            </div>
          ) : visible.length === 0 ? (
            <div className="card animate-rise px-6 py-14 text-center">
              <div className="mx-auto grid h-14 w-14 place-items-center rounded-xl border border-line-strong bg-surface">
                <svg viewBox="0 0 48 48" className="h-7 w-7 text-forest" aria-hidden="true">
                  <path
                    d="M6 10h36v28H6zM6 22h14M20 22v16M32 10v12M32 22h10"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.4"
                    strokeLinejoin="round"
                  />
                </svg>
              </div>
              <h2 className="mt-5 text-2xl">
                {jobs.length === 0 ? 'No reconstructions yet' : 'No completed walkthroughs yet'}
              </h2>
              <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-stone">
                {jobs.length === 0
                  ? 'Upload a floor plan to create your first automatically furnished walkthrough.'
                  : 'Jobs in progress will appear here as soon as their geometry and navigation checks pass.'}
              </p>
              {jobs.length === 0 ? (
                <Link href="/create-tour" className="btn btn-primary mt-6">
                  Create a walkthrough
                </Link>
              ) : (
                <button
                  type="button"
                  onClick={() => setReadyOnly(false)}
                  className="btn btn-secondary mt-6"
                >
                  Show all jobs
                </button>
              )}
            </div>
          ) : (
            <ul className="space-y-3">
              {visible.map((job) => (
                <li
                  key={job.job_id}
                  className="card card-hover animate-fade flex flex-col gap-4 p-4 sm:flex-row sm:items-center"
                >
                  <Link href={`/tour/${job.job_id}`} className="shrink-0" aria-label={`Open ${job.project_name || 'walkthrough'}`}>
                    <Thumb job={job} />
                  </Link>

                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <Link
                        href={`/tour/${job.job_id}`}
                        className="truncate font-serif text-[19px] leading-snug text-ink hover:text-forest-soft"
                      >
                        {job.project_name || 'Untitled reconstruction'}
                      </Link>
                      <span className={`${tone(job)} capitalize`}>{job.status.replace(/_/g, ' ')}</span>
                    </div>
                    <p className="mt-1.5 text-xs text-stone">
                      {formatDate(job.created_at)}
                      <span className="mx-1.5 text-line-strong">·</span>
                      {job.room_count ?? 0} rooms
                      <span className="mx-1.5 text-line-strong">·</span>
                      {job.furniture_count ?? 0} furniture
                      <span className="mx-1.5 text-line-strong">·</span>
                      {job.furnishing_style ?? 'modern'} style
                    </p>
                    <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-stone-light">
                      <span>{job.scale_verified ? 'Measured scale' : 'Estimated scale'}</span>
                      <span>{job.geometry_valid ? 'Geometry validated' : 'Geometry not validated'}</span>
                      <span>{job.navigation_available ? 'Fully navigable' : 'Partial navigation'}</span>
                      {job.retry_count ? <span>{job.retry_count} retries</span> : null}
                    </div>

                    {isActive(job) && (
                      <div className="mt-3 max-w-md">
                        <div className="progress-track">
                          <div
                            className="progress-fill"
                            style={{ width: `${Math.max(3, Math.round(job.progress))}%` }}
                          />
                        </div>
                        <p className="mt-1.5 text-[11px] text-stone">
                          {job.stage_label || job.current_stage} · {Math.round(job.progress)}%
                        </p>
                      </div>
                    )}

                    {job.error && (
                      <p className="mt-2 line-clamp-2 break-words text-[11px] text-danger">{job.error}</p>
                    )}
                  </div>

                  <div className="flex flex-wrap items-center gap-2 sm:justify-end">
                    <Link
                      href={`/tour/${job.job_id}`}
                      className={`btn btn-sm ${job.status === 'completed' ? 'btn-primary' : 'btn-secondary'}`}
                    >
                      {job.status === 'completed' ? 'Open' : 'Open status'}
                    </Link>
                    {(job.status === 'failed' || job.status === 'cancelled') && (
                      <button
                        type="button"
                        onClick={() => handleRetry(job.job_id)}
                        disabled={busyId === job.job_id}
                        className="btn btn-secondary btn-sm"
                      >
                        {busyId === job.job_id ? 'Starting…' : 'Retry'}
                      </button>
                    )}
                    <a
                      href={`${RECONSTRUCTION_URL}/reconstructions/${job.job_id}/report`}
                      target="_blank"
                      rel="noreferrer"
                      className="btn btn-ghost btn-sm"
                    >
                      Report
                    </a>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </main>
    </div>
  )
}