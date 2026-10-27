'use client'

import Navigation from '@/components/Navigation'
import { createClient } from '@/lib/supabase/client'
import { useIsClient } from '@/lib/useIsClient'
import {
  RECONSTRUCTION_URL,
  floorPlanPreviewUrl,
  listJobs,
  retryJob,
  type ReconstructionJob,
} from '@/lib/reconstruction'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useEffect, useMemo, useState } from 'react'

type Filter = 'all' | 'ready' | 'progress' | 'attention'

function isReady(job: ReconstructionJob): boolean {
  return job.status === 'completed'
}

function needsAttention(job: ReconstructionJob): boolean {
  return job.status === 'failed' || job.status === 'cancelled'
}

function isActive(job: ReconstructionJob): boolean {
  return !isReady(job) && !needsAttention(job)
}

function statusTone(job: ReconstructionJob): string {
  if (isReady(job)) return 'chip chip-success'
  if (needsAttention(job)) return 'chip chip-danger'
  return 'chip chip-progress'
}

function statusLabel(job: ReconstructionJob): string {
  return job.status.replace(/_/g, ' ')
}

function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Unknown date'
  return date.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

function PlanThumbnail({ job }: { job: ReconstructionJob }) {
  const [failed, setFailed] = useState(false)
  const src = floorPlanPreviewUrl(job.job_id)

  return (
    <div className="relative aspect-[4/3] overflow-hidden rounded-t-[13px] border-b border-line bg-parchment">
      <div className="blueprint-grid absolute inset-0 opacity-70" aria-hidden="true" />
      {failed ? (
        <div className="absolute inset-0 grid place-items-center">
          <svg viewBox="0 0 64 64" className="h-14 w-14 text-line-strong" aria-hidden="true">
            <path
              d="M10 12h44v40H10zM10 28h16M26 28v24M40 12v14M40 26h14M26 44h14"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
            />
          </svg>
        </div>
      ) : (
        // Engine-served previews are dynamic local URLs; next/image cannot
        // optimise them without a configured loader.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={src}
          alt={`Floor plan for ${job.project_name || 'reconstruction'}`}
          loading="lazy"
          onError={() => setFailed(true)}
          className="absolute inset-0 h-full w-full object-contain p-3 mix-blend-multiply"
        />
      )}
      <span className={`absolute left-3 top-3 ${statusTone(job)} shadow-sm backdrop-blur`}>
        {isActive(job) ? (
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-bronze" aria-hidden="true" />
        ) : null}
        <span className="capitalize">{statusLabel(job)}</span>
      </span>
      {isActive(job) && (
        <span className="absolute bottom-3 left-3 right-3 rounded-full bg-surface/90 px-2.5 py-1 text-[10px] font-medium text-stone backdrop-blur">
          {job.stage_label || job.current_stage} · {Math.round(job.progress)}%
        </span>
      )}
    </div>
  )
}

function StatCell({
  label,
  value,
  hint,
  accent,
}: {
  label: string
  value: number
  hint: string
  accent?: boolean
}) {
  return (
    <div className="flex flex-col justify-between gap-6 px-5 py-5 sm:px-6">
      <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-stone">{label}</p>
      <div>
        <p className={`font-serif text-[34px] leading-none ${accent ? 'text-bronze' : 'text-ink'}`}>
          {value}
        </p>
        <p className="mt-1.5 text-xs text-stone-light">{hint}</p>
      </div>
    </div>
  )
}

