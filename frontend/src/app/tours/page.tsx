'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import Navigation from '@/components/Navigation'
import {
  RECONSTRUCTION_URL,
  listJobs,
  retryJob,
  type ReconstructionJob,
} from '@/lib/reconstruction'

function statusClasses(status: string): string {
  if (status === 'completed') return 'border-emerald-500/40 bg-emerald-950/30 text-emerald-300'
  if (status === 'failed') return 'border-rose-500/40 bg-rose-950/30 text-rose-300'
  if (status === 'cancelled') return 'border-amber-500/40 bg-amber-950/30 text-amber-300'
  return 'border-blue-500/40 bg-blue-950/30 text-blue-300'
}

export default function ToursPage() {
  const [jobs, setJobs] = useState<ReconstructionJob[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reload, setReload] = useState(0)
  const [busyId, setBusyId] = useState<string | null>(null)

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

  return (
    <div className="min-h-screen bg-gray-900">
      <Navigation />
      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold text-white">Tours</h1>
            <p className="mt-1 text-gray-400">
              Every reconstruction job stored by the local engine, including failed attempts.
            </p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => {
                setLoading(true)
                setReload((value) => value + 1)
              }}
              className="rounded-lg border border-gray-700 px-4 py-2 text-sm font-medium text-gray-200 hover:bg-gray-800"
            >
              Refresh
            </button>
            <Link
              href="/create-tour"
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500"
            >
              New reconstruction
            </Link>
          </div>
        </div>

        {error && (
          <div className="mb-6 rounded-lg border border-rose-500/40 bg-rose-950/30 p-4 text-sm text-rose-200">
            <p>{error}</p>
            <p className="mt-2 text-xs text-rose-300/80">
              Reconstruction engine: {RECONSTRUCTION_URL}
            </p>
          </div>
        )}

        {loading ? (
          <div className="rounded-lg bg-gray-800 p-8 text-gray-400">Loading tours...</div>
        ) : jobs.length === 0 ? (
          <div className="rounded-lg bg-gray-800 p-10 text-center">
            <h2 className="text-lg font-semibold text-white">No reconstructions yet</h2>
            <p className="mt-2 text-sm text-gray-400">
              Upload a floor plan to create your first automatically furnished walkthrough.
            </p>
            <Link
              href="/create-tour"
              className="mt-5 inline-block rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500"
            >
              Create a tour
            </Link>
          </div>
        ) : (
          <ul className="grid gap-4 md:grid-cols-2">
            {jobs.map((job) => (
              <li key={job.job_id} className="rounded-lg border border-gray-800 bg-gray-800 p-5">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h2 className="truncate text-lg font-semibold text-white">
                      {job.project_name || 'Untitled reconstruction'}
                    </h2>
                    <p className="mt-1 text-xs text-gray-500">
                      {new Date(job.created_at).toLocaleString()} · {job.room_count ?? 0} rooms ·{' '}
                      {job.furniture_count ?? 0} furniture items
                    </p>
                  </div>
                  <span
                    className={`rounded-full border px-2.5 py-0.5 text-xs font-medium capitalize ${statusClasses(job.status)}`}
                  >
                    {job.status.replace(/_/g, ' ')}
                  </span>
                </div>

                <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-400">
                  <span>Style: {job.furnishing_style ?? 'modern'}</span>
                  <span>Scale: {job.scale_verified ? 'measured' : 'estimated'}</span>
                  <span>Geometry: {job.geometry_valid ? 'validated' : job.status === 'completed' ? 'unknown' : 'not validated'}</span>
                  <span>Navigation: {job.navigation_available ? 'full' : 'partial/none'}</span>
                  {job.retry_count ? <span>Retries: {job.retry_count}</span> : null}
                </div>

                {job.status !== 'completed' && job.status !== 'failed' && job.status !== 'cancelled' && (
                  <div className="mt-3">
                    <div className="h-1.5 overflow-hidden rounded-full bg-gray-700">
                      <div
                        className="h-full rounded-full bg-blue-500"
                        style={{ width: `${Math.max(3, Math.round(job.progress))}%` }}
                      />
                    </div>
                    <p className="mt-1 text-xs text-gray-500">
                      {job.stage_label || job.current_stage} · {Math.round(job.progress)}%
                    </p>
                  </div>
                )}

                {job.error && (
                  <p className="mt-3 line-clamp-2 break-words text-xs text-rose-300">{job.error}</p>
                )}

                <div className="mt-4 flex flex-wrap gap-2">
                  <Link
                    href={`/tour/${job.job_id}`}
                    className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-500"
                  >
                    {job.status === 'completed' ? 'Open walkthrough' : 'Open status'}
                  </Link>
                  {(job.status === 'failed' || job.status === 'cancelled') && (
                    <button
                      onClick={() => handleRetry(job.job_id)}
                      disabled={busyId === job.job_id}
                      className="rounded-lg border border-gray-600 px-3 py-1.5 text-xs font-medium text-gray-200 hover:bg-gray-700 disabled:opacity-50"
                    >
                      {busyId === job.job_id ? 'Starting...' : 'Retry'}
                    </button>
                  )}
                  <a
                    href={`${RECONSTRUCTION_URL}/reconstructions/${job.job_id}/report`}
                    target="_blank"
                    rel="noreferrer"
                    className="rounded-lg border border-gray-600 px-3 py-1.5 text-xs font-medium text-gray-200 hover:bg-gray-700"
                  >
                    Report
                  </a>
                </div>
              </li>
            ))}
          </ul>
        )}
      </main>
    </div>
  )
}
