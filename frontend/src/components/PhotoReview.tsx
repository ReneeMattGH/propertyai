'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  addReviewPhoto,
  assetUrl,
  confirmPhotoAssignments,
  fetchPhotoReview,
  removeReviewPhoto,
  replaceReviewPhoto,
  type PhotoAssignmentInput,
  type PhotoReview,
  type ReviewPhoto,
  type ReviewRoom,
  type ReconstructionJob,
} from '@/lib/reconstruction'

type Choice = number | 'exclude' | null

interface PhotoReviewProps {
  jobId: string
  onConfirmed: (job: ReconstructionJob) => void
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function defaultChoice(photo: ReviewPhoto): Choice {
  const assignment = photo.room_assignment
  if (assignment.excluded) return 'exclude'
  if (assignment.confirmed && assignment.room_id !== null) return assignment.room_id
  if (photo.suggestion?.status === 'suggested' && photo.suggestion.room_id !== null) {
    return photo.suggestion.room_id ?? null
  }
  return null
}

function roomName(
  room: ReviewRoom,
  rooms: ReviewRoom[],
  types: Record<number, string>,
): string {
  const type = types[room.room_id] ?? room.room_type ?? ''
  if (!type) return room.name || room.default_name
  if (type === 'bedroom') {
    const ordinal =
      rooms
        .filter((candidate) => candidate.room_id <= room.room_id)
        .filter((candidate) => (types[candidate.room_id] ?? candidate.room_type) === 'bedroom')
        .length || 1
    return `Bedroom ${ordinal}`
  }
  const labels = REVIEW_LABELS
  return labels[type] || type.replace(/_/g, ' ')
}

const REVIEW_LABELS: Record<string, string> = {
  entrance: 'Entrance / foyer',
  living_room: 'Living room',
  kitchen: 'Kitchen',
  bedroom: 'Bedroom',
  bathroom: 'Bathroom',
  dining_room: 'Dining room',
  hallway: 'Hallway / corridor',
  balcony: 'Balcony / terrace',
  utility: 'Utility room',
  exterior: 'Exterior / garden',
  staircase: 'Staircase',
  unknown: 'Unknown / unclassified',
}

export default function PhotoReview({ jobId, onConfirmed }: PhotoReviewProps) {
  const [review, setReview] = useState<PhotoReview | null>(null)
  const [assignments, setAssignments] = useState<Record<string, Choice>>({})
  const [roomTypes, setRoomTypes] = useState<Record<number, string>>({})
  const [loadedJobId, setLoadedJobId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [previewPhoto, setPreviewPhoto] = useState<ReviewPhoto | null>(null)
  const [brokenImages, setBrokenImages] = useState<Set<string>>(new Set())
  const [filterRoom, setFilterRoom] = useState<number | 'all'>('all')
  const replaceRefs = useRef<Record<string, HTMLInputElement | null>>({})
  const addInputRef = useRef<HTMLInputElement>(null)

  const applyReview = useCallback((next: PhotoReview) => {
    setReview(next)
    setAssignments((previous) => {
      const merged: Record<string, Choice> = {}
      for (const photo of next.photos) {
        merged[photo.photo_id] =
          photo.photo_id in previous ? previous[photo.photo_id] : defaultChoice(photo)
      }
      return merged
    })
    setRoomTypes((previous) => {
      const merged: Record<number, string> = {}
      for (const room of next.rooms) {
        merged[room.room_id] =
          previous[room.room_id] ?? room.room_type ?? 'unknown'
      }
      return merged
    })
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    fetchPhotoReview(jobId, controller.signal)
      .then((data) => {
        if (controller.signal.aborted) return
        applyReview(data)
        setError(null)
      })
      .catch((caught) => {
        if (controller.signal.aborted) return
        setError(messageOf(caught))
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadedJobId(jobId)
      })
    return () => controller.abort()
  }, [jobId, applyReview])

  const loading = loadedJobId !== jobId

  const rooms = useMemo(
    () => [...(review?.rooms ?? [])].sort((a, b) => a.room_id - b.room_id),
    [review],
  )

  const readyPhotos = useMemo(
    () => (review?.photos ?? []).filter((photo) => photo.processing.status === 'ready'),
    [review],
  )

  const unassigned = readyPhotos.filter((photo) => assignments[photo.photo_id] == null)
  const assignedPhotos = readyPhotos.filter(
    (photo) => typeof assignments[photo.photo_id] === 'number',
  )
  const excludedPhotos = readyPhotos.filter(
    (photo) => assignments[photo.photo_id] === 'exclude',
  )

  const photosForRoom = useCallback(
    (roomId: number) =>
      readyPhotos.filter((photo) => assignments[photo.photo_id] === roomId),
    [readyPhotos, assignments],
  )

  const visiblePhotos = useMemo(() => {
    if (!review) return []
    if (filterRoom === 'all') return review.photos
    return review.photos.filter((photo) => assignments[photo.photo_id] === filterRoom)
  }, [review, filterRoom, assignments])

  const setChoice = (photoId: string, choice: Choice) => {
    setAssignments((previous) => ({ ...previous, [photoId]: choice }))
    setError(null)
  }

  const updateReview = async (action: () => Promise<PhotoReview>) => {
    setBusy(true)
    setError(null)
    try {
      const next = await action()
      applyReview(next)
    } catch (caught) {
      setError(messageOf(caught))
      try {
        const next = await fetchPhotoReview(jobId)
        applyReview(next)
      } catch {
        // Keep the original error visible.
      }
    } finally {
      setBusy(false)
    }
  }

  const handleAdd = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (event.target) event.target.value = ''
    if (!file) return
    void updateReview(() => addReviewPhoto(jobId, file))
  }

  const handleReplace = (photoId: string, event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (event.target) event.target.value = ''
    if (!file) return
    void updateReview(() => replaceReviewPhoto(jobId, photoId, file))
  }

  const handleRemove = (photoId: string) => {
    void updateReview(() => removeReviewPhoto(jobId, photoId))
  }

  const handleConfirm = async () => {
    if (!review) return
    if (readyPhotos.length === 0) {
      setError('At least one usable photograph must be assigned to a room.')
      return
    }
    if (unassigned.length > 0) {
      setError(
        `Assign or exclude ${unassigned.length} remaining photograph(s) before confirming.`,
      )
      return
    }
    const payload: PhotoAssignmentInput[] = readyPhotos.map((photo) => {
      const choice = assignments[photo.photo_id]
      return choice === 'exclude'
        ? { photo_id: photo.photo_id, excluded: true }
        : { photo_id: photo.photo_id, room_id: choice as number }
    })
    const types: Record<string, string> = {}
    for (const room of rooms) {
      const type = roomTypes[room.room_id]
      if (type) types[String(room.room_id)] = type
    }
    setConfirming(true)
    setError(null)
    try {
      const job = await confirmPhotoAssignments(jobId, payload, types)
      onConfirmed(job)
    } catch (caught) {
      setError(messageOf(caught))
    } finally {
      setConfirming(false)
    }
  }

  if (loading) {
    return (
      <div className="rounded-lg bg-gray-800 p-6 text-sm text-gray-300">
        Loading the photograph review for this job…
      </div>
    )
  }

  if (!review) {
    return (
      <div className="rounded-lg border border-rose-500/40 bg-rose-950/30 p-4 text-sm text-rose-200">
        <p>The photograph review could not be loaded.</p>
        {error && <p className="mt-1 break-words text-rose-100/80">{error}</p>}
      </div>
    )
  }

  return (
    <div
      className="rounded-lg bg-gray-800 p-6"
      data-testid="photo-review"
      data-job-id={review.job_id}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold text-white">Review photo assignments</h2>
          <p className="mt-1 max-w-2xl text-sm text-gray-400">
            The vision classifier suggests a room category for each photograph. Confirm or
            correct every assignment before reconstruction starts; nothing is applied silently.
          </p>
        </div>
        <div className="flex items-center gap-2 rounded-full border border-blue-500/40 bg-blue-950/30 px-3 py-1 text-xs text-blue-200">
          <span>{review.summary.ready} usable</span>
          <span aria-hidden>·</span>
          <span>{review.summary.ambiguous_photos ?? 0} ambiguous</span>
          <span aria-hidden>·</span>
          <span>{review.summary.unavailable_photos ?? 0} unclassified</span>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <div className="rounded-lg bg-gray-900/60 px-3 py-2">
          <p className="text-[11px] uppercase tracking-wide text-gray-500">Assigned</p>
          <p className="text-lg font-semibold text-emerald-300">
            {assignedPhotos.length}/{readyPhotos.length}
          </p>
        </div>
        <div className="rounded-lg bg-gray-900/60 px-3 py-2">
          <p className="text-[11px] uppercase tracking-wide text-gray-500">Excluded</p>
          <p className="text-lg font-semibold text-gray-200">{excludedPhotos.length}</p>
        </div>
        <div className="rounded-lg bg-gray-900/60 px-3 py-2">
          <p className="text-[11px] uppercase tracking-wide text-gray-500">Rooms</p>
          <p className="text-lg font-semibold text-white">{rooms.length}</p>
        </div>
        <div className="rounded-lg bg-gray-900/60 px-3 py-2">
          <p className="text-[11px] uppercase tracking-wide text-gray-500">Rooms without photos</p>
          <p className="text-lg font-semibold text-amber-300">
            {rooms.filter((room) => photosForRoom(room.room_id).length === 0).length}
          </p>
        </div>
      </div>

      <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-semibold uppercase tracking-wide text-gray-300">
              Photographs ({review.photos.length})
            </h3>
            <div className="flex flex-wrap items-center gap-2">
              <label className="text-xs text-gray-400">
                Show
                <select
                  value={filterRoom === 'all' ? 'all' : String(filterRoom)}
                  onChange={(event) =>
                    setFilterRoom(event.target.value === 'all' ? 'all' : Number(event.target.value))
                  }
                  className="ml-1 rounded border border-gray-600 bg-gray-900 px-2 py-1 text-xs text-gray-100"
                >
                  <option value="all">All photographs</option>
                  {rooms.map((room) => (
                    <option key={room.room_id} value={String(room.room_id)}>
                      {roomName(room, rooms, roomTypes)}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                onClick={() => addInputRef.current?.click()}
                disabled={busy || review.photos.length >= 40}
                className="rounded border border-gray-600 px-3 py-1.5 text-xs font-medium text-gray-100 hover:bg-gray-700 disabled:opacity-50"
              >
                Add photograph
              </button>
              <input
                ref={addInputRef}
                type="file"
                accept=".png,.jpg,.jpeg"
                className="hidden"
                onChange={handleAdd}
              />
            </div>
          </div>

          <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
            {visiblePhotos.map((photo) => {
              const choice = assignments[photo.photo_id]
              const classification = photo.classification
              const predicted = classification.predicted_room_type
              const confidence =
                typeof classification.confidence === 'number'
                  ? Math.round(classification.confidence * 100)
                  : null
              const rejected = photo.processing.status !== 'ready'
              const duplicate = photo.quality?.duplicate_of
              const broken = brokenImages.has(photo.photo_id)
              return (
                <article
                  key={photo.photo_id}
                  data-testid={`review-photo-${photo.photo_id}`}
                  data-photo-name={photo.original_filename}
                  data-assigned-room={typeof choice === 'number' ? choice : ''}
                  className={`rounded-lg border p-3 ${
                    rejected
                      ? 'border-rose-500/40 bg-rose-950/20'
                      : choice == null
                        ? 'border-amber-500/50 bg-gray-900/70'
                        : 'border-gray-700 bg-gray-900/70'
                  }`}
                >
                  <div className="flex gap-3">
                    <button
                      type="button"
                      onClick={() => setPreviewPhoto(photo)}
                      className="relative h-24 w-28 flex-shrink-0 overflow-hidden rounded border border-gray-700 bg-gray-950"
                      title="Preview original photograph"
                    >
                      {broken ? (
                        <span className="flex h-full w-full items-center justify-center p-2 text-center text-[11px] text-rose-300">
                          Thumbnail failed to load
                        </span>
                      ) : (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={assetUrl(photo.thumbnail_url)}
                          alt={`Photograph ${photo.original_filename}`}
                          className="h-full w-full object-cover"
                          loading="lazy"
                          onError={() =>
                            setBrokenImages((previous) => {
                              const next = new Set(previous)
                              next.add(photo.photo_id)
                              return next
                            })
                          }
                        />
                      )}
                    </button>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-white">
                        {photo.original_filename}
                      </p>
                      <p className="mt-0.5 text-[11px] text-gray-400">
                        {photo.width && photo.height
                          ? `${photo.width}×${photo.height}px`
                          : 'dimensions unknown'}
                        {photo.orientation ? ` · ${photo.orientation}` : ''}
                        {photo.aspect_ratio ? ` · ${photo.aspect_ratio.toFixed(2)}:1` : ''}
                      </p>
                      <p className="mt-1 text-xs text-gray-300">
                        {predicted ? (
                          <>
                            Classified as{' '}
                            <span className="font-medium text-white">
                              {REVIEW_LABELS[predicted] || predicted.replace(/_/g, ' ')}
                            </span>
                            {confidence !== null ? ` (${confidence}%)` : ''}
                          </>
                        ) : (
                          'Classifier did not produce a category'
                        )}
                      </p>
                      {classification.ambiguous && (
                        <p className="mt-1 text-[11px] text-amber-300">
                          Ambiguous between similar categories — confirm the room explicitly.
                        </p>
                      )}
                      {classification.status === 'unavailable' && (
                        <p className="mt-1 text-[11px] text-amber-300">
                          Classifier unavailable{classification.error ? `: ${classification.error}` : ''}
                        </p>
                      )}
                      {photo.suggestion?.reason && (
                        <p className="mt-1 line-clamp-2 text-[11px] text-gray-400">
                          {photo.suggestion.reason}
                        </p>
                      )}
                      {duplicate && (
                        <p className="mt-1 text-[11px] text-amber-300">
                          Possible duplicate of {duplicate}.
                        </p>
                      )}
                      {rejected && (
                        <p className="mt-1 break-words text-[11px] text-rose-300">
                          {photo.processing.errors.join('; ') || 'This image could not be processed.'}
                        </p>
                      )}
                    </div>
                  </div>

                  {!rejected && (
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <label className="flex-1 text-[11px] text-gray-400">
                        Assign to room
                        <select
                          data-testid={`assign-${photo.photo_id}`}
                          value={choice == null ? '' : String(choice)}
                          onChange={(event) => {
                            const value = event.target.value
                            setChoice(
                              photo.photo_id,
                              value === '' ? null : value === 'exclude' ? 'exclude' : Number(value),
                            )
                          }}
                          className="mt-1 w-full rounded border border-gray-600 bg-gray-800 px-2 py-1.5 text-xs text-white"
                        >
                          <option value="">Not assigned</option>
                          {rooms.map((room) => (
                            <option key={room.room_id} value={String(room.room_id)}>
                              {roomName(room, rooms, roomTypes)}
                            </option>
                          ))}
                          <option value="exclude">Exclude from walkthrough</option>
                        </select>
                      </label>
                      <div className="flex gap-1 pt-4">
                        <button
                          type="button"
                          onClick={() => setPreviewPhoto(photo)}
                          className="rounded border border-gray-600 px-2 py-1.5 text-[11px] text-gray-200 hover:bg-gray-700"
                        >
                          Preview
                        </button>
                        <button
                          type="button"
                          onClick={() => replaceRefs.current[photo.photo_id]?.click()}
                          disabled={busy}
                          className="rounded border border-gray-600 px-2 py-1.5 text-[11px] text-gray-200 hover:bg-gray-700 disabled:opacity-50"
                        >
                          Replace
                        </button>
                        <input
                          ref={(element) => {
                            replaceRefs.current[photo.photo_id] = element
                          }}
                          type="file"
                          accept=".png,.jpg,.jpeg"
                          className="hidden"
                          onChange={(event) => handleReplace(photo.photo_id, event)}
                        />
                        <button
                          type="button"
                          onClick={() => handleRemove(photo.photo_id)}
                          disabled={busy}
                          className="rounded border border-rose-500/50 px-2 py-1.5 text-[11px] text-rose-300 hover:bg-rose-950/40 disabled:opacity-50"
                        >
                          Remove
                        </button>
                      </div>
                    </div>
                  )}
                </article>
              )
            })}
            {visiblePhotos.length === 0 && (
              <p className="text-sm text-gray-400">No photographs match this filter.</p>
            )}
          </div>
        </div>

        <aside className="space-y-4">
          <div>
            <h3 className="text-sm font-semibold uppercase tracking-wide text-gray-300">
              Room mapping preview
            </h3>
            <p className="mt-1 text-xs text-gray-500">
              Rooms come from the parsed floor plan. Set the room type so Bedroom 1 and Bedroom 2
              stay distinct; photos are assigned to the room you choose.
            </p>
          </div>
          <div className="max-h-[36rem] space-y-3 overflow-y-auto pr-1">
            {rooms.map((room) => {
              const assigned = photosForRoom(room.room_id)
              return (
                <div
                  key={room.room_id}
                  data-testid={`review-room-${room.room_id}`}
                  className={`rounded-lg border p-3 ${
                    assigned.length > 0
                      ? 'border-emerald-500/30 bg-emerald-950/10'
                      : 'border-amber-500/40 bg-amber-950/10'
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <p className="text-sm font-medium text-white">
                        {roomName(room, rooms, roomTypes)}
                      </p>
                      <p className="text-[11px] text-gray-400">
                        Room {room.room_id + 1}
                        {typeof room.area_m2 === 'number'
                          ? ` · ${room.area_m2.toFixed(1)} m²`
                          : ''}
                        {` · ${assigned.length} photo${assigned.length === 1 ? '' : 's'}`}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() =>
                        setFilterRoom(filterRoom === room.room_id ? 'all' : room.room_id)
                      }
                      className="text-[11px] text-blue-300 hover:text-blue-200"
                    >
                      {filterRoom === room.room_id ? 'Show all' : 'Show photos'}
                    </button>
                  </div>
                  <label className="mt-2 block text-[11px] text-gray-400">
                    Room type
                    <select
                      data-testid={`room-type-${room.room_id}`}
                      value={roomTypes[room.room_id] ?? ''}
                      onChange={(event) =>
                        setRoomTypes((previous) => ({
                          ...previous,
                          [room.room_id]: event.target.value,
                        }))
                      }
                      className="mt-1 w-full rounded border border-gray-600 bg-gray-800 px-2 py-1.5 text-xs text-white"
                    >
                      <option value="">Unclassified</option>
                      {(review.room_types.length > 0
                        ? review.room_types
                        : Object.keys(REVIEW_LABELS)
                      ).map((type) => (
                        <option key={type} value={type}>
                          {review.room_type_labels?.[type] || REVIEW_LABELS[type] || type}
                        </option>
                      ))}
                    </select>
                  </label>
                  {assigned.length === 0 ? (
                    <p className="mt-2 text-[11px] text-amber-200/80">
                      No photograph assigned. This room will use neutral materials.
                    </p>
                  ) : (
                    <ul className="mt-2 flex flex-wrap gap-2">
                      {assigned.map((photo) => (
                        <li key={photo.photo_id}>
                          <button
                            type="button"
                            onClick={() => setPreviewPhoto(photo)}
                            className="block h-12 w-16 overflow-hidden rounded border border-gray-600 bg-gray-950"
                            title={photo.original_filename}
                          >
                            {brokenImages.has(photo.photo_id) ? (
                              <span className="flex h-full w-full items-center justify-center text-[9px] text-rose-300">
                                broken
                              </span>
                            ) : (
                              // eslint-disable-next-line @next/next/no-img-element
                              <img
                                src={assetUrl(photo.thumbnail_url)}
                                alt={photo.original_filename}
                                className="h-full w-full object-cover"
                                loading="lazy"
                              />
                            )}
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )
            })}
          </div>

          {brokenImages.size > 0 && (
            <div className="rounded border border-rose-500/40 bg-rose-950/20 p-3 text-xs text-rose-200">
              {brokenImages.size} photograph preview(s) failed to load. Replace the affected
              image before confirming; the engine will reject undecodable textures.
            </div>
          )}

          {error && (
            <div
              role="alert"
              className="rounded border border-rose-500/40 bg-rose-950/40 p-3 text-sm text-rose-200"
            >
              {error}
            </div>
          )}

          <div className="rounded-lg border border-gray-700 bg-gray-900/70 p-3">
            <p className="text-xs text-gray-300">
              {unassigned.length > 0
                ? `${unassigned.length} photograph(s) still need a room or exclusion.`
                : 'Every usable photograph is resolved. Confirming starts texture extraction, photogrammetry and geometry.'}
            </p>
            <button
              type="button"
              data-testid="review-confirm"
              onClick={handleConfirm}
              disabled={confirming || busy || unassigned.length > 0 || readyPhotos.length === 0}
              className="mt-3 w-full rounded-lg bg-emerald-600 py-2.5 text-sm font-medium text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:bg-gray-600"
            >
              {confirming ? 'Starting reconstruction…' : 'Confirm assignments and reconstruct'}
            </button>
            <p className="mt-2 text-[11px] text-gray-500">
              Only photographs you confirm are used as materials, and each room only receives its
              own photographs.
            </p>
          </div>
        </aside>
      </div>

      {previewPhoto && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
          role="dialog"
          aria-modal="true"
          aria-label="Photograph preview"
          onClick={() => setPreviewPhoto(null)}
        >
          <div
            className="max-h-full w-full max-w-4xl overflow-auto rounded-xl border border-gray-700 bg-gray-900 p-4"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-white">{previewPhoto.original_filename}</p>
                <p className="text-[11px] text-gray-400">
                  {previewPhoto.width && previewPhoto.height
                    ? `${previewPhoto.width}×${previewPhoto.height}px`
                    : 'dimensions unknown'}
                  {previewPhoto.orientation ? ` · ${previewPhoto.orientation}` : ''}
                </p>
              </div>
              <div className="flex gap-2">
                <a
                  href={assetUrl(previewPhoto.original_url)}
                  target="_blank"
                  rel="noreferrer"
                  className="rounded border border-gray-600 px-3 py-1.5 text-xs text-gray-200 hover:bg-gray-800"
                >
                  Open original
                </a>
                <button
                  type="button"
                  onClick={() => setPreviewPhoto(null)}
                  className="rounded border border-gray-600 px-3 py-1.5 text-xs text-gray-200 hover:bg-gray-800"
                >
                  Close
                </button>
              </div>
            </div>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={assetUrl(previewPhoto.original_url)}
              alt={`Original photograph ${previewPhoto.original_filename}`}
              className="mx-auto mt-3 max-h-[70vh] w-auto max-w-full object-contain"
              onError={() =>
                setBrokenImages((previous) => {
                  const next = new Set(previous)
                  next.add(previewPhoto.photo_id)
                  return next
                })
              }
            />
            {brokenImages.has(previewPhoto.photo_id) && (
              <p className="mt-2 text-center text-sm text-rose-300">
                This original photograph could not be loaded from the engine.
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
