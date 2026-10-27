'use client'

import { useEffect, useMemo, useState } from 'react'
import {
  assetUrl,
  confirmRoomTypes,
  fetchRoomTypes,
  type ReconstructionJob,
  type RoomTypeEntry,
  type RoomTypeReview,
  type RoomTypeSelection,
} from '@/lib/reconstruction'

interface RoomTypeReviewProps {
  jobId: string
  onConfirmed: (job: ReconstructionJob) => void
  style?: string
  variant?: number
}

interface Selection {
  room_type: string
  name: string
}

const ROOM_COLORS = [
  '#3f6b52',
  '#b07a3f',
  '#b0603f',
  '#5b7287',
  '#7c6a9b',
  '#8a7a45',
  '#4f7d80',
  '#a05f72',
  '#6c7f4e',
  '#9b6b43',
  '#5f6b8a',
  '#8d8578',
]

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function dimensions(bounds?: [number, number, number, number] | null): string | null {
  if (!bounds) return null
  const width = Number(bounds[2])
  const depth = Number(bounds[3])
  if (!Number.isFinite(width) || !Number.isFinite(depth)) return null
  return `${width.toFixed(1)} × ${depth.toFixed(1)} m`
}

function typeLabel(review: RoomTypeReview, value?: string | null): string {
  if (!value) return 'Unspecified'
  return review.labels.find((option) => option.value === value)?.label ?? value.replace(/_/g, ' ')
}

function rectangleFromBounds(bounds?: [number, number, number, number] | null) {
  if (!bounds) return null
  const [x, y, width, height] = bounds
  if (![x, y, width, height].every((value) => Number.isFinite(Number(value)))) return null
  return [
    [x, y],
    [x + width, y],
    [x + width, y + height],
    [x, y + height],
  ] as [number, number][]
}

function shapeCenter(room: RoomTypeEntry, points: [number, number][]) {
  if (room.center_px && room.center_px.length === 2) {
    return [Number(room.center_px[0]), Number(room.center_px[1])] as [number, number]
  }
  const total = points.reduce(
    (accumulator, point) => [accumulator[0] + point[0], accumulator[1] + point[1]],
    [0, 0],
  )
  return [total[0] / points.length, total[1] / points.length] as [number, number]
}

