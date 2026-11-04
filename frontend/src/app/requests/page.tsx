'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import Navigation from '@/components/Navigation'
import RoomTypeReview from '@/components/RoomTypeReview'
import {
  fetchJob,
  type ReconstructionJob,
} from '@/lib/reconstruction'
import {
  deliverIntakeRequest,
  fetchIntakeRequest,
  fetchIntakeRequests,
  ignoreIntakeRequest,
  intakeFileUrl,
  spanValue,
  startIntakeBuild,
  type IntakeFile,
  type IntakeRequestDetail,
  type IntakeRequestSummary,
} from '@/lib/intake'

const STYLES = [
  { value: 'modern', label: 'Modern' },
  { value: 'minimalist', label: 'Minimalist' },
  { value: 'scandinavian', label: 'Scandinavian' },
  { value: 'warm_luxury', label: 'Warm luxury' },
] as const

const TERMINAL_JOB_STATUSES = new Set(['completed', 'failed', 'cancelled'])

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function receivedLabel(iso: string): string {
  const stamp = new Date(iso).getTime()
  if (!Number.isFinite(stamp)) return iso
  const minutes = Math.max(0, Math.round((Date.now() - stamp) / 60000))
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  return new Date(stamp).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

function StatusChip({ status }: { status: string }) {
  const tone =
    status === 'new'
      ? 'chip-warning'
      : status === 'ignored'
        ? 'chip-neutral'
        : status === 'delivered'
          ? 'chip-success'
          : 'chip-neutral'
  const label =
    status === 'new'
      ? 'Awaiting production'
      : status === 'building'
        ? 'In production'
        : status === 'delivered'
          ? 'Delivered'
          : status === 'ignored'
            ? 'Archived'
            : status
  return <span className={`chip ${tone}`}>{label}</span>
}

function formatBytes(size: number): string {
  if (!size) return '0 KB'
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

function FileRow({ file }: { file: IntakeFile }) {
  return (
    <li className="flex items-start justify-between gap-4 border-b border-line/70 py-2.5 last:border-0">
      <div className="min-w-0">
        <p className="truncate text-[13px] text-ink">{file.name}</p>
        <p className="mt-0.5 text-[11px] text-stone-light">
          {file.category} · {formatBytes(file.size_bytes)}
          {file.kind === 'plan' ? ' · plan' : ''}
        </p>
        {file.error ? <p className="mt-1 text-[11px] text-danger">{file.error}</p> : null}
      </div>
      {file.stored ? (
        <a
          href={intakeFileUrl(file)}
          target="_blank"
          rel="noreferrer"
          className="shrink-0 text-[11px] font-semibold uppercase tracking-[0.12em] text-forest-soft hover:text-forest"
        >
          Open
        </a>
      ) : (
        <span className="shrink-0 text-[11px] text-stone-light">Not stored</span>
      )}
    </li>
  )
}

function RequestDetail({
  requestId,
  onChanged,
}: {
  requestId: string
  onChanged: () => void
}) {
  const [detail, setDetail] = useState<IntakeRequestDetail | null>(null)
  const [job, setJob] = useState<ReconstructionJob | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<'' | 'build' | 'archive' | 'send'>('')
  const [width, setWidth] = useState('')
  const [length, setLength] = useState('')
  const [style, setStyle] = useState<string>('modern')
  const [floors, setFloors] = useState(1)
  const [reloadTick, setReloadTick] = useState(0)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const next = await fetchIntakeRequest(requestId)
        if (cancelled) return
        setDetail(next)
        setError(null)
        const project = next.project ?? {}
        setWidth((current) => current || spanValue(project.planWidthMeters))
        setLength((current) => current || spanValue(project.planLengthMeters))
        if (
          project.furnishingStyle &&
          STYLES.some((option) => option.value === project.furnishingStyle)
        ) {
          setStyle((current) => (current === 'modern' ? project.furnishingStyle as string : current))
        }
      } catch (caught) {
        if (!cancelled) setError(messageOf(caught))
      }
    }

    void load()
    return () => {
      cancelled = true
    }
  }, [requestId, reloadTick])

  const jobId = detail?.build?.job_id ?? null
  useEffect(() => {
    if (!jobId) return
    let cancelled = false

    const poll = async () => {
      try {
        const next = await fetchJob(jobId)
        if (!cancelled) setJob(next)
      } catch {
        // Keep the last known progress when the engine is briefly unreachable.
      }
    }

    void poll()
    const timer = window.setInterval(poll, 2000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [jobId])

  const activeJob = job?.job_id === jobId ? job : null

  const refresh = () => setReloadTick((tick) => tick + 1)

  const onBuild = async () => {
    setBusy('build')
    setError(null)
    try {
      await startIntakeBuild(requestId, {
        style,
        num_floors: floors,
        known_width: width ? Number(width) : null,
        known_length: length ? Number(length) : null,
      })
      refresh()
      onChanged()
    } catch (caught) {
      setError(messageOf(caught))
    } finally {
      setBusy('')
    }
  }

  const onIgnore = async () => {
    setBusy('archive')
    try {
      await ignoreIntakeRequest(requestId)
      refresh()
      onChanged()
    } catch (caught) {
      setError(messageOf(caught))
    } finally {
      setBusy('')
    }
  }

  const onDeliver = async () => {
    setBusy('send')
    setError(null)
    try {
      await deliverIntakeRequest(requestId)
      refresh()
      onChanged()
    } catch (caught) {
      setError(messageOf(caught))
    } finally {
      setBusy('')
    }
  }

  if (!detail) {
    return (
      <div className="mt-5 border-t border-line pt-5 text-sm text-stone">
        {error ?? 'Loading submission…'}
      </div>
    )
  }

  const plans = detail.files.filter((file) => file.kind === 'plan' && file.stored)
  const others = detail.files.filter((file) => file.kind !== 'plan')
  const plan = plans[0]
  const awaitingTypes = activeJob?.status === 'awaiting_room_types'
  const scaleVerified = Boolean(width && length)

  return (
    <div className="mt-5 space-y-6 border-t border-line pt-5">
      {plan ? (
        <a
          href={intakeFileUrl(plan)}
          target="_blank"
          rel="noreferrer"
          className="block overflow-hidden rounded-[10px] border border-line bg-parchment"
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={intakeFileUrl(plan)} alt={`${detail.project_name} floor plan`} className="max-h-[460px] w-full object-contain" />
        </a>
      ) : (
        <p className="rounded-[10px] border border-line bg-parchment px-4 py-3 text-[12px] text-warning">
          No readable plan was stored for this submission, so it cannot be built yet.
        </p>
      )}

      <div className="grid gap-5 sm:grid-cols-2">
        <div>
          <span className="label">Client</span>
          <p className="text-[13px] text-ink">
            {[detail.client.firstName, detail.client.lastName].filter(Boolean).join(' ') || '—'}
          </p>
          <p className="mt-0.5 text-[12px] text-stone">{detail.client.email}</p>
          {detail.client.phone ? <p className="mt-0.5 text-[12px] text-stone">{detail.client.phone}</p> : null}
        </div>
        <div>
          <span className="label">Company</span>
          <p className="text-[13px] text-ink">{detail.company?.name || '—'}</p>
          {detail.company?.website ? (
            <p className="mt-0.5 text-[12px] text-stone">{detail.company.website}</p>
          ) : null}
        </div>
        <div>
          <span className="label">Project</span>
          <p className="text-[13px] text-ink">{detail.project_name}</p>
          <p className="mt-0.5 text-[12px] text-stone">
            {[detail.project?.type, detail.project?.location, detail.project?.developer]
              .filter(Boolean)
              .join(' · ') || 'No further detail supplied'}
          </p>
        </div>
        <div>
          <span className="label">Build inputs</span>
          <p className="text-[13px] text-ink">
            {width && length ? `${width} × ${length} m` : 'Scale not measured'}
          </p>
          <p className="mt-0.5 text-[12px] text-stone">
            {STYLES.find((option) => option.value === style)?.label}
            {detail.project?.unitTypes?.length ? ` · ${detail.project.unitTypes.join(', ')}` : ''}
          </p>
        </div>
      </div>

      {detail.issues.length ? (
        <div className="rounded-[10px] border border-terracotta/30 bg-terracotta/5 px-4 py-3">
          <span className="label">Intake notes</span>
          <ul className="space-y-1 text-[12px] leading-5 text-terracotta">
            {detail.issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {others.length ? (
        <div>
          <span className="label">Reference files</span>
          <p className="mb-1 text-[11px] text-stone-light">
            Reference material is stored for context only. The plan drives the model.
          </p>
          <ul className="text-[13px]">
            {others.map((file) => (
              <FileRow key={file.file_id} file={file} />
            ))}
          </ul>
        </div>
      ) : null}

      {detail.build ? (
        <div className="card px-5 py-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <span className="label">Reconstruction</span>
              <p className="text-[13px] text-ink">
                {activeJob
                  ? `${activeJob.stage_label} · ${Math.round(activeJob.progress)}%`
                  : 'Job link established'}
              </p>
            </div>
            {activeJob?.status === 'completed' ? (
              <div className="flex flex-wrap items-center gap-2">
                <Link href={`/tour/${activeJob.job_id}`} className="btn btn-secondary btn-sm">
                  Preview walkthrough
                </Link>
                {detail.delivery ? (
                  detail.delivery.portal_http_status === 200 ? (
                    <span className="chip chip-success">Sent to client</span>
                  ) : (
                    <>
                      <span className="chip chip-warning">Last send failed</span>
                      <button
                        type="button"
                        className="btn btn-primary btn-sm"
                        onClick={onDeliver}
                        disabled={busy !== ''}
                      >
                        Send to client again
                      </button>
                    </>
                  )
                ) : (
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    onClick={onDeliver}
                    disabled={busy !== ''}
                  >
                    {busy === 'send' ? 'Sending…' : 'Send to client'}
                  </button>
                )}
              </div>
            ) : activeJob && TERMINAL_JOB_STATUSES.has(activeJob.status) ? (
              <span className="chip chip-warning">{activeJob.status}</span>
            ) : null}          </div>
          {activeJob && !TERMINAL_JOB_STATUSES.has(activeJob.status) ? (
            <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-parchment">
              <div
                className="h-full rounded-full bg-forest-soft transition-[width] duration-500"
                style={{ width: `${Math.max(4, activeJob.progress)}%` }}
              />
            </div>
          ) : null}
          {activeJob?.error ? (
            <p className="mt-2 text-[12px] text-danger">{activeJob.error}</p>
          ) : null}
          {awaitingTypes && activeJob ? (
            <div className="mt-4">
              <RoomTypeReview
                jobId={activeJob.job_id}
                style={detail.build.style}
                variant={0}
                onConfirmed={refresh}
              />
            </div>
          ) : null}
        </div>
      ) : (
        <div className="card px-5 py-4">
          <span className="label">Start production</span>
          <div className="mt-2 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <div>
              <label className="label" htmlFor={`width-${requestId}`}>
                Width (m)
              </label>
              <input
                id={`width-${requestId}`}
                className="field"
                type="number"
                min={2}
                max={100}
                step="0.1"
                value={width}
                onChange={(event) => setWidth(event.target.value)}
              />
            </div>
            <div>
              <label className="label" htmlFor={`length-${requestId}`}>
                Length (m)
              </label>
              <input
                id={`length-${requestId}`}
                className="field"
                type="number"
                min={2}
                max={100}
                step="0.1"
                value={length}
                onChange={(event) => setLength(event.target.value)}
              />
            </div>
            <div>
              <label className="label" htmlFor={`style-${requestId}`}>
                Style
              </label>
              <select
                id={`style-${requestId}`}
                className="field"
                value={style}
                onChange={(event) => setStyle(event.target.value)}
              >
                {STYLES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="label" htmlFor={`floors-${requestId}`}>
                Floors in this plan
              </label>
              <select
                id={`floors-${requestId}`}
                className="field"
                value={floors}
                onChange={(event) => setFloors(Number(event.target.value))}
              >
                {[1, 2, 3, 4, 5].map((count) => (
                  <option key={count} value={count}>
                    {count}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <p className="mt-2 text-[11px] leading-5 text-stone-light">
            {scaleVerified
              ? 'Both spans supplied, so the model is built at verified scale.'
              : 'Only one span supplied or none: room sizes will be estimated and flagged approximate.'}
            {' Each additional floor needs its own plan image; the engine builds one level per job.'}
          </p>
          {error ? <p className="mt-2 text-[12px] text-danger">{error}</p> : null}
          <div className="mt-4 flex flex-wrap gap-2.5">
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={onBuild}
              disabled={busy !== '' || plans.length === 0}
            >
              Build this plan
            </button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={onIgnore} disabled={busy !== ''}>
              Archive request
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export default function RequestsPage() {
  const [requests, setRequests] = useState<IntakeRequestSummary[]>([])
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [total, setTotal] = useState(0)
  const [offline, setOffline] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let active = true
    const poll = async () => {
      try {
        const listing = await fetchIntakeRequests()
        if (!active) return
        setRequests(listing.requests)
        setCounts(listing.counts)
        setTotal(listing.total)
        setOffline(null)
      } catch (caught) {
        if (active) setOffline(messageOf(caught))
      } finally {
        if (active) setLoaded(true)
      }
    }
    void poll()
    const timer = setInterval(poll, 4000)
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [])

  const pending = counts.new ?? 0

  return (
    <>
      <Navigation />
      <main className="shell flex-1 py-10">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="eyebrow">Production queue</p>
            <h1 className="display mt-3 text-[38px] sm:text-[46px]">Requests</h1>
            <p className="mt-2 max-w-[620px] text-sm leading-6 text-stone">
              Submissions arrive here from the Crestion Labs portal with the client, company, project
              brief and the stored floor plan. Building starts from this page.
            </p>
          </div>
          <div className="flex items-center gap-2.5">
            {offline ? (
              <span className="chip chip-warning">Engine offline</span>
            ) : (
              <span className="chip chip-success">
                {pending} awaiting production · {total} received
              </span>
            )}
          </div>
        </header>

        {offline ? (
          <p className="mt-6 rounded-[10px] border border-terracotta/30 bg-terracotta/5 px-4 py-3 text-[12px] text-terracotta">
            The reconstruction engine is not answering on port 8002: {offline}
          </p>
        ) : null}

        {!loaded && !offline ? (
          <div className="mt-8 space-y-4" aria-busy="true" aria-label="Loading requests">
            {[0, 1, 2].map((index) => (
              <div key={index} className="card h-24 animate-pulse bg-parchment/60" />
            ))}
          </div>
        ) : null}

        {loaded && !offline && requests.length === 0 ? (
          <div className="card mt-8 px-6 py-14 text-center">
            <h2 className="text-2xl">No client requests yet</h2>
            <p className="mx-auto mt-2 max-w-[460px] text-sm leading-6 text-stone">
              When a client submits a floor plan from the portal it appears here within a few seconds,
              with its plan stored on this machine.
            </p>
          </div>
        ) : null}

        {requests.length > 0 ? (
          <ul className="mt-8 space-y-4">
            {requests.map((request) => {
              const isOpen = selected === request.request_id
              return (
                <li key={request.request_id} className="card overflow-hidden">
                  <button
                    type="button"
                    className="flex w-full flex-wrap items-start justify-between gap-4 px-5 py-4 text-left sm:px-6"
                    onClick={() => setSelected(isOpen ? null : request.request_id)}
                    aria-expanded={isOpen}
                  >
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2.5">
                        <h2 className="text-[17px] font-medium text-ink">{request.project_name}</h2>
                        <StatusChip status={request.status} />
                        {!request.plans_ready && request.status === 'new' ? (
                          <span className="chip chip-warning">Plan unavailable</span>
                        ) : null}
                      </div>
                      <p className="mt-1.5 text-[12px] text-stone">
                        {request.client.email}
                        {request.company_name ? ` · ${request.company_name}` : ''} ·{' '}
                        {request.file_count} file{request.file_count === 1 ? '' : 's'} ·{' '}
                        {receivedLabel(request.received_at)}
                      </p>
                      {request.issues.length ? (
                        <p className="mt-1.5 text-[12px] text-terracotta">{request.issues[0]}</p>
                      ) : null}
                    </div>
                    <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-stone-light">
                      {isOpen ? 'Close' : 'Open'}
                    </span>
                  </button>
                  {isOpen ? (
                    <div className="px-5 pb-6 sm:px-6">
                      <RequestDetail requestId={request.request_id} onChanged={() => setSelected(request.request_id)} />
                    </div>
                  ) : null}
                </li>
              )
            })}
          </ul>
        ) : null}
      </main>
    </>
  )
}
