'use client'

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import {
  downloadSceneAsset,
  fetchJob,
  fetchScene,
  type ReconstructionJob,
  type SceneBundle,
} from '@/lib/reconstruction'
import {
  PITCH_LIMIT,
  PLAYER_RADIUS,
  applyLook,
  isBlocked,
  moveWithCollision,
  movementVector,
  roomAtPoint,
  type Point2,
  type RoomArea,
  type WallBox,
} from '@/lib/collision'

const EYE_HEIGHT = 1.65
const WALK_SPEED = 2.6
const RUN_SPEED = 4.2
const GRAVITY = 12
const JUMP_SPEED = 3.6

type Phase = 'connecting' | 'downloading' | 'building' | 'ready' | 'error'
type Quality = 'low' | 'medium' | 'high'

interface GameEngineProps {
  jobId: string
}

interface Runtime {
  renderer: THREE.WebGLRenderer
  scene: THREE.Scene
  camera: THREE.PerspectiveCamera
  ceilings: THREE.Object3D[]
  position: THREE.Vector3
  yaw: number
  pitch: number
  velocityY: number
  keys: Set<string>
  jumpQueued: boolean
  walls: WallBox[]
  wallCollision: WallBox[]
  furnitureCollision: WallBox[]
  furnitureVisible: boolean
  furnitureCollisionPending: boolean
  footprint?: { width: number; height: number }
  spawn: { position: THREE.Vector3; yaw: number }
  currentRoomId: number | null
  frame: number
  setFurnitureVisible: (visible: boolean) => void
  reset: () => void
  dispose: () => void
}

function subscribeCoarsePointer(onChange: () => void): () => void {
  const query = window.matchMedia('(pointer: coarse)')
  query.addEventListener('change', onChange)
  return () => query.removeEventListener('change', onChange)
}

function coarsePointerSnapshot(): boolean {
  return window.matchMedia('(pointer: coarse)').matches
}

function coarsePointerServerSnapshot(): boolean {
  return false
}

function qualityPixelRatio(quality: Quality): number {
  const cap = quality === 'low' ? 1 : quality === 'medium' ? 1.5 : 2
  return Math.min(window.devicePixelRatio || 1, cap)
}

function findCeilingRoots(model: THREE.Object3D): THREE.Object3D[] {
  const matches: THREE.Object3D[] = []
  model.traverse((object) => {
    if (!/ceiling/i.test(object.name)) return
    let ancestorMatches = false
    let parent = object.parent
    while (parent) {
      if (/ceiling/i.test(parent.name)) {
        ancestorMatches = true
        break
      }
      parent = parent.parent
    }
    if (!ancestorMatches) matches.push(object)
  })
  return matches
}

function findFurnitureRoots(model: THREE.Object3D): THREE.Object3D[] {
  const matches: THREE.Object3D[] = []
  model.traverse((object) => {
    if (!object.name.startsWith('Furniture_')) return
    let ancestorMatches = false
    let parent = object.parent
    while (parent) {
      if (parent.name.startsWith('Furniture_')) {
        ancestorMatches = true
        break
      }
      parent = parent.parent
    }
    if (!ancestorMatches) matches.push(object)
  })
  return matches
}

function assertUsableTextures(model: THREE.Group): void {
  const failed = new Set<string>()
  model.traverse((object) => {
    const mesh = object as THREE.Mesh
    if (!mesh.isMesh) return
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material]
    for (const material of materials) {
      if (!material) continue
      const record = material as unknown as Record<string, unknown>
      for (const key of ['map', 'normalMap', 'roughnessMap', 'metalnessMap', 'emissiveMap']) {
        const texture = record[key]
        if (!(texture instanceof THREE.Texture)) continue
        const image = texture.image as { width?: number; height?: number } | null | undefined
        if (!image || !(image.width ?? 0) || !(image.height ?? 0)) {
          failed.add(mesh.name || material.name || key)
        }
      }
    }
  })
  if (failed.size > 0) {
    throw new Error(
      `The generated scene references textures that failed to decode (${[...failed]
        .slice(0, 5)
        .join(', ')}). The walkthrough was stopped instead of showing blank or unrelated materials. Retry the reconstruction.`,
    )
  }
}

export default function GameEngine({ jobId }: GameEngineProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const runtimeRef = useRef<Runtime | null>(null)
  const joystickRef = useRef({ x: 0, z: 0, active: false })

  const [phase, setPhase] = useState<Phase>('connecting')
  const [progress, setProgress] = useState(0)
  const [statusText, setStatusText] = useState('Connecting to the reconstruction engine')
  const [error, setError] = useState<string | null>(null)
  const [job, setJob] = useState<ReconstructionJob | null>(null)
  const [sceneBundle, setSceneBundle] = useState<SceneBundle | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [pointerLocked, setPointerLocked] = useState(false)
  const isTouch = useSyncExternalStore(
    subscribeCoarsePointer,
    coarsePointerSnapshot,
    coarsePointerServerSnapshot,
  )
  const [ceilingVisible, setCeilingVisible] = useState(true)
  const [furnitureVisible, setFurnitureVisible] = useState(true)
  const [quality, setQuality] = useState<Quality>('medium')
  const [helpOpen, setHelpOpen] = useState(false)
  const [fps, setFps] = useState<number | null>(null)
  const [currentRoomId, setCurrentRoomId] = useState<number | null>(null)

  const resetView = useCallback(() => {
    runtimeRef.current?.reset()
  }, [])

  const handleJoystick = useCallback((x: number, z: number, active: boolean) => {
    joystickRef.current = { x, z, active }
  }, [])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const controller = new AbortController()
    const cleanups: (() => void)[] = []
    let cancelled = false
    let runtime: Runtime | null = null

    const fail = (message: string) => {
      if (cancelled) return
      setError(message)
      setPhase('error')
      setProgress(0)
    }

    const run = async () => {
      try {
        setPhase('connecting')
        setProgress(3)
        setError(null)
        setCurrentRoomId(null)
        setStatusText('Verifying the reconstruction job')

        const [fetchedJob, scene] = await Promise.all([
          fetchJob(jobId, controller.signal),
          fetchScene(jobId, controller.signal),
        ])
        if (cancelled) return
        setJob(fetchedJob)
        setSceneBundle(scene)

        if (
          fetchedJob.job_id !== jobId ||
          scene.job_id !== jobId ||
          scene.metadata?.job_id !== jobId
        ) {
          throw new Error(
            'The engine returned a scene that belongs to a different job. Reload the tour or create a new reconstruction; this walkthrough will not show another property.',
          )
        }
        if (!scene.model_url.includes(jobId)) {
          throw new Error(
            'The scene model URL is not scoped to this job. The walkthrough was stopped instead of loading unrelated assets.',
          )
        }
        const validation = scene.manifest?.validation as
          | { geometry_valid?: boolean; navigation_available?: boolean }
          | undefined
        if (validation && validation.geometry_valid === false) {
          throw new Error(
            'The generated scene did not pass reconstruction validation. Retry the reconstruction and inspect the validation report.',
          )
        }

        if (fetchedJob.status !== 'completed') {
          throw new Error(
            fetchedJob.error ||
              `This reconstruction is still ${fetchedJob.stage_label || fetchedJob.status} ` +
                `(${Math.round(fetchedJob.progress)}%). Open the tour again once it completes.`,
          )
        }
        if (!scene.collision?.spawn?.position || scene.collision.walls.length === 0) {
          throw new Error(
            'The scene has no validated collision data. Retry the reconstruction or create a new tour.',
          )
        }

        setPhase('downloading')
        setProgress(10)
        setStatusText('Downloading the generated 3D model')
        const modelBuffer = await downloadSceneAsset(
          scene.model_url,
          (loaded, total) => {
            if (cancelled) return
            if (total) {
              setProgress(10 + Math.min(60, (loaded / total) * 60))
            } else {
              setProgress((current) => Math.min(68, current + 1))
            }
          },
          controller.signal,
        )
        if (cancelled) return

        setPhase('building')
        setProgress(78)
        setStatusText('Preparing rooms, lighting and collision')
        const gltf = await new Promise<{ scene: THREE.Group }>((resolve, reject) => {
          new GLTFLoader().parse(
            modelBuffer,
            '',
            (result) => resolve(result as unknown as { scene: THREE.Group }),
            (parseError) =>
              reject(
                new Error(
                  `The generated GLB could not be loaded: ${
                    parseError instanceof Error ? parseError.message : String(parseError)
                  }`,
                ),
              ),
          )
        })
        if (cancelled) return
        assertUsableTextures(gltf.scene)

        setProgress(90)
        runtime = buildRuntime(container, gltf.scene, scene, cleanups, {
          onLockChange: setPointerLocked,
          onFps: setFps,
          onRoomChange: setCurrentRoomId,
          joystick: joystickRef,
          quality,
          isTouch: window.matchMedia('(pointer: coarse)').matches,
          signal: controller.signal,
        })
        runtimeRef.current = runtime
        runtime.ceilings.forEach((object) => {
          object.visible = ceilingVisible
        })
        setProgress(100)
        setStatusText('Ready')
        setPhase('ready')
      } catch (caught) {
        if (controller.signal.aborted || cancelled) return
        const message =
          caught instanceof Error ? caught.message : 'The walkthrough could not be opened'
        fail(message)
      }
    }

    void run()

    return () => {
      cancelled = true
      controller.abort()
      if (runtime) {
        runtime.dispose()
        runtimeRef.current = null
      }
      cleanups.forEach((cleanup) => cleanup())
      setPointerLocked(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, attempt])

  useEffect(() => {
    const runtime = runtimeRef.current
    if (!runtime) return
    runtime.ceilings.forEach((object) => {
      object.visible = ceilingVisible
    })
  }, [ceilingVisible, phase])

  useEffect(() => {
    runtimeRef.current?.setFurnitureVisible(furnitureVisible)
  }, [furnitureVisible, phase])

  useEffect(() => {
    const runtime = runtimeRef.current
    if (!runtime) return
    runtime.renderer.setPixelRatio(qualityPixelRatio(quality))
    runtime.renderer.shadowMap.enabled = quality !== 'low'
    runtime.renderer.shadowMap.needsUpdate = true
  }, [quality, phase])

  const requestFullscreen = useCallback(() => {
    const element = containerRef.current
    if (!element) return
    if (document.fullscreenElement) {
      void document.exitFullscreen()
    } else {
      void element.requestFullscreen?.().catch(() => undefined)
    }
  }, [])

  const retry = useCallback(() => {
    setError(null)
    setAttempt((value) => value + 1)
  }, [])

  const meta = sceneBundle?.manifest
  const roomNodes = sceneBundle?.room_graph?.nodes ?? []
  const rooms = roomNodes.length
  const currentRoom = roomNodes.find((node) => node.id === currentRoomId) ?? null
  const currentFurniture = currentRoom?.furniture ?? []
  const doors = sceneBundle?.collision?.doors.length ?? 0
  const furnitureCount =
    sceneBundle?.furniture?.length ?? sceneBundle?.collision?.furniture?.length ?? 0
  const furnishingStyle = sceneBundle?.furnishing?.style ?? job?.furnishing_style ?? 'modern'
  const approximate = job ? job.scale_verified === false : false
  const showOverlay = phase !== 'ready'

  return (
    <div
      ref={containerRef}
      className="absolute inset-0 select-none overflow-hidden bg-slate-950 text-white"
      data-testid="walkthrough-viewer"
    >
      {showOverlay && (
        <div className="absolute inset-0 z-30 flex items-center justify-center bg-slate-950/95 px-4">
          <div className="w-full max-w-lg rounded-2xl border border-white/10 bg-slate-900/80 p-6 shadow-2xl">
            {phase === 'error' ? (
              <>
                <p className="text-xs font-semibold uppercase tracking-[0.2em] text-rose-400">
                  Walkthrough unavailable
                </p>
                <h2 className="mt-2 text-xl font-semibold">This scene could not be opened</h2>
                <p className="mt-3 break-words text-sm leading-6 text-slate-300">{error}</p>
                <div className="mt-6 flex flex-wrap gap-3">
                  <button
                    onClick={retry}
                    className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500"
                  >
                    Retry loading
                  </button>
                  <a
                    href="/tours"
                    className="rounded-lg border border-white/20 px-4 py-2 text-sm font-medium text-slate-200 hover:bg-white/10"
                  >
                    Back to tours
                  </a>
                  <a
                    href="/create-tour"
                    className="rounded-lg border border-white/20 px-4 py-2 text-sm font-medium text-slate-200 hover:bg-white/10"
                  >
                    New reconstruction
                  </a>
                </div>
              </>
            ) : (
              <>
                <p className="text-xs font-semibold uppercase tracking-[0.2em] text-blue-400">
                  Reconstruction
                </p>
                <h2 className="mt-2 text-xl font-semibold">
                  {job?.project_name || 'Preparing the walkthrough'}
                </h2>
                <p className="mt-3 text-sm text-slate-300">{statusText}</p>
                <div className="mt-5 h-2 overflow-hidden rounded-full bg-white/10">
                  <div
                    className="h-full rounded-full bg-blue-500 transition-[width] duration-300"
                    style={{ width: `${Math.max(3, Math.round(progress))}%` }}
                  />
                </div>
                <div className="mt-2 flex justify-between text-xs text-slate-400">
                  <span>Floor plan geometry + 3D furnishings</span>
                  <span>{Math.round(progress)}%</span>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {phase === 'ready' && (
        <>
          <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex flex-wrap items-start justify-between gap-2 p-3">
            <div className="pointer-events-auto max-w-[70%] rounded-xl border border-white/10 bg-black/55 px-3 py-2 backdrop-blur">
              <p className="truncate text-sm font-semibold">
                {job?.project_name || 'Property walkthrough'}
              </p>
              <p className="mt-0.5 text-[11px] text-slate-300">
                {rooms} room{rooms === 1 ? '' : 's'} · {furnitureCount} furniture item
                {furnitureCount === 1 ? '' : 's'} · {doors} doorway{doors === 1 ? '' : 's'}
                {approximate ? ' · approximate scale' : ' · dimensions supplied'}
                {job?.navigation_available === false ? ' · partial navigation' : ''}
              </p>
              <div
                className="mt-2 border-t border-white/10 pt-2"
                data-testid="room-info-overlay"
                data-room-id={currentRoom?.id ?? ''}
              >
                <p className="text-[10px] uppercase tracking-[0.14em] text-slate-400">
                  Current room
                </p>
                <p className="text-sm font-medium" data-testid="current-room">
                  {currentRoom?.name ?? 'Between rooms'}
                </p>
                {currentRoom && (
                  <p className="text-[11px] text-slate-300">
                    {currentRoom.room_type
                      ? currentRoom.room_type.replace(/_/g, ' ')
                      : 'unspecified type'}
                    {currentRoom.area_m2 != null
                      ? ` · ${Number(currentRoom.area_m2).toFixed(1)} m²`
                      : ''}
                    {' · '}
                    {currentFurniture.length} furniture item
                    {currentFurniture.length === 1 ? '' : 's'}
                  </p>
                )}
                {currentRoom && currentFurniture.length > 0 && (
                  <p className="mt-1 text-[10px] text-slate-400">
                    {currentFurniture
                      .slice(0, 6)
                      .map((item) => item.label)
                      .join(' · ')}
                    {currentFurniture.length > 6 ? ` +${currentFurniture.length - 6} more` : ''}
                  </p>
                )}
                {currentRoom && currentFurniture.length === 0 && (
                  <p className="mt-1 text-[10px] text-slate-400">
                    No furniture placed in this room. Unspecified rooms stay minimal, and items
                    that cannot fit with a walkable route are skipped rather than forced.
                  </p>
                )}
              </div>
            </div>
            <div className="pointer-events-auto flex flex-wrap items-center gap-2">
              <button
                onClick={() => setFurnitureVisible((value) => !value)}
                className="rounded-lg border border-white/15 bg-black/55 px-3 py-2 text-xs font-medium text-slate-100 backdrop-blur hover:bg-black/70"
                aria-pressed={furnitureVisible}
              >
                {furnitureVisible ? 'Hide furniture' : 'Show furniture'}
              </button>
              <button
                onClick={() => setCeilingVisible((value) => !value)}
                className="rounded-lg border border-white/15 bg-black/55 px-3 py-2 text-xs font-medium text-slate-100 backdrop-blur hover:bg-black/70"
                aria-pressed={ceilingVisible}
              >
                {ceilingVisible ? 'Hide ceiling' : 'Show ceiling'}
              </button>
              <label className="flex items-center gap-2 rounded-lg border border-white/15 bg-black/55 px-3 py-2 text-xs text-slate-200 backdrop-blur">
                Quality
                <select
                  value={quality}
                  onChange={(event) => setQuality(event.target.value as Quality)}
                  className="rounded bg-transparent text-xs text-white outline-none"
                  aria-label="Rendering quality"
                >
                  <option value="low" className="text-black">Low</option>
                  <option value="medium" className="text-black">Medium</option>
                  <option value="high" className="text-black">High</option>
                </select>
              </label>
              <button
                onClick={resetView}
                className="rounded-lg border border-white/15 bg-black/55 px-3 py-2 text-xs font-medium text-slate-100 backdrop-blur hover:bg-black/70"
              >
                Reset
              </button>
              <button
                onClick={requestFullscreen}
                className="rounded-lg border border-white/15 bg-black/55 px-3 py-2 text-xs font-medium text-slate-100 backdrop-blur hover:bg-black/70"
              >
                Fullscreen
              </button>
              <a
                href="/tours"
                className="rounded-lg border border-white/15 bg-black/55 px-3 py-2 text-xs font-medium text-slate-100 backdrop-blur hover:bg-black/70"
              >
                Exit
              </a>
            </div>
          </div>

          <div className="pointer-events-none absolute left-1/2 top-1/2 z-10 h-1.5 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-white/60" />

          {!isTouch && !pointerLocked && (
            <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center">
              <div className="rounded-xl border border-white/10 bg-black/60 px-5 py-3 text-center backdrop-blur">
                <p className="text-sm font-medium">Click the scene to look around</p>
                <p className="mt-1 text-xs text-slate-300">
                  WASD or arrow keys to move · Shift to run · Esc releases the cursor
                </p>
              </div>
            </div>
          )}

          {isTouch ? (
            <>
              <Joystick onChange={handleJoystick} />
              <div className="absolute bottom-10 right-4 z-10 flex flex-col items-stretch gap-2">
                <button
                  onClick={() => {
                    const runtime = runtimeRef.current
                    if (runtime) runtime.jumpQueued = true
                  }}
                  className="rounded-full border border-white/20 bg-black/60 px-4 py-3 text-xs font-semibold text-white backdrop-blur"
                >
                  Jump
                </button>
                <button
                  onClick={resetView}
                  className="rounded-full border border-white/20 bg-black/60 px-4 py-3 text-xs font-semibold text-white backdrop-blur"
                >
                  Reset
                </button>
              </div>
              <p className="pointer-events-none absolute bottom-40 left-4 z-10 w-32 rounded-full bg-black/55 px-3 py-1 text-center text-[11px] text-slate-200 backdrop-blur">
                Drag the scene to look around
              </p>
            </>
          ) : (
            <div className="pointer-events-none absolute bottom-3 left-3 z-10 hidden rounded-lg border border-white/10 bg-black/50 px-3 py-2 text-[11px] text-slate-300 backdrop-blur sm:block">
              WASD / arrows move · mouse look · Shift run · Space jump · R reset · Esc release
            </div>
          )}

          <div className="pointer-events-none absolute bottom-3 right-3 z-10 flex items-center gap-2 rounded-lg bg-black/45 px-2 py-1 text-[10px] text-slate-400 backdrop-blur">
            {fps !== null && <span>{fps} fps</span>}
            <button
              onClick={() => setHelpOpen((value) => !value)}
              className="pointer-events-auto text-slate-200 underline decoration-dotted underline-offset-2"
            >
              Scene info
            </button>
          </div>

          {helpOpen && (
            <div className="absolute bottom-12 right-3 z-20 w-80 max-w-[calc(100%-1.5rem)] rounded-xl border border-white/10 bg-slate-900/95 p-4 text-xs text-slate-300 shadow-2xl">
              <div className="flex items-start justify-between gap-3">
                <p className="text-sm font-semibold text-white">About this reconstruction</p>
                <button
                  onClick={() => setHelpOpen(false)}
                  className="text-slate-400 hover:text-white"
                  aria-label="Close scene information"
                >
                  Close
                </button>
              </div>
              <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2">
                <dt className="text-slate-400">Rooms</dt>
                <dd>{rooms}</dd>
                <dt className="text-slate-400">Furniture</dt>
                <dd>{furnitureCount} items</dd>
                <dt className="text-slate-400">Doorways</dt>
                <dd>{doors}</dd>
                <dt className="text-slate-400">Scale</dt>
                <dd>{approximate ? 'Estimated from the plan' : 'From supplied dimensions'}</dd>
                <dt className="text-slate-400">Style</dt>
                <dd className="capitalize">{furnishingStyle}</dd>
                <dt className="text-slate-400">Input</dt>
                <dd>Floor plan only</dd>
                <dt className="text-slate-400">Geometry</dt>
                <dd>{job?.geometry_valid ? 'Validated' : 'Not validated'}</dd>
                <dt className="text-slate-400">Navigation</dt>
                <dd>{job?.navigation_available ? 'All rooms reachable' : 'Partial'}</dd>
                <dt className="text-slate-400">Current room</dt>
                <dd data-testid="scene-info-room">{currentRoom?.name ?? 'Between rooms'}</dd>
                <dt className="text-slate-400">Room furniture</dt>
                <dd>
                  {currentRoom
                    ? currentFurniture.length > 0
                      ? currentFurniture.map((item) => item.label).join(', ')
                      : 'None — room left minimal'
                    : '—'}
                </dd>
              </dl>
              {(meta?.limitations?.length ?? 0) > 0 && (
                <div className="mt-3 border-t border-white/10 pt-3">
                  <p className="font-medium text-slate-200">Limitations</p>
                  <ul className="mt-1 list-disc space-y-1 pl-4">
                    {meta?.limitations?.map((limitation, index) => (
                      <li key={`limitation-${index}`}>{limitation}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}

interface JoystickProps {
  onChange: (x: number, z: number, active: boolean) => void
}

function Joystick({ onChange }: JoystickProps) {
  const [knob, setKnob] = useState<Point2>({ x: 0, z: 0 })
  const pointerId = useRef<number | null>(null)
  const base = useRef<HTMLDivElement>(null)

  const update = (event: React.PointerEvent) => {
    const element = base.current
    if (!element) return
    const rect = element.getBoundingClientRect()
    const dx = event.clientX - (rect.left + rect.width / 2)
    const dy = event.clientY - (rect.top + rect.height / 2)
    const radius = rect.width / 2
    const length = Math.hypot(dx, dy)
    const scale = length > radius ? radius / length : 1
    const x = (dx * scale) / radius
    const z = (dy * scale) / radius
    setKnob({ x: x * radius * 0.65, z: z * radius * 0.65 })
    onChange(x, z, true)
  }

  const release = () => {
    pointerId.current = null
    setKnob({ x: 0, z: 0 })
    onChange(0, 0, false)
  }

  return (
    <div
      ref={base}
      onPointerDown={(event) => {
        event.preventDefault()
        pointerId.current = event.pointerId
        event.currentTarget.setPointerCapture(event.pointerId)
        update(event)
      }}
      onPointerMove={(event) => {
        if (pointerId.current !== event.pointerId) return
        event.preventDefault()
        update(event)
      }}
      onPointerUp={(event) => {
        if (pointerId.current !== event.pointerId) return
        release()
      }}
      onPointerCancel={release}
      className="absolute bottom-4 left-4 z-10 h-32 w-32 touch-none rounded-full border border-white/20 bg-black/35 backdrop-blur"
      aria-label="Movement joystick"
      role="group"
    >
      <div className="absolute inset-4 rounded-full border border-white/10" />
      <div
        className="pointer-events-none absolute left-1/2 top-1/2 h-12 w-12 rounded-full border border-white/30 bg-white/20"
        style={{ transform: `translate(calc(-50% + ${knob.x}px), calc(-50% + ${knob.z}px))` }}
      />
    </div>
  )
}

interface BuildOptions {
  onLockChange: (locked: boolean) => void
  onFps: (fps: number) => void
  onRoomChange: (roomId: number | null) => void
  joystick: React.MutableRefObject<{ x: number; z: number; active: boolean }>
  quality: Quality
  isTouch: boolean
  signal: AbortSignal
}

function buildRuntime(
  container: HTMLDivElement,
  model: THREE.Group,
  bundle: SceneBundle,
  cleanups: (() => void)[],
  options: BuildOptions,
): Runtime {
  const { collision } = bundle
  let renderer: THREE.WebGLRenderer
  try {
    renderer = new THREE.WebGLRenderer({
      antialias: options.quality !== 'low',
      powerPreference: 'high-performance',
    })
  } catch {
    throw new Error('WebGL is not available in this browser, so the 3D walkthrough cannot start.')
  }

  renderer.setPixelRatio(qualityPixelRatio(options.quality))
  renderer.setSize(container.clientWidth, container.clientHeight)
  renderer.outputColorSpace = THREE.SRGBColorSpace
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.1
  renderer.shadowMap.enabled = options.quality !== 'low'
  renderer.shadowMap.type = THREE.PCFSoftShadowMap
  renderer.domElement.className = 'block h-full w-full touch-none'
  renderer.domElement.setAttribute('data-testid', 'walkthrough-canvas')
  container.appendChild(renderer.domElement)

  const scene = new THREE.Scene()
  scene.background = new THREE.Color(0x0c1420)

  const camera = new THREE.PerspectiveCamera(
    68,
    Math.max(1, container.clientWidth) / Math.max(1, container.clientHeight),
    0.05,
    160,
  )
  camera.rotation.order = 'YXZ'

  const bounds = new THREE.Box3().setFromObject(model)
  const size = bounds.getSize(new THREE.Vector3())
  const center = bounds.getCenter(new THREE.Vector3())
  const footprint = collision.footprint

  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry((footprint?.width ?? size.x) + 30, (footprint?.height ?? size.z) + 30),
    new THREE.MeshStandardMaterial({ color: 0x26312b, roughness: 1 }),
  )
  ground.rotation.x = -Math.PI / 2
  ground.position.set(center.x, -0.08, center.z)
  ground.receiveShadow = true
  scene.add(ground)

  const hemisphere = new THREE.HemisphereLight(0xf4f7ff, 0x4b5563, 1.25)
  scene.add(hemisphere)
  scene.add(new THREE.AmbientLight(0xffffff, 0.45))

  const sun = new THREE.DirectionalLight(0xfff1dc, 2.1)
  sun.position.set(center.x - size.x * 0.4, (collision.wall_height || 2.8) + 8, center.z - size.z * 0.3)
  sun.target.position.set(center.x, 0, center.z)
  sun.castShadow = options.quality !== 'low'
  sun.shadow.mapSize.set(2048, 2048)
  sun.shadow.bias = -0.0004
  sun.shadow.normalBias = 0.035
  const halfSpan = Math.max(size.x, size.z) * 0.75 + 3
  sun.shadow.camera.left = -halfSpan
  sun.shadow.camera.right = halfSpan
  sun.shadow.camera.top = halfSpan
  sun.shadow.camera.bottom = -halfSpan
  sun.shadow.camera.near = 0.5
  sun.shadow.camera.far = (collision.wall_height || 2.8) + 30
  sun.shadow.camera.updateProjectionMatrix()
  scene.add(sun)
  scene.add(sun.target)

  for (const room of collision.rooms) {
    const lamp = new THREE.PointLight(0xfff5e6, 7, Math.max(6, collision.wall_height * 3), 2)
    lamp.position.set(room.center[0], Math.max(2.1, collision.wall_height - 0.35), room.center[1])
    scene.add(lamp)
  }

  const ceilings = findCeilingRoots(model)
  const ceilingMeshes = new Set<THREE.Object3D>()
  for (const root of ceilings) {
    root.traverse((object) => {
      if ((object as THREE.Mesh).isMesh) ceilingMeshes.add(object)
    })
  }
  model.traverse((object) => {
    const mesh = object as THREE.Mesh
    if (!mesh.isMesh) return
    const isCeiling = ceilingMeshes.has(object)
    mesh.castShadow = !isCeiling && options.quality !== 'low'
    mesh.receiveShadow = true
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material]
    for (const material of materials) {
      if (material instanceof THREE.MeshStandardMaterial) {
        material.roughness = Math.max(0.55, material.roughness)
        material.metalness = 0
        material.envMapIntensity = 0.5
        material.needsUpdate = true
      }
    }
  })
  scene.add(model)

  const spawn = collision.spawn
  const spawnPosition = new THREE.Vector3(
    spawn.position[0],
    EYE_HEIGHT,
    spawn.position[2],
  )
  const roomAreas: RoomArea[] = (bundle.room_graph?.nodes ?? []).map((node) => ({
    id: node.id,
    center: node.center,
    bounds: node.bounds,
    polygon: node.polygon,
  }))
  const wallCollision: WallBox[] = collision.walls
  const furnitureCollision: WallBox[] = (collision.furniture ?? []).map((item) => ({
    min: item.min,
    max: item.max,
  }))
  const furnitureRoots = findFurnitureRoots(model)
  const runtime: Runtime = {
    renderer,
    scene,
    camera,
    ceilings,
    position: spawnPosition.clone(),
    yaw: spawn.yaw,
    pitch: 0,
    velocityY: 0,
    keys: new Set<string>(),
    jumpQueued: false,
    walls: [...wallCollision, ...furnitureCollision],
    wallCollision,
    furnitureCollision,
    furnitureVisible: true,
    furnitureCollisionPending: false,
    footprint,
    spawn: { position: spawnPosition.clone(), yaw: spawn.yaw },
    currentRoomId: null,
    frame: 0,
    setFurnitureVisible: (visible: boolean) => {
      runtime.furnitureVisible = visible
      for (const node of furnitureRoots) node.visible = visible
      if (!visible) {
        runtime.furnitureCollisionPending = false
        runtime.walls = runtime.wallCollision
        return
      }
      const trapped = isBlocked(
        runtime.position.x,
        runtime.position.z,
        PLAYER_RADIUS,
        runtime.furnitureCollision,
      )
      runtime.furnitureCollisionPending = trapped
      runtime.walls = trapped
        ? runtime.wallCollision
        : [...runtime.wallCollision, ...runtime.furnitureCollision]
    },
    reset: () => {
      runtime.position.copy(runtime.spawn.position)
      runtime.yaw = runtime.spawn.yaw
      runtime.pitch = 0
      runtime.velocityY = 0
      runtime.jumpQueued = false
    },
    dispose: () => {
      cancelAnimationFrame(runtime.frame)
      renderer.dispose()
      renderer.domElement.remove()
      scene.traverse((object) => {
        const mesh = object as THREE.Mesh
        if (mesh.geometry) mesh.geometry.dispose()
        const materials = Array.isArray(mesh.material) ? mesh.material : mesh.material ? [mesh.material] : []
        for (const material of materials) {
          for (const value of Object.values(material)) {
            if (value instanceof THREE.Texture) value.dispose()
          }
          material.dispose()
        }
      })
    },
  }
  runtime.reset()

  if (process.env.NODE_ENV !== 'production') {
    const debugWindow = window as unknown as {
      __walkthrough?: {
        getState: () => {
          x: number
          y: number
          z: number
          yaw: number
          pitch: number
          jobId: string
          roomId: number | null
        }
      }
    }
    debugWindow.__walkthrough = {
      getState: () => ({
        x: runtime.position.x,
        y: runtime.position.y,
        z: runtime.position.z,
        yaw: runtime.yaw,
        pitch: runtime.pitch,
        jobId: bundle.job_id,
        roomId: runtime.currentRoomId,
      }),
    }
    cleanups.push(() => {
      delete debugWindow.__walkthrough
    })
  }

  const applyCamera = () => {
    camera.position.copy(runtime.position)
    camera.rotation.y = runtime.yaw
    camera.rotation.x = Math.max(-PITCH_LIMIT, Math.min(PITCH_LIMIT, runtime.pitch))
  }

  const onKeyDown = (event: KeyboardEvent) => {
    const target = event.target as HTMLElement | null
    if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return
    runtime.keys.add(event.code)
    if (event.code === 'Space') {
      runtime.jumpQueued = true
      event.preventDefault()
    }
    if (event.code === 'KeyR') {
      runtime.reset()
    }
    if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(event.code)) {
      event.preventDefault()
    }
  }
  const onKeyUp = (event: KeyboardEvent) => runtime.keys.delete(event.code)
  const onBlur = () => runtime.keys.clear()
  window.addEventListener('keydown', onKeyDown)
  window.addEventListener('keyup', onKeyUp)
  window.addEventListener('blur', onBlur)
  cleanups.push(() => {
    window.removeEventListener('keydown', onKeyDown)
    window.removeEventListener('keyup', onKeyUp)
    window.removeEventListener('blur', onBlur)
  })

  const canvas = renderer.domElement
  const onPointerLockChange = () => {
    options.onLockChange(document.pointerLockElement === canvas)
  }
  document.addEventListener('pointerlockchange', onPointerLockChange)
  cleanups.push(() => document.removeEventListener('pointerlockchange', onPointerLockChange))

  const onMouseMove = (event: MouseEvent) => {
    if (document.pointerLockElement !== canvas) return
    const next = applyLook(runtime.yaw, runtime.pitch, event.movementX, event.movementY)
    runtime.yaw = next.yaw
    runtime.pitch = next.pitch
  }
  document.addEventListener('mousemove', onMouseMove)
  cleanups.push(() => document.removeEventListener('mousemove', onMouseMove))

  const onCanvasClick = () => {
    if (options.isTouch || document.pointerLockElement === canvas) return
    try {
      const request = canvas.requestPointerLock()
      if (request && typeof request.catch === 'function') {
        request.catch(() => options.onLockChange(false))
      }
    } catch {
      options.onLockChange(false)
    }
  }
  canvas.addEventListener('click', onCanvasClick)
  cleanups.push(() => canvas.removeEventListener('click', onCanvasClick))

  let touchId: number | null = null
  let lastTouch = { x: 0, y: 0 }
  const onTouchStart = (event: TouchEvent) => {
    const touch = event.changedTouches[0]
    if (!touch) return
    touchId = touch.identifier
    lastTouch = { x: touch.clientX, y: touch.clientY }
    event.preventDefault()
  }
  const onTouchMove = (event: TouchEvent) => {
    for (let index = 0; index < event.changedTouches.length; index += 1) {
      const touch = event.changedTouches[index]
      if (touch.identifier !== touchId) continue
      const next = applyLook(
        runtime.yaw,
        runtime.pitch,
        touch.clientX - lastTouch.x,
        touch.clientY - lastTouch.y,
        0.0045,
      )
      runtime.yaw = next.yaw
      runtime.pitch = next.pitch
      lastTouch = { x: touch.clientX, y: touch.clientY }
      event.preventDefault()
    }
  }
  const onTouchEnd = (event: TouchEvent) => {
    for (let index = 0; index < event.changedTouches.length; index += 1) {
      if (event.changedTouches[index].identifier === touchId) touchId = null
    }
  }
  canvas.addEventListener('touchstart', onTouchStart, { passive: false })
  canvas.addEventListener('touchmove', onTouchMove, { passive: false })
  canvas.addEventListener('touchend', onTouchEnd)
  canvas.addEventListener('touchcancel', onTouchEnd)
  cleanups.push(() => {
    canvas.removeEventListener('touchstart', onTouchStart)
    canvas.removeEventListener('touchmove', onTouchMove)
    canvas.removeEventListener('touchend', onTouchEnd)
    canvas.removeEventListener('touchcancel', onTouchEnd)
  })

  const resize = () => {
    const width = container.clientWidth
    const height = container.clientHeight
    if (!width || !height) return
    camera.aspect = width / height
    camera.updateProjectionMatrix()
    renderer.setSize(width, height, false)
  }
  const observer = new ResizeObserver(resize)
  observer.observe(container)
  cleanups.push(() => observer.disconnect())

  applyCamera()

  let lastTime = performance.now()
  let frameCount = 0
  let fpsWindow = lastTime
  const animate = (now: number) => {
    runtime.frame = requestAnimationFrame(animate)
    const delta = Math.min(0.05, Math.max(0.001, (now - lastTime) / 1000))
    lastTime = now

    if (
      runtime.furnitureVisible &&
      runtime.furnitureCollisionPending &&
      !isBlocked(
        runtime.position.x,
        runtime.position.z,
        PLAYER_RADIUS,
        runtime.furnitureCollision,
      )
    ) {
      runtime.furnitureCollisionPending = false
      runtime.walls = [...runtime.wallCollision, ...runtime.furnitureCollision]
    }

    let forward = 0
    let strafe = 0
    if (runtime.keys.has('KeyW') || runtime.keys.has('ArrowUp')) forward += 1
    if (runtime.keys.has('KeyS') || runtime.keys.has('ArrowDown')) forward -= 1
    if (runtime.keys.has('KeyD') || runtime.keys.has('ArrowRight')) strafe += 1
    if (runtime.keys.has('KeyA') || runtime.keys.has('ArrowLeft')) strafe -= 1
    const joystick = options.joystick.current
    if (joystick.active) {
      forward -= joystick.z
      strafe += joystick.x
    }

    if (forward !== 0 || strafe !== 0) {
      const running = runtime.keys.has('ShiftLeft') || runtime.keys.has('ShiftRight')
      const speed = running ? RUN_SPEED : WALK_SPEED
      const direction = movementVector(forward, strafe, runtime.yaw)
      const moved: Point2 = moveWithCollision(
        { x: runtime.position.x, z: runtime.position.z },
        { x: direction.x * speed * delta, z: direction.z * speed * delta },
        PLAYER_RADIUS,
        runtime.walls,
        runtime.footprint,
      )
      runtime.position.x = moved.x
      runtime.position.z = moved.z
    }

    if (runtime.jumpQueued && runtime.position.y <= EYE_HEIGHT + 1e-4) {
      runtime.velocityY = JUMP_SPEED
    }
    runtime.jumpQueued = false
    runtime.velocityY -= GRAVITY * delta
    runtime.position.y += runtime.velocityY * delta
    const ceilingLimit = Math.max(EYE_HEIGHT, (collision.wall_height || 2.8) - 0.12)
    if (runtime.position.y > ceilingLimit) {
      runtime.position.y = ceilingLimit
      runtime.velocityY = Math.min(0, runtime.velocityY)
    }
    if (runtime.position.y <= EYE_HEIGHT) {
      runtime.position.y = EYE_HEIGHT
      runtime.velocityY = 0
    }

    applyCamera()
    const detectedRoom = roomAtPoint(runtime.position.x, runtime.position.z, roomAreas)
    if (detectedRoom !== runtime.currentRoomId) {
      runtime.currentRoomId = detectedRoom
      options.onRoomChange(detectedRoom)
    }
    renderer.render(scene, camera)

    frameCount += 1
    if (now - fpsWindow >= 1000) {
      options.onFps(Math.round((frameCount * 1000) / (now - fpsWindow)))
      frameCount = 0
      fpsWindow = now
    }
  }
  runtime.frame = requestAnimationFrame(animate)

  return runtime
}