export default function RoomTypeReview({
  jobId,
  onConfirmed,
  style,
  variant,
}: RoomTypeReviewProps) {
  const [review, setReview] = useState<RoomTypeReview | null>(null)
  const [selections, setSelections] = useState<Record<number, Selection>>({})
  const [selectedRoomId, setSelectedRoomId] = useState<number | null>(null)
  const [planSrc, setPlanSrc] = useState<string | null>(null)
  const [naturalSize, setNaturalSize] = useState<{ width: number; height: number } | null>(null)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    fetchRoomTypes(jobId, controller.signal)
      .then((data) => {
        if (controller.signal.aborted) return
        setReview(data)
        const chosen =
          data.assignments.length > 0
            ? data.assignments
            : data.rooms.map((room) => ({
                room_id: room.room_id,
                room_type: room.room_type,
                name: room.name ?? '',
              }))
        setSelections(
          Object.fromEntries(
            chosen.map((entry) => [
              Number(entry.room_id),
              { room_type: entry.room_type, name: entry.name ?? '' },
            ]),
          ),
        )
        const initial = data.rooms.find((room) => room.needs_confirmation) ?? data.rooms[0]
        setSelectedRoomId(initial ? initial.room_id : null)
        setPlanSrc(
          assetUrl(data.normalized_floor_plan_url || data.floor_plan_url),
        )
        setNaturalSize(null)
        setError(null)
      })
      .catch((caught) => {
        if (controller.signal.aborted) return
        setError(messageOf(caught))
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [jobId])

  const selectRoom = (roomId: number) => {
    setSelectedRoomId(roomId)
    window.requestAnimationFrame(() => {
      document
        .getElementById(`room-card-${roomId}`)
        ?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    })
  }

  const unknownRooms = useMemo(() => {
    if (!review) return []
    return review.rooms.filter(
      (room) => selections[room.room_id]?.room_type === 'unknown',
    )
  }, [review, selections])

  const uncertainRooms = review?.rooms.filter((room) => room.needs_confirmation) ?? []

  const planWidth = Number(review?.image_dimensions?.[0] ?? naturalSize?.width ?? 0)
  const planHeight = Number(review?.image_dimensions?.[1] ?? naturalSize?.height ?? 0)
  const hasPlanDimensions = planWidth > 0 && planHeight > 0

  const roomShapes = useMemo(() => {
    if (!review) return []
    return review.rooms.flatMap((room, index) => {
      const points =
        room.polygon_px && room.polygon_px.length >= 3
          ? room.polygon_px
          : rectangleFromBounds(room.bounds_px)
      if (!points || points.length < 3) return []
      return [
        {
          room,
          index,
          color: ROOM_COLORS[index % ROOM_COLORS.length],
          points,
          center: shapeCenter(room, points),
        },
      ]
    })
  }, [review])

  const selectedRoom = review?.rooms.find((room) => room.room_id === selectedRoomId) ?? null
  const selectedIndex = selectedRoom
    ? review?.rooms.findIndex((room) => room.room_id === selectedRoom.room_id) ?? 0
    : 0
  const selectedColor = ROOM_COLORS[selectedIndex % ROOM_COLORS.length]
  const selectedSelection = selectedRoom ? selections[selectedRoom.room_id] : undefined

  const handleImageError = () => {
    if (!review) return
    const fallback = assetUrl(review.floor_plan_url)
    if (planSrc !== fallback) setPlanSrc(fallback)
  }

  const handleSubmit = async () => {
    if (!review) return
    const rooms: RoomTypeSelection[] = review.rooms.map((room) => {
      const selection = selections[room.room_id]
      return {
        room_id: room.room_id,
        room_type: selection?.room_type || room.room_type,
        name: selection?.name.trim() || undefined,
      }
    })
    if (rooms.length === 0) {
      setError('The floor plan contains no rooms to confirm.')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const job = await confirmRoomTypes(jobId, rooms, style, variant)
      onConfirmed(job)
    } catch (caught) {
      setError(messageOf(caught))
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) {
    return (
      <div className="rounded-[12px] border border-line bg-surface p-5 text-sm text-stone">
        Loading room types detected from the floor plan…
      </div>
    )
  }

  if (!review) {
    return (
      <div className="rounded-[12px] border border-danger/30 bg-danger/[0.06] p-5 text-sm text-danger">
        {error || 'Room types are not available for this job yet.'}
      </div>
    )
  }

  const confirmed = review.status === 'confirmed'
  const labelRadius = Math.max(11, Math.min(planWidth || 900, planHeight || 900) * 0.022)
  const labelSize = labelRadius * 0.95

  return (
    <div
      className="rounded-[16px] border border-line bg-surface p-5 shadow-card sm:p-6"
      data-testid="room-type-review"
      data-job-id={jobId}
      data-status={confirmed ? 'confirmed' : 'pending'}
    >
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line pb-4">
        <div>
          <p className="eyebrow">Step 2 of 2</p>
          <h2 className="mt-1.5 text-[26px]">
            {confirmed ? 'Room types' : 'Review room types'}
          </h2>
          <p className="mt-1.5 max-w-3xl text-sm leading-6 text-stone">
            {confirmed
              ? 'Every room already has a type. Change any room and regenerate the furniture; the walls, doors and room sizes stay exactly as parsed from your plan.'
              : 'Your uploaded plan is shown on the left. Select a highlighted room on the plan or in the list, then set its type and display name. Rooms left as “Unspecified” stay empty.'}
          </p>
        </div>
        <div className="text-right text-xs text-stone">
          <p>{review.rooms.length} rooms detected</p>
          {review.footprint_m ? (
            <p className="mt-0.5 text-stone-light">
              Footprint {Number(review.footprint_m.width ?? 0).toFixed(1)} ×{' '}
              {Number(review.footprint_m.height ?? 0).toFixed(1)} m
            </p>
          ) : null}
        </div>
      </div>

      {!confirmed && (uncertainRooms.length > 0 || unknownRooms.length > 0) && (
        <p className="mt-4 rounded-[10px] border border-warning/30 bg-warning/[0.07] p-3 text-xs leading-5 text-ink-soft">
          {uncertainRooms.length > 0
            ? `${uncertainRooms.length} room${uncertainRooms.length === 1 ? ' is' : 's are'} low-confidence and should be reviewed. `
            : ''}
          {unknownRooms.length > 0
            ? `${unknownRooms.length} room${unknownRooms.length === 1 ? '' : 's'} will remain unfurnished unless you choose a type.`
            : ''}
        </p>
      )}

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
        <div className="lg:sticky lg:top-24 lg:self-start">
          <figure
            className="overflow-hidden rounded-[12px] border border-line bg-parchment/40"
            data-testid="room-plan"
          >
            <div
              className="relative w-full"
              style={
                hasPlanDimensions
                  ? { aspectRatio: `${planWidth} / ${planHeight}` }
                  : undefined
              }
            >
              {planSrc ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={planSrc}
                  alt="Uploaded floor plan with highlighted detected rooms"
                  draggable={false}
                  className="block h-full w-full select-none object-contain"
                  onLoad={(event) => {
                    const { naturalWidth, naturalHeight } = event.currentTarget
                    if (naturalWidth > 0 && naturalHeight > 0) {
                      setNaturalSize({ width: naturalWidth, height: naturalHeight })
                    }
                  }}
                  onError={handleImageError}
                />
              ) : (
                <div className="flex min-h-64 items-center justify-center p-6 text-sm text-stone">
                  The floor plan preview is not available for this job.
                </div>
              )}
              {planSrc && hasPlanDimensions && roomShapes.length > 0 ? (
                <svg
                  viewBox={`0 0 ${planWidth} ${planHeight}`}
                  className="absolute inset-0 h-full w-full"
                  role="group"
                  aria-label="Detected rooms on the floor plan"
                  data-testid="room-plan-overlay"
                >
                  {roomShapes.map(({ room, index, color, points, center }) => {
                    const isSelected = selectedRoomId === room.room_id
                    const name =
                      selections[room.room_id]?.name?.trim() ||
                      room.name ||
                      typeLabel(review, room.room_type)
                    const description = `${name}, ${typeLabel(review, room.room_type)}, ${
                      room.area_m2 != null ? `${Number(room.area_m2).toFixed(1)} square metres` : 'area unknown'
                    }`
                    return (
                      <g
                        key={`plan-room-${room.room_id}`}
                        role="button"
                        tabIndex={0}
                        focusable="true"
                        aria-label={`Room ${room.room_id + 1}: ${description}`}
                        aria-pressed={isSelected}
                        data-testid={`room-plan-room-${room.room_id}`}
                        data-room-id={room.room_id}
                        className="cursor-pointer outline-none"
                        onClick={() => selectRoom(room.room_id)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault()
                            selectRoom(room.room_id)
                          }
                        }}
                      >
                        <polygon
                          points={points.map((point) => point.join(',')).join(' ')}
                          fill={color}
                          fillOpacity={isSelected ? 0.4 : 0.16}
                          stroke={color}
                          strokeOpacity={isSelected ? 1 : 0.8}
                          strokeWidth={isSelected ? 4 : 2}
                          vectorEffect="non-scaling-stroke"
                        />
                        <circle
                          cx={center[0]}
                          cy={center[1]}
                          r={labelRadius}
                          fill={color}
                          stroke={isSelected ? '#fcfaf5' : '#1b211d'}
                          strokeWidth={isSelected ? 3 : 2}
                          vectorEffect="non-scaling-stroke"
                          pointerEvents="none"
                        />
                        <text
                          x={center[0]}
                          y={center[1]}
                          textAnchor="middle"
                          dominantBaseline="central"
                          fontSize={labelSize}
                          fontWeight={700}
                          fill="#ffffff"
                          pointerEvents="none"
                        >
                          {index + 1}
                        </text>
                      </g>
                    )
                  })}
                </svg>
              ) : null}
            </div>
            <figcaption className="border-t border-line bg-surface px-3 py-2 text-[11px] text-stone">
              {roomShapes.length === 0
                ? 'Room outlines were not published for this plan; use the list to assign types.'
                : 'Click a numbered room on the plan to jump to its settings.'}
            </figcaption>
          </figure>

          {selectedRoom ? (
            <div
              className="mt-3 rounded-[10px] border border-line bg-parchment/50 p-3"
              aria-live="polite"
              data-testid="room-plan-selection"
            >
              <div className="flex items-center gap-2">
                <span
                  className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold text-white"
                  style={{ backgroundColor: selectedColor }}
                >
                  {selectedIndex + 1}
                </span>
                <p className="text-sm font-medium text-ink">
                  Selected: {selectedSelection?.name?.trim() || selectedRoom.name || 'Room'}
                </p>
              </div>
              <p className="mt-1 text-xs text-stone">
                {typeLabel(review, selectedSelection?.room_type ?? selectedRoom.room_type)}
                {selectedRoom.area_m2 != null
                  ? ` · ${Number(selectedRoom.area_m2).toFixed(1)} m²`
                  : ''}
                {dimensions(selectedRoom.bounds_m)
                  ? ` · ${dimensions(selectedRoom.bounds_m)}`
                  : ''}
              </p>
            </div>
          ) : null}
        </div>

        <ol className="space-y-2" aria-label="Detected rooms">
          {review.rooms.map((room, index) => {
            const selection = selections[room.room_id] ?? {
              room_type: room.room_type,
              name: room.name ?? '',
            }
            const size = dimensions(room.bounds_m)
            const color = ROOM_COLORS[index % ROOM_COLORS.length]
            const isSelected = selectedRoomId === room.room_id
            const confidence =
              typeof room.confidence === 'number' ? Math.round(room.confidence * 100) : null
            return (
              <li
                key={room.room_id}
                id={`room-card-${room.room_id}`}
                data-testid={`room-card-${room.room_id}`}
                data-selected={isSelected ? 'true' : 'false'}
                className="rounded-[10px] border bg-surface transition-colors"
                style={{
                  borderColor: isSelected ? color : '#e0d8c8',
                  boxShadow: isSelected ? `0 0 0 1px ${color}` : undefined,
                }}
                onFocusCapture={() => setSelectedRoomId(room.room_id)}
                onClick={() => setSelectedRoomId(room.room_id)}
              >
                <div className="flex items-start gap-3 p-3">
                  <span
                    className="mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-bold text-white"
                    style={{ backgroundColor: color }}
                    aria-hidden="true"
                  >
                    {index + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-ink">
                      <span className="truncate">
                        {selection.name.trim() || room.name || typeLabel(review, room.room_type)}
                      </span>
                      {room.needs_confirmation ? (
                        <span className="chip chip-warning uppercase tracking-[0.06em]">
                          Review
                        </span>
                      ) : null}
                      {selection.room_type === 'unknown' ? (
                        <span className="chip chip-danger uppercase tracking-[0.06em]">
                          Unspecified
                        </span>
                      ) : null}
                    </p>
                    <p className="mt-0.5 text-xs text-stone">
                      Room {room.room_id + 1} · {typeLabel(review, selection.room_type)}
                      {room.area_m2 != null ? ` · ${Number(room.area_m2).toFixed(1)} m²` : ''}
                      {size ? ` · ${size}` : ''}
                    </p>
                    {confidence !== null && !confirmed ? (
                      <p className="mt-0.5 text-[11px] text-stone-light">
                        Inferred confidence {confidence}%
                        {room.alternatives && room.alternatives.length > 0
                          ? ` · alternatives: ${room.alternatives
                              .slice(0, 2)
                              .map(
                                (alternative) =>
                                  `${typeLabel(review, alternative.room_type)} (${Math.round(
                                    alternative.score * 100,
                                  )}%)`,
                              )
                              .join(', ')}`
                          : ''}
                      </p>
                    ) : null}

                    <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                      <div>
                        <label
                          htmlFor={`room-type-${room.room_id}`}
                          className="mb-1 block text-[10px] font-semibold uppercase tracking-[0.1em] text-stone"
                        >
                          Room type
                        </label>
                        <select
                          id={`room-type-${room.room_id}`}
                          value={selection.room_type}
                          onChange={(event) =>
                            setSelections((previous) => ({
                              ...previous,
                              [room.room_id]: {
                                ...selection,
                                room_type: event.target.value,
                              },
                            }))
                          }
                          className="w-full rounded-[7px] border border-line-strong bg-surface px-2.5 py-1.5 text-sm text-ink focus:border-forest-soft focus:outline-none focus:ring-2 focus:ring-forest/15"
                        >
                          {review.labels.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </div>
                      <div>
                        <label
                          htmlFor={`room-name-${room.room_id}`}
                          className="mb-1 block text-[10px] font-semibold uppercase tracking-[0.1em] text-stone"
                        >
                          Display name
                        </label>
                        <input
                          id={`room-name-${room.room_id}`}
                          type="text"
                          value={selection.name}
                          maxLength={60}
                          placeholder={typeLabel(review, selection.room_type)}
                          onChange={(event) =>
                            setSelections((previous) => ({
                              ...previous,
                              [room.room_id]: {
                                ...selection,
                                name: event.target.value,
                              },
                            }))
                          }
                          className="w-full rounded-[7px] border border-line-strong bg-surface px-2.5 py-1.5 text-sm text-ink focus:border-forest-soft focus:outline-none focus:ring-2 focus:ring-forest/15"
                        />
                      </div>
                    </div>

                    {isSelected && room.reasons && room.reasons.length > 0 && !confirmed ? (
                      <details className="mt-2 text-[11px] text-stone-light" open>
                        <summary className="cursor-pointer text-stone">Why this type?</summary>
                        <ul className="mt-1 list-disc space-y-1 pl-4">
                          {room.reasons.map((reason, index) => (
                            <li key={`reason-${room.room_id}-${index}`}>{reason}</li>
                          ))}
                        </ul>
                      </details>
                    ) : null}
                  </div>
                </div>
              </li>
            )
          })}
        </ol>
      </div>

      {error && (
        <div className="mt-4 rounded-[10px] border border-danger/30 bg-danger/[0.06] p-3 text-sm text-danger">
          {error}
        </div>
      )}

      <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
        <p className="text-[11px] leading-5 text-stone">
          Style: <span className="capitalize text-ink">{review.style ?? style ?? 'modern'}</span>
          {' · '}
          Rooms with no type are left mostly empty rather than filled at random.
        </p>
        <button
          type="button"
          onClick={handleSubmit}
          disabled={submitting || review.rooms.length === 0}
          className="btn btn-primary"
        >
          {submitting
            ? 'Saving…'
            : confirmed
              ? 'Apply changes and regenerate furniture'
              : 'Confirm room types and generate walkthrough'}
        </button>
      </div>
    </div>
  )
}