function EmptyState() {
  return (
    <div className="card animate-rise relative overflow-hidden px-6 py-14 text-center sm:px-10">
      <div className="blueprint-grid pointer-events-none absolute inset-0 opacity-60" aria-hidden="true" />
      <div className="relative mx-auto flex max-w-md flex-col items-center">
        <div className="grid h-16 w-16 place-items-center rounded-2xl border border-line-strong bg-surface shadow-card">
          <svg viewBox="0 0 48 48" className="h-8 w-8 text-forest" aria-hidden="true">
            <path
              d="M6 10h36v28H6zM6 22h14M20 22v16M32 10v12M32 22h10M20 32h12"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinejoin="round"
            />
          </svg>
        </div>
        <h2 className="mt-6 text-2xl">No walkthroughs yet</h2>
        <p className="mt-2 text-sm leading-6 text-stone">
          Upload a floor plan and RealEstAI will detect the rooms, furnish them in a chosen style,
          validate the geometry, then open a first-person walkthrough in your browser.
        </p>
        <div className="mt-7 flex flex-wrap justify-center gap-2.5">
          <Link href="/create-tour" className="btn btn-primary">
            Create your first walkthrough
          </Link>
          <Link href="/tours" className="btn btn-secondary">
            Browse all jobs
          </Link>
        </div>
        <p className="mt-5 text-[11px] text-stone-light">
          Runs against your local reconstruction engine at {RECONSTRUCTION_URL.replace(/^https?:\/\//, '')}
        </p>
      </div>
    </div>
  )
}

function LoadingState() {
  return (
    <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3" aria-busy="true" aria-label="Loading walkthroughs">
      {[0, 1, 2].map((index) => (
        <div key={index} className="card overflow-hidden">
          <div className="aspect-[4/3] animate-pulse bg-parchment" />
          <div className="space-y-3 p-5">
            <div className="h-4 w-2/3 animate-pulse rounded bg-parchment" />
            <div className="h-3 w-1/2 animate-pulse rounded bg-parchment/80" />
            <div className="h-8 w-full animate-pulse rounded bg-parchment/60" />
          </div>
        </div>
      ))}
    </div>
  )
}

export default function DashboardPage() {
  const router = useRouter()
  const [jobs, setJobs] = useState<ReconstructionJob[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  const [reloadTick, setReloadTick] = useState(0)
  const [busyId, setBusyId] = useState<string | null>(null)
  const isClient = useIsClient()
  const supabase = isClient ? createClient() : null

  useEffect(() => {
    if (!isClient) return
    const hasUrl =
      process.env.NEXT_PUBLIC_SUPABASE_URL &&
      process.env.NEXT_PUBLIC_SUPABASE_URL !== 'https://placeholder.supabase.co'
    const hasKey =
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY &&
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY !== 'placeholder_anon_key'
    if (!hasUrl || !hasKey || !supabase) return
    supabase.auth
      .getUser()
      .then(({ data }) => {
        if (!data.user) router.push('/auth/signin')
      })
      .catch(() => undefined)
  }, [isClient, router, supabase])

  useEffect(() => {
    const controller = new AbortController()
    let cancelled = false

    const load = async () => {
      try {
        const data = await listJobs(controller.signal)
        if (cancelled) return
        setJobs(data)
        setError(null)
      } catch (caught) {
        if (cancelled || controller.signal.aborted) return
        setError(caught instanceof Error ? caught.message : String(caught))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void load()
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void load()
    }, 8000)

    return () => {
      cancelled = true
      controller.abort()
      window.clearInterval(timer)
    }
  }, [reloadTick])

  const counts = useMemo(
    () => ({
      all: jobs.length,
      ready: jobs.filter(isReady).length,
      progress: jobs.filter(isActive).length,
      attention: jobs.filter(needsAttention).length,
    }),
    [jobs],
  )

  const filtered = useMemo(() => {
    if (filter === 'ready') return jobs.filter(isReady)
    if (filter === 'progress') return jobs.filter(isActive)
    if (filter === 'attention') return jobs.filter(needsAttention)
    return jobs
  }, [filter, jobs])

  const handleRetry = async (jobId: string) => {
    setBusyId(jobId)
    setError(null)
    try {
      await retryJob(jobId)
      setReloadTick((value) => value + 1)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusyId(null)
    }
  }

  const filters: { id: Filter; label: string; count: number }[] = [
    { id: 'all', label: 'All', count: counts.all },
    { id: 'ready', label: 'Ready', count: counts.ready },
    { id: 'progress', label: 'In progress', count: counts.progress },
    { id: 'attention', label: 'Needs attention', count: counts.attention },
  ]

  return (
    <div className="min-h-screen bg-ivory">
      <Navigation />

      <main className="shell pb-20 pt-10 sm:pt-12">
        <div className="flex flex-wrap items-end justify-between gap-5">
          <div className="animate-rise">
            <p className="eyebrow">Workspace</p>
            <h1 className="mt-2 text-[34px] leading-tight sm:text-[42px]">Your walkthroughs</h1>
            <p className="mt-3 max-w-xl text-sm leading-6 text-stone">
              Every reconstruction job handled by your local engine, with its real status, floor
              plan, room count, and furnishing. Nothing here is sampled or pre-filled.
            </p>
          </div>
          <div className="flex flex-wrap gap-2.5">
            <button
              type="button"
              onClick={() => {
                setLoading(true)
                setReloadTick((value) => value + 1)
              }}
              className="btn btn-secondary"
            >
              <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" aria-hidden="true">
                <path
                  d="M13 8a5 5 0 1 1-1.6-3.7M13 3v3h-3"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.4"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
              Refresh
            </button>
            <Link href="/create-tour" className="btn btn-primary">
              <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" aria-hidden="true">
                <path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              </svg>
              New walkthrough
            </Link>
          </div>
        </div>

        <div className="card mt-8 grid grid-cols-2 divide-line sm:grid-cols-4 sm:divide-x">
          <StatCell label="Total jobs" value={counts.all} hint="Stored by the engine" />
          <StatCell label="Ready" value={counts.ready} hint="Validated walkthroughs" />
          <StatCell label="In progress" value={counts.progress} hint="Live pipeline stages" accent={counts.progress > 0} />
          <StatCell label="Needs attention" value={counts.attention} hint="Failed or cancelled" />
        </div>

        <div className="mt-9 flex flex-wrap items-center justify-between gap-3 border-b border-line pb-3">
          <div className="flex flex-wrap gap-1" role="tablist" aria-label="Filter walkthroughs">
            {filters.map((item) => (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={filter === item.id}
                onClick={() => setFilter(item.id)}
                className={`rounded-full px-3.5 py-1.5 text-xs font-medium transition-colors ${
                  filter === item.id
                    ? 'bg-forest text-ivory'
                    : 'text-stone hover:bg-ink/[0.05] hover:text-ink'
                }`}
              >
                {item.label}
                <span className={`ml-1.5 ${filter === item.id ? 'text-ivory/60' : 'text-stone-light'}`}>
                  {item.count}
                </span>
              </button>
            ))}
          </div>
          <p className="text-[11px] text-stone-light">
            Auto-refreshes every 8 seconds while this page is open
          </p>
        </div>

        {error && (
          <div className="mt-6 flex flex-wrap items-start justify-between gap-4 rounded-[12px] border border-danger/30 bg-danger/[0.06] px-4 py-3.5 text-sm text-danger">
            <div>
              <p className="font-medium">Cannot reach the reconstruction engine</p>
              <p className="mt-1 break-words text-xs text-danger/80">{error}</p>
              <p className="mt-1 text-[11px] text-stone">
                Engine: {RECONSTRUCTION_URL} — start it with{' '}
                <code className="rounded bg-surface px-1.5 py-0.5 text-[11px] text-ink">
                  venv/bin/uvicorn main:app --port 8002
                </code>
              </p>
            </div>
            <button
              type="button"
              onClick={() => {
                setLoading(true)
                setReloadTick((value) => value + 1)
              }}
              className="btn btn-secondary btn-sm"
            >
              Retry
            </button>
          </div>
        )}

        <div className="mt-6">
          {loading ? (
            <LoadingState />
          ) : filtered.length === 0 ? (
            counts.all === 0 ? (
              <EmptyState />
            ) : (
              <div className="card px-6 py-12 text-center">
                <p className="text-sm text-stone">No walkthroughs match this filter.</p>
                <button
                  type="button"
                  onClick={() => setFilter('all')}
                  className="btn btn-secondary btn-sm mt-4"
                >
                  Show all
                </button>
              </div>
            )
          ) : (
            <ul className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
              {filtered.map((job, index) => (
                <li
                  key={job.job_id}
                  className={`card card-hover animate-rise overflow-hidden ${
                    index > 2 ? '' : index === 1 ? 'delay-1' : index === 2 ? 'delay-2' : ''
                  }`}
                >
                  <Link href={`/tour/${job.job_id}`} className="block" aria-label={`Open ${job.project_name || 'walkthrough'}`}>
                    <PlanThumbnail job={job} />
                  </Link>
                  <div className="flex flex-col gap-3 p-5">
                    <div>
                      <h2 className="truncate text-lg leading-snug text-ink">
                        {job.project_name || 'Untitled reconstruction'}
                      </h2>
                      <p className="mt-1 text-xs text-stone">
                        {formatDate(job.created_at)}
                        <span className="mx-1.5 text-line-strong">·</span>
                        {job.room_count != null ? `${job.room_count} rooms` : 'rooms pending'}
                        {isReady(job) && job.furniture_count != null
                          ? ` · ${job.furniture_count} furniture`
                          : ''}
                      </p>
                    </div>

                    {isActive(job) && (
                      <div>
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

                    {job.error && needsAttention(job) && (
                      <p className="line-clamp-2 break-words rounded-md bg-danger/[0.05] px-2.5 py-1.5 text-[11px] text-danger">
                        {job.error}
                      </p>
                    )}

                    <div className="mt-auto flex flex-wrap items-center gap-2 pt-1">
                      <Link
                        href={`/tour/${job.job_id}`}
                        className={`btn btn-sm ${isReady(job) ? 'btn-primary' : 'btn-secondary'}`}
                      >
                        {isReady(job) ? 'Open walkthrough' : isActive(job) ? 'View progress' : 'Review job'}
                      </Link>
                      <a
                        href={`${RECONSTRUCTION_URL}/reconstructions/${job.job_id}/report`}
                        target="_blank"
                        rel="noreferrer"
                        className="btn btn-ghost btn-sm"
                      >
                        Report
                      </a>
                      {needsAttention(job) && (
                        <button
                          type="button"
                          onClick={() => handleRetry(job.job_id)}
                          disabled={busyId === job.job_id}
                          className="btn btn-secondary btn-sm ml-auto"
                        >
                          {busyId === job.job_id ? 'Starting…' : 'Retry'}
                        </button>
                      )}
                    </div>
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