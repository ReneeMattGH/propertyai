'use client'

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'
import {
  downloadSceneAsset,
  fetchJob,
  fetchScene,
  type FurnitureItem,
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
  type Footprint,
  type Point2,
  type RoomArea,
  type WallBox,
} from '@/lib/collision'

const EYE_HEIGHT = 1.63
const WALK_SPEED = 2.45
const RUN_SPEED = 3.9
const ACCELERATION = 12
const DECELERATION = 16
const GRAVITY = 12
const JUMP_SPEED = 3.4
const DESKTOP_FOV = 64
const TOUCH_FOV = 72
// A level camera puts the wall/ceiling junction on the horizon and leaves the
// top half of the frame empty. A slight downward angle frames the furniture.
const DESKTOP_SPAWN_PITCH = -0.12
const TOUCH_SPAWN_PITCH = -0.18
const FOCAL_ASSETS = [
  'bed_queen',
  'bed_double',
  'bed_single',
  'sofa',
  'sectional',
  'dining_table',
  'kitchen_counter',
  'kitchen_island',
  'desk',
  'vanity',
  'wardrobe',
  'tv_stand',
  'armchair',
  'coffee_table',
]

type Phase = 'connecting' | 'downloading' | 'building' | 'ready' | 'error'
type Quality = 'low' | 'medium' | 'high'
type LightingPreset = 'daylight' | 'evening' | 'neutral'

interface GameEngineProps {
  jobId: string
}

interface RenderStats {
  drawCalls: number
  triangles: number
  textures: number
  geometries: number
  programs: number
  fps: number
  quality: Quality
  lighting: LightingPreset
}

interface Runtime {
  renderer: THREE.WebGLRenderer
  scene: THREE.Scene
  camera: THREE.PerspectiveCamera
  ceilings: THREE.Object3D[]
  position: THREE.Vector3
  velocity: THREE.Vector2
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
  footprint?: Footprint
  spawn: { position: THREE.Vector3; yaw: number }
  currentRoomId: number | null
  lightingPreset: LightingPreset
  frame: number
  setFurnitureVisible: (visible: boolean) => void
  setLightingPreset: (preset: LightingPreset) => void
  setQuality: (quality: Quality) => void
  teleportToRoom: (roomId: number) => boolean
  getStats: () => RenderStats
  reset: () => void
  dispose: () => void
}

interface LightingConfig {
  label: string
  exposure: number
  sunColor: number
  sunIntensity: number
  sunDirection: [number, number, number]
  hemiSky: number
  hemiGround: number
  hemiIntensity: number
  ambient: number
  fill: number
  lampColor: number
  lampIntensity: number
  envIntensity: number
  skyTop: number
  skyHorizon: number
  groundColor: number
}

const LIGHTING_PRESETS: Record<LightingPreset, LightingConfig> = {
  daylight: {
    label: 'Daylight',
    exposure: 0.86,
    sunColor: 0xfff2df,
    sunIntensity: 2.05,
    sunDirection: [0.42, 0.86, 0.3],
    hemiSky: 0xe9f1fb,
    hemiGround: 0xa79b87,
    hemiIntensity: 0.54,
    ambient: 0.11,
    fill: 0.18,
    lampColor: 0xffefd7,
    lampIntensity: 12,
    envIntensity: 0.52,
    skyTop: 0x9abfe2,
    skyHorizon: 0xf0e6d4,
    groundColor: 0x9a9180,
  },
  evening: {
    label: 'Warm evening',
    exposure: 0.84,
    sunColor: 0xffb377,
    sunIntensity: 0.8,
    sunDirection: [-0.55, 0.42, -0.35],
    hemiSky: 0x38425c,
    hemiGround: 0x352a20,
    hemiIntensity: 0.32,
    ambient: 0.08,
    fill: 0.06,
    lampColor: 0xffc78d,
    lampIntensity: 24,
    envIntensity: 0.28,
    skyTop: 0x222c45,
    skyHorizon: 0xc9895f,
    groundColor: 0x3b342b,
  },
  neutral: {
    label: 'Neutral',
    exposure: 0.9,
    sunColor: 0xffffff,
    sunIntensity: 1.6,
    sunDirection: [0.2, 0.92, 0.36],
    hemiSky: 0xf1f3f5,
    hemiGround: 0x867f73,
    hemiIntensity: 0.66,
    ambient: 0.13,
    fill: 0.18,
    lampColor: 0xfff6e8,
    lampIntensity: 15,
    envIntensity: 0.5,
    skyTop: 0xc3ced8,
    skyHorizon: 0xefeee8,
    groundColor: 0x8e8c84,
  },
}

const QUALITY_OPTIONS: { value: Quality; label: string }[] = [
  { value: 'low', label: 'Performance' },
  { value: 'medium', label: 'Balanced' },
  { value: 'high', label: 'High' },
]

const LIGHTING_OPTIONS: { value: LightingPreset; label: string }[] = [
  { value: 'daylight', label: 'Daylight' },
  { value: 'evening', label: 'Evening' },
  { value: 'neutral', label: 'Neutral' },
]

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

function longestSightYaw(
  x: number,
  z: number,
  roomId: number | null,
  rooms: RoomArea[],
  walls: WallBox[],
  fallback: number,
): number {
  let bestYaw = fallback
  let bestDistance = 0
  for (let index = 0; index < 32; index += 1) {
    const yaw = -Math.PI + index * ((2 * Math.PI) / 32)
    const dx = -Math.sin(yaw)
    const dz = -Math.cos(yaw)
    let distance = 0
    for (let step = 1; step <= 40; step += 1) {
      const px = x + dx * step * 0.1
      const pz = z + dz * step * 0.1
      if (isBlocked(px, pz, 0.06, walls)) break
      if (roomId != null && roomAtPoint(px, pz, rooms) !== roomId) break
      distance = step * 0.1
    }
    if (distance > bestDistance) {
      bestDistance = distance
      bestYaw = yaw
    }
  }
  return bestYaw
}

function resolveSpawn(point: Point2, walls: WallBox[], footprint?: Footprint): Point2 {
  if (!isBlocked(point.x, point.z, PLAYER_RADIUS, walls)) return point
  for (let ring = 1; ring <= 20; ring += 1) {
    const radius = ring * 0.2
    for (let index = 0; index < 24; index += 1) {
      const angle = (index / 24) * Math.PI * 2
      const x = point.x + Math.cos(angle) * radius
      const z = point.z + Math.sin(angle) * radius
      if (footprint) {
        if (x < PLAYER_RADIUS || z < PLAYER_RADIUS) continue
        if (x > footprint.width - PLAYER_RADIUS || z > footprint.height - PLAYER_RADIUS) continue
      }
      if (!isBlocked(x, z, PLAYER_RADIUS, walls)) return { x, z }
    }
  }
  return point
}

interface FocalView {
  yaw: number
  pitch: number
}

function blendAngle(from: number, to: number, amount: number): number {
  let delta = (to - from) % (Math.PI * 2)
  if (delta > Math.PI) delta -= Math.PI * 2
  if (delta < -Math.PI) delta += Math.PI * 2
  return from + delta * amount
}

function primaryFurnitureTarget(
  roomId: number | null,
  items: FurnitureItem[],
): FurnitureItem | null {
  if (roomId == null || items.length === 0) return null
  const inRoom = items.filter((item) => item.room_id === roomId)
  for (const asset of FOCAL_ASSETS) {
    const candidates = inRoom.filter(
      (item) => item.asset_id === asset || item.asset_id.startsWith(`${asset}_`),
    )
    if (candidates.length > 0) return candidates[0]
  }
  return null
}

function viewToward(
  x: number,
  z: number,
  target: FurnitureItem,
  walls: WallBox[],
): FocalView | null {
  const dx = target.position[0] - x
  const dz = target.position[2] - z
  const distance = Math.hypot(dx, dz)
  if (distance < 0.6) return null
  for (let step = 1; step <= Math.floor(distance / 0.25); step += 1) {
    const t = (step * 0.25) / distance
    if (isBlocked(x + dx * t, z + dz * t, 0.05, walls)) return null
  }
  const top = Math.min((target.height_m ?? 0.7) * 0.72, 1.15)
  return {
    yaw: Math.atan2(-dx, -dz),
    pitch: THREE.MathUtils.clamp(Math.atan2(top - EYE_HEIGHT, distance), -0.24, -0.04),
  }
}

function primaryFurnitureView(
  x: number,
  z: number,
  roomId: number | null,
  items: FurnitureItem[],
  walls: WallBox[],
): FocalView | null {
  const target = primaryFurnitureTarget(roomId, items)
  return target ? viewToward(x, z, target, walls) : null
}

function contactShadowTexture(): THREE.CanvasTexture {
  const size = 128
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const context = canvas.getContext('2d')
  if (context) {
    const gradient = context.createRadialGradient(
      size / 2,
      size / 2,
      size * 0.03,
      size / 2,
      size / 2,
      size * 0.5,
    )
    // alphaMap samples the green channel, so the gradient must be grayscale.
    gradient.addColorStop(0, '#ffffff')
    gradient.addColorStop(0.52, '#7a7a7a')
    gradient.addColorStop(1, '#000000')
    context.fillStyle = gradient
    context.fillRect(0, 0, size, size)
  }
  const texture = new THREE.CanvasTexture(canvas)
  texture.needsUpdate = true
  return texture
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
  const [lighting, setLighting] = useState<LightingPreset>('daylight')
  const [helpOpen, setHelpOpen] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [fps, setFps] = useState<number | null>(null)
  const [renderStats, setRenderStats] = useState<RenderStats | null>(null)
  const [currentRoomId, setCurrentRoomId] = useState<number | null>(null)

  const initialQualityApplied = useRef(false)
  useEffect(() => {
    if (initialQualityApplied.current) return
    initialQualityApplied.current = true
    if (!isTouch) return
    const frame = requestAnimationFrame(() => setQuality('low'))
    return () => cancelAnimationFrame(frame)
  }, [isTouch])

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
        setStatusText('Preparing materials, lighting and collision')
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

        setProgress(88)
        setStatusText('Composing the first view')
        runtime = await buildRuntime(container, gltf.scene, scene, cleanups, {
          onLockChange: setPointerLocked,
          onFps: setFps,
          onRoomChange: setCurrentRoomId,
          joystick: joystickRef,
          quality,
          lighting,
          isTouch: window.matchMedia('(pointer: coarse)').matches,
          signal: controller.signal,
        })
        if (cancelled) {
          runtime.dispose()
          return
        }
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
    runtimeRef.current?.setQuality(quality)
  }, [quality, phase])

  useEffect(() => {
    runtimeRef.current?.setLightingPreset(lighting)
  }, [lighting, phase])

  useEffect(() => {
    if (!helpOpen) return
    setRenderStats(runtimeRef.current?.getStats() ?? null)
  }, [helpOpen, phase, fps])

  useEffect(() => {
    if (phase === 'ready') return
    const frame = requestAnimationFrame(() => setMenuOpen(false))
    return () => cancelAnimationFrame(frame)
  }, [phase])

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

  const viewerControlProps = {
    furnitureVisible,
    ceilingVisible,
    quality,
    lighting,
    onFurniture: () => setFurnitureVisible((value) => !value),
    onCeiling: () => setCeilingVisible((value) => !value),
    onQuality: setQuality,
    onLighting: setLighting,
    onReset: resetView,
    onFullscreen: requestFullscreen,
  }

  return (
    <div
      ref={containerRef}
      className="absolute inset-0 select-none overflow-hidden bg-[#0e1310] text-ivory"
      data-testid="walkthrough-viewer"
    >
      {showOverlay && (
        <div className="absolute inset-0 z-30 flex items-center justify-center bg-[#0e1310] px-4">
          <div className="w-full max-w-md rounded-[16px] border border-white/10 bg-[#151b17]/95 p-6 shadow-[0_48px_120px_-48px_rgba(0,0,0,0.9)] sm:p-7">
            {phase === 'error' ? (
              <>
                <p className="text-[10px] font-semibold uppercase tracking-[0.24em] text-terracotta">
                  Walkthrough unavailable
                </p>
                <h2 className="mt-2 font-serif text-[24px] leading-tight text-ivory">
                  This scene could not be opened
                </h2>
                <p className="mt-3 break-words text-[13px] leading-6 text-ivory/65">{error}</p>
                <div className="mt-6 flex flex-wrap gap-2">
                  <button
                    onClick={retry}
                    className="inline-flex items-center rounded-full bg-bronze px-4 py-2 text-[12px] font-semibold text-ivory transition hover:bg-bronze-soft hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-bronze-soft motion-reduce:transition-none"
                  >
                    Retry loading
                  </button>
                  <a
                    href="/tours"
                    className="inline-flex items-center rounded-full border border-white/15 px-4 py-2 text-[12px] font-medium text-ivory/80 transition hover:border-white/30 hover:text-ivory motion-reduce:transition-none"
                  >
                    Back to tours
                  </a>
                  <a
                    href="/create-tour"
                    className="inline-flex items-center rounded-full border border-white/15 px-4 py-2 text-[12px] font-medium text-ivory/80 transition hover:border-white/30 hover:text-ivory motion-reduce:transition-none"
                  >
                    New reconstruction
                  </a>
                </div>
              </>
            ) : (
              <>
                <p className="text-[10px] font-semibold uppercase tracking-[0.24em] text-bronze-soft">
                  Reconstruction
                </p>
                <h2 className="mt-2 font-serif text-[24px] leading-tight text-ivory">
                  {job?.project_name || 'Preparing the walkthrough'}
                </h2>
                <p className="mt-2 text-[12px] leading-5 text-ivory/55">{statusText}</p>
                <div className="mt-5 h-1 overflow-hidden rounded-full bg-white/10">
                  <div
                    className="h-full rounded-full bg-bronze transition-[width] duration-300 motion-reduce:transition-none"
                    style={{ width: `${Math.max(3, Math.round(progress))}%` }}
                  />
                </div>
                <div className="mt-2.5 flex items-center justify-between text-[10px] uppercase tracking-[0.14em] text-ivory/40">
                  <span>Floor plan geometry + 3D furnishings</span>
                  <span className="tabular-nums text-ivory/70">{Math.round(progress)}%</span>
                </div>
                <div className="mt-5 grid grid-cols-3 gap-2 text-[10px] text-ivory/35">
                  {['Verify', 'Download', 'Compose'].map((step, index) => {
                    const threshold = index === 0 ? 3 : index === 1 ? 10 : 78
                    const complete = progress >= threshold
                    return (
                      <span
                        key={step}
                        className={
                          complete
                            ? 'border-t border-bronze/70 pt-2 text-ivory/70'
                            : 'border-t border-white/10 pt-2'
                        }
                      >
                        {step}
                      </span>
                    )
                  })}
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {phase === 'ready' && (
        <>
          <div className="pointer-events-none absolute inset-x-0 top-0 z-20 flex items-start justify-between gap-3 p-3 sm:p-4">
            <div className="pointer-events-auto w-[min(21rem,calc(100vw-4.5rem))] rounded-[12px] border border-white/10 bg-[#111713]/80 p-3.5 shadow-[0_24px_60px_-32px_rgba(0,0,0,0.95)] backdrop-blur-md max-sm:w-[calc(100vw-7rem)] max-sm:p-2.5">
              <p className="hidden text-[9px] font-semibold uppercase tracking-[0.26em] text-bronze-soft sm:block">
                Walkthrough
              </p>
              <h2 className="truncate font-serif text-[16px] leading-tight text-ivory sm:mt-1 sm:text-[19px]">
                {job?.project_name || 'Property walkthrough'}
              </h2>
              <p className="mt-1 hidden text-[10.5px] leading-4 text-ivory/55 sm:block">
                {rooms} room{rooms === 1 ? '' : 's'} · {furnitureCount} furniture · {doors} doorway
                {doors === 1 ? '' : 's'}
                {approximate ? ' · estimated scale' : ' · measured scale'}
                {job?.navigation_available === false ? ' · partial navigation' : ''}
              </p>
              <div
                className="mt-1.5 border-t border-white/10 pt-1.5 sm:mt-2.5 sm:pt-2.5"
                data-testid="room-info-overlay"
                data-room-id={currentRoom?.id ?? ''}
              >
                <div className="flex items-baseline justify-between gap-2">
                  <p className="hidden text-[9px] uppercase tracking-[0.2em] text-ivory/40 sm:block">
                    Current room
                  </p>
                  {currentRoom?.area_m2 != null && (
                    <p className="text-[9.5px] tabular-nums text-ivory/45 sm:text-[10px]">
                      {Number(currentRoom.area_m2).toFixed(1)} m²
                    </p>
                  )}
                </div>
                <p
                  className="mt-0.5 font-serif text-[15px] leading-tight text-ivory sm:text-[17px]"
                  data-testid="current-room"
                >
                  {currentRoom?.name ?? 'Between rooms'}
                </p>
                {currentRoom ? (
                  <p className="mt-0.5 text-[10px] text-ivory/55 first-letter:uppercase sm:text-[10.5px]">
                    {`${
                      currentRoom.room_type
                        ? currentRoom.room_type.replace(/_/g, ' ')
                        : 'unspecified type'
                    } · ${currentFurniture.length} ${
                      currentFurniture.length === 1 ? 'item' : 'items'
                    }`}
                  </p>
                ) : (
                  <p className="mt-0.5 text-[10px] text-ivory/45 sm:text-[10.5px]">
                    Step into a room to see its details.
                  </p>
                )}
                {currentRoom && currentFurniture.length > 0 && (
                  <p className="mt-1 hidden text-[10px] leading-4 text-ivory/40 sm:block">
                    {currentFurniture
                      .slice(0, 5)
                      .map((item) => item.label)
                      .join(' · ')}
                    {currentFurniture.length > 5 ? ` +${currentFurniture.length - 5}` : ''}
                  </p>
                )}
                {currentRoom && currentFurniture.length === 0 && (
                  <p className="mt-1 hidden text-[10px] leading-4 text-ivory/40 sm:block">
                    Left minimal — items that would block a walkable route are skipped rather than
                    forced.
                  </p>
                )}
              </div>
            </div>

            <div className="pointer-events-auto flex max-w-[58%] flex-col items-end gap-2">
              <div className="hidden max-w-full md:block">
                <ViewerControls {...viewerControlProps} />
              </div>
              <div className="flex items-center gap-1.5 md:hidden">
                <button
                  onClick={() => setMenuOpen((value) => !value)}
                  className="inline-flex items-center rounded-full border border-white/12 bg-[#111713]/80 px-3 py-1.5 text-[11px] font-medium text-ivory/85 backdrop-blur-md transition hover:border-white/25 hover:text-ivory motion-reduce:transition-none"
                  aria-expanded={menuOpen}
                >
                  {menuOpen ? 'Close' : 'Options'}
                </button>
                <a
                  href="/tours"
                  className="inline-flex items-center rounded-full border border-white/12 bg-[#111713]/80 px-3 py-1.5 text-[11px] font-medium text-ivory/85 backdrop-blur-md transition hover:border-white/25 hover:text-ivory motion-reduce:transition-none"
                >
                  Exit
                </a>
              </div>
            </div>
          </div>

          {menuOpen && (
            <div className="pointer-events-auto absolute right-3 top-[7.5rem] z-20 w-[min(21rem,calc(100vw-1.5rem))] rounded-[12px] border border-white/10 bg-[#111713]/92 p-3 shadow-[0_28px_70px_-30px_rgba(0,0,0,0.95)] backdrop-blur-md md:hidden">
              <ViewerControls {...viewerControlProps} compact />
              {roomNodes.length > 1 && (
                <div className="mt-2 border-t border-white/10 pt-2" data-testid="mobile-room-list">
                  <p className="px-1 pb-1 text-[9px] font-semibold uppercase tracking-[0.2em] text-ivory/35">
                    Rooms
                  </p>
                  <div className="grid grid-cols-2 gap-1">
                    {roomNodes.map((node) => {
                      const active = node.id === currentRoomId
                      return (
                        <button
                          key={node.id}
                          onClick={() => {
                            runtimeRef.current?.teleportToRoom(node.id)
                            setMenuOpen(false)
                          }}
                          aria-current={active ? 'location' : undefined}
                          className={
                            active
                              ? 'truncate rounded-[7px] bg-white/12 px-2 py-1.5 text-left text-[10.5px] text-ivory'
                              : 'truncate rounded-[7px] border border-white/8 px-2 py-1.5 text-left text-[10.5px] text-ivory/65'
                          }
                        >
                          {node.name || `Room ${node.id + 1}`}
                        </button>
                      )
                    })}
                  </div>
                </div>
              )}
              <a
                href={`/tour/${jobId}`}
                className="mt-1.5 flex items-center justify-center rounded-full border border-white/12 px-3 py-1.5 text-[11px] font-medium text-ivory/75 transition hover:border-white/30 hover:text-ivory motion-reduce:transition-none"
              >
                Reconstruction details
              </a>
            </div>
          )}

          {roomNodes.length > 1 && (
            <div
              className="pointer-events-none absolute left-4 top-1/2 z-20 hidden -translate-y-1/2 lg:block"
              data-testid="room-rail"
            >
              <div className="pointer-events-auto w-44 rounded-[12px] border border-white/10 bg-[#111713]/70 p-2 backdrop-blur-md">
                <p className="px-2 pb-1.5 pt-1 text-[9px] font-semibold uppercase tracking-[0.2em] text-ivory/35">
                  Rooms
                </p>
                <ul className="max-h-[52vh] space-y-0.5 overflow-y-auto pr-0.5">
                  {roomNodes.map((node) => {
                    const active = node.id === currentRoomId
                    return (
                      <li key={node.id}>
                        <button
                          onClick={() => runtimeRef.current?.teleportToRoom(node.id)}
                          aria-current={active ? 'location' : undefined}
                          className={
                            active
                              ? 'flex w-full items-center justify-between gap-2 rounded-[7px] bg-white/10 px-2 py-1.5 text-left text-[11px] text-ivory'
                              : 'flex w-full items-center justify-between gap-2 rounded-[7px] px-2 py-1.5 text-left text-[11px] text-ivory/55 transition hover:bg-white/[0.06] hover:text-ivory/85 motion-reduce:transition-none'
                          }
                        >
                          <span className="truncate">{node.name || `Room ${node.id + 1}`}</span>
                          {node.area_m2 != null && (
                            <span className="shrink-0 text-[9px] tabular-nums text-ivory/35">
                              {Number(node.area_m2).toFixed(0)} m²
                            </span>
                          )}
                        </button>
                      </li>
                    )
                  })}
                </ul>
              </div>
            </div>
          )}

          {!isTouch && <div className="pointer-events-none absolute left-1/2 top-1/2 z-10 h-1 w-1 -translate-x-1/2 -translate-y-1/2 rounded-full bg-white/45" />}

          {!isTouch && !pointerLocked && (
            <div className="pointer-events-none absolute inset-x-0 bottom-16 z-10 flex justify-center px-4">
              <div className="rounded-full border border-white/10 bg-[#111713]/75 px-4 py-2 text-center backdrop-blur-md">
                <p className="text-[11px] font-medium text-ivory/85">
                  Click the scene to look around · WASD to walk
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
                  className="rounded-full border border-white/18 bg-[#111713]/75 px-4 py-3 text-[11px] font-semibold text-ivory backdrop-blur-md transition active:scale-95 motion-reduce:transition-none"
                >
                  Jump
                </button>
                <button
                  onClick={resetView}
                  className="rounded-full border border-white/18 bg-[#111713]/75 px-4 py-3 text-[11px] font-semibold text-ivory backdrop-blur-md transition active:scale-95 motion-reduce:transition-none"
                >
                  Reset
                </button>
              </div>
              <p className="pointer-events-none absolute bottom-44 left-4 z-10 w-32 rounded-full bg-[#111713]/60 px-3 py-1 text-center text-[10px] text-ivory/70 backdrop-blur">
                Drag the scene to look around
              </p>
            </>
          ) : (
            <div className="pointer-events-none absolute bottom-3 left-3 z-10 hidden rounded-full border border-white/10 bg-[#111713]/60 px-3 py-1.5 text-[10px] text-ivory/55 backdrop-blur sm:block">
              WASD / arrows move · mouse look · Shift run · Space jump · R reset · Esc release
            </div>
          )}

          <div className="pointer-events-none absolute bottom-3 right-3 z-10 flex items-center gap-2 rounded-full border border-white/10 bg-[#111713]/65 px-2.5 py-1.5 text-[10px] text-ivory/45 backdrop-blur">
            {fps !== null && <span className="tabular-nums">{fps} fps</span>}
            <button
              onClick={() => setHelpOpen((value) => !value)}
              className="pointer-events-auto text-ivory/75 underline decoration-white/30 decoration-dotted underline-offset-2 transition hover:text-ivory motion-reduce:transition-none"
            >
              Scene info
            </button>
          </div>

          {helpOpen && (
            <div className="absolute bottom-12 right-3 z-30 w-80 max-w-[calc(100%-1.5rem)] rounded-[12px] border border-white/10 bg-[#111713]/96 p-4 text-[11px] text-ivory/60 shadow-[0_32px_80px_-32px_rgba(0,0,0,0.95)] backdrop-blur-md">
              <div className="flex items-start justify-between gap-3">
                <p className="font-serif text-[17px] leading-tight text-ivory">
                  About this reconstruction
                </p>
                <button
                  onClick={() => setHelpOpen(false)}
                  className="text-[10px] text-ivory/45 transition hover:text-ivory motion-reduce:transition-none"
                  aria-label="Close scene information"
                >
                  Close
                </button>
              </div>
              <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2">
                <dt className="text-ivory/35">Rooms</dt>
                <dd>{rooms}</dd>
                <dt className="text-ivory/35">Furniture</dt>
                <dd>{furnitureCount} items</dd>
                <dt className="text-ivory/35">Doorways</dt>
                <dd>{doors}</dd>
                <dt className="text-ivory/35">Scale</dt>
                <dd>{approximate ? 'Estimated from plan' : 'Measured dimensions'}</dd>
                <dt className="text-ivory/35">Style</dt>
                <dd className="capitalize">{furnishingStyle.replace(/_/g, ' ')}</dd>
                <dt className="text-ivory/35">Lighting</dt>
                <dd>{LIGHTING_PRESETS[lighting].label}</dd>
                <dt className="text-ivory/35">Input</dt>
                <dd>Floor plan only</dd>
                <dt className="text-ivory/35">Geometry</dt>
                <dd>{job?.geometry_valid ? 'Validated' : 'Not validated'}</dd>
                <dt className="text-ivory/35">Navigation</dt>
                <dd>{job?.navigation_available ? 'All rooms reachable' : 'Partial'}</dd>
                <dt className="text-ivory/35">Current room</dt>
                <dd data-testid="scene-info-room">{currentRoom?.name ?? 'Between rooms'}</dd>
                <dt className="text-ivory/35">Room furniture</dt>
                <dd>
                  {currentRoom
                    ? currentFurniture.length > 0
                      ? currentFurniture.map((item) => item.label).join(', ')
                      : 'None — room left minimal'
                    : '—'}
                </dd>
              </dl>
              {renderStats && (
                <div className="mt-3 border-t border-white/10 pt-2.5">
                  <p className="text-[9px] uppercase tracking-[0.18em] text-ivory/35">Renderer</p>
                  <p className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[10px] tabular-nums text-ivory/55">
                    <span>{renderStats.drawCalls} draw calls</span>
                    <span>{renderStats.triangles.toLocaleString()} triangles</span>
                    <span>{renderStats.textures} textures</span>
                    <span>{renderStats.programs} shaders</span>
                    <span>{renderStats.fps} fps</span>
                  </p>
                </div>
              )}
              {(meta?.limitations?.length ?? 0) > 0 && (
                <div className="mt-3 border-t border-white/10 pt-2.5">
                  <p className="font-medium text-ivory/70">Limitations</p>
                  <ul className="mt-1 list-disc space-y-1 pl-4 leading-4">
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

interface ViewerControlsProps {
  furnitureVisible: boolean
  ceilingVisible: boolean
  quality: Quality
  lighting: LightingPreset
  compact?: boolean
  onFurniture: () => void
  onCeiling: () => void
  onQuality: (quality: Quality) => void
  onLighting: (preset: LightingPreset) => void
  onReset: () => void
  onFullscreen: () => void
}

function ViewerControls({
  furnitureVisible,
  ceilingVisible,
  quality,
  lighting,
  compact = false,
  onFurniture,
  onCeiling,
  onQuality,
  onLighting,
  onReset,
  onFullscreen,
}: ViewerControlsProps) {
  const pill =
    'inline-flex items-center justify-center rounded-full border border-white/12 bg-[#111713]/80 px-3 py-1.5 text-[10.5px] font-medium text-ivory/80 backdrop-blur-md transition hover:border-white/25 hover:text-ivory focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-bronze/60 motion-reduce:transition-none'
  const activePill =
    'inline-flex items-center justify-center rounded-full border border-bronze/60 bg-bronze/20 px-3 py-1.5 text-[10.5px] font-medium text-ivory backdrop-blur-md transition hover:border-bronze/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-bronze/60 motion-reduce:transition-none'
  return (
    <div className={compact ? 'grid grid-cols-2 gap-1.5' : 'flex flex-wrap justify-end gap-1.5'}>
      <div className="col-span-2 flex min-w-[10.5rem] overflow-hidden rounded-full border border-white/12 bg-[#111713]/80 backdrop-blur-md">
        {LIGHTING_OPTIONS.map((option) => (
          <button
            key={option.value}
            onClick={() => onLighting(option.value)}
            aria-pressed={lighting === option.value}
            className={
              lighting === option.value
                ? 'flex-1 bg-bronze/25 px-2 py-1.5 text-[10.5px] font-medium text-ivory'
                : 'flex-1 px-2 py-1.5 text-[10.5px] text-ivory/50 transition hover:text-ivory/85 motion-reduce:transition-none'
            }
          >
            {option.label}
          </button>
        ))}
      </div>
      <button onClick={onFurniture} className={pill} aria-pressed={furnitureVisible}>
        {furnitureVisible ? 'Furniture on' : 'Furniture off'}
      </button>
      <button onClick={onCeiling} className={pill} aria-pressed={ceilingVisible}>
        {ceilingVisible ? 'Ceiling on' : 'Ceiling off'}
      </button>
      <label
        className={`${pill} cursor-pointer gap-1.5`}
        title="Rendering quality"
      >
        <span className="text-ivory/40">Quality</span>
        <select
          value={quality}
          onChange={(event) => onQuality(event.target.value as Quality)}
          className="bg-transparent text-[10.5px] font-medium text-ivory outline-none"
          aria-label="Rendering quality"
        >
          {QUALITY_OPTIONS.map((option) => (
            <option key={option.value} value={option.value} className="text-ink">
              {option.label}
            </option>
          ))}
        </select>
      </label>
      <button onClick={onReset} className={activePill}>
        Reset view
      </button>
      <button onClick={onFullscreen} className={pill}>
        Fullscreen
      </button>
      <a href="/tours" className={pill}>
        Exit
      </a>
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
      className="absolute bottom-4 left-4 z-10 h-32 w-32 touch-none rounded-full border border-white/18 bg-[#111713]/55 backdrop-blur"
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
  lighting: LightingPreset
  isTouch: boolean
  signal: AbortSignal
}

async function buildRuntime(
  container: HTMLDivElement,
  model: THREE.Group,
  bundle: SceneBundle,
  cleanups: (() => void)[],
  options: BuildOptions,
): Promise<Runtime> {
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
  renderer.toneMappingExposure = LIGHTING_PRESETS[options.lighting].exposure
  renderer.shadowMap.enabled = options.quality !== 'low'
  renderer.shadowMap.type = THREE.PCFSoftShadowMap
  renderer.domElement.className = 'block h-full w-full touch-none'
  renderer.domElement.setAttribute('data-testid', 'walkthrough-canvas')
  container.appendChild(renderer.domElement)

  const scene = new THREE.Scene()
  scene.background = null

  const camera = new THREE.PerspectiveCamera(
    options.isTouch ? TOUCH_FOV : DESKTOP_FOV,
    Math.max(1, container.clientWidth) / Math.max(1, container.clientHeight),
    0.06,
    600,
  )
  camera.rotation.order = 'YXZ'

  model.updateMatrixWorld(true)
  const bounds = new THREE.Box3().setFromObject(model)
  const size = bounds.getSize(new THREE.Vector3())
  const center = bounds.getCenter(new THREE.Vector3())
  const wallHeight = collision.wall_height || 2.8
  const footprint = collision.footprint

  const pmrem = new THREE.PMREMGenerator(renderer)
  const roomEnvironment = new RoomEnvironment()
  const environmentTarget = pmrem.fromScene(roomEnvironment, 0.04)
  scene.environment = environmentTarget.texture
  scene.environmentIntensity = LIGHTING_PRESETS[options.lighting].envIntensity
  pmrem.dispose()
  roomEnvironment.dispose()

  const skyUniforms = {
    topColor: { value: new THREE.Color(LIGHTING_PRESETS[options.lighting].skyTop) },
    bottomColor: { value: new THREE.Color(LIGHTING_PRESETS[options.lighting].skyHorizon) },
    offset: { value: 24 },
    exponent: { value: 0.72 },
  }
  const skyMaterial = new THREE.ShaderMaterial({
    uniforms: skyUniforms,
    vertexShader: `
      varying vec3 vWorldPosition;
      void main() {
        vec4 worldPosition = modelMatrix * vec4(position, 1.0);
        vWorldPosition = worldPosition.xyz;
        gl_Position = projectionMatrix * viewMatrix * worldPosition;
      }
    `,
    fragmentShader: `
      uniform vec3 topColor;
      uniform vec3 bottomColor;
      uniform float offset;
      uniform float exponent;
      varying vec3 vWorldPosition;
      void main() {
        float h = normalize(vWorldPosition + vec3(0.0, offset, 0.0)).y;
        vec3 color = mix(bottomColor, topColor, pow(max(h, 0.0), exponent));
        gl_FragColor = vec4(color, 1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }
    `,
    side: THREE.BackSide,
    depthWrite: false,
    fog: false,
  })
  const sky = new THREE.Mesh(new THREE.SphereGeometry(340, 32, 18), skyMaterial)
  sky.position.set(center.x, 0, center.z)
  sky.frustumCulled = false
  sky.renderOrder = -10
  scene.add(sky)

  const ground = new THREE.Mesh(
    new THREE.CircleGeometry(300, 48),
    new THREE.MeshStandardMaterial({
      color: LIGHTING_PRESETS[options.lighting].groundColor,
      roughness: 0.98,
      metalness: 0,
      envMapIntensity: 0.18,
    }),
  )
  ground.rotation.x = -Math.PI / 2
  ground.position.set(center.x, -0.05, center.z)
  ground.receiveShadow = true
  scene.add(ground)

  const hemisphere = new THREE.HemisphereLight(0xffffff, 0x6f6c66, 1)
  scene.add(hemisphere)
  const ambient = new THREE.AmbientLight(0xffffff, 0.16)
  scene.add(ambient)

  const sun = new THREE.DirectionalLight(0xffffff, 2.5)
  sun.castShadow = options.quality !== 'low'
  sun.shadow.bias = -0.00035
  sun.shadow.normalBias = 0.02
  sun.shadow.mapSize.set(options.quality === 'high' ? 2048 : 1024, options.quality === 'high' ? 2048 : 1024)
  const halfSpan = Math.max(size.x, size.z) * 0.78 + 3
  sun.shadow.camera.left = -halfSpan
  sun.shadow.camera.right = halfSpan
  sun.shadow.camera.top = halfSpan
  sun.shadow.camera.bottom = -halfSpan
  sun.shadow.camera.near = 0.5
  sun.shadow.camera.far = Math.max(size.x, size.z) * 3 + 60
  sun.shadow.camera.updateProjectionMatrix()
  sun.target.position.set(center.x, 0, center.z)
  scene.add(sun)
  scene.add(sun.target)

  const fill = new THREE.DirectionalLight(0xdfe8f5, 0.24)
  fill.position.set(center.x + size.x, wallHeight + 3, center.z - size.z)
  fill.target.position.set(center.x, 0, center.z)
  scene.add(fill)
  scene.add(fill.target)

  const lamps: THREE.SpotLight[] = []
  for (const room of collision.rooms) {
    // Wide downlights: a point light a few centimetres below the ceiling
    // produced a blown-out white pool on the plaster. A downward spot keeps
    // the ceiling clean while lighting the floor and furniture below.
    const lamp = new THREE.SpotLight(
      0xffffff,
      12,
      Math.max(9, wallHeight * 3.6),
      Math.PI / 2.7,
      0.85,
      2,
    )
    lamp.position.set(room.center[0], Math.max(2.1, wallHeight - 0.12), room.center[1])
    lamp.target.position.set(room.center[0], 0, room.center[1])
    scene.add(lamp)
    scene.add(lamp.target)
    lamps.push(lamp)
  }

  const maxAnisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy())
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
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material]
    const transparent = materials.some(
      (material) => material && (material as THREE.Material).transparent,
    )
    mesh.castShadow = !isCeiling && !transparent && options.quality !== 'low'
    mesh.receiveShadow = !transparent
    for (const material of materials) {
      if (!material) continue
      const standard = material as THREE.MeshStandardMaterial
      if ('envMapIntensity' in standard) {
        standard.envMapIntensity = 0.85
      }
      const record = standard as unknown as Record<string, unknown>
      for (const key of ['map', 'normalMap', 'roughnessMap', 'metalnessMap', 'emissiveMap', 'aoMap']) {
        const texture = record[key]
        if (texture instanceof THREE.Texture) {
          texture.anisotropy = maxAnisotropy
          texture.needsUpdate = true
        }
      }
      material.needsUpdate = true
    }
  })
  scene.add(model)

  const wallCollision: WallBox[] = collision.walls
  const furnitureCollision: WallBox[] = (collision.furniture ?? []).map((item) => ({
    min: item.min,
    max: item.max,
  }))
  const furnitureRoots = findFurnitureRoots(model)
  const roomAreas: RoomArea[] = (bundle.room_graph?.nodes ?? []).map((node) => ({
    id: node.id,
    center: node.center,
    bounds: node.bounds,
    polygon: node.polygon,
  }))

  const shadowTexture = contactShadowTexture()
  const shadowGeometry = new THREE.PlaneGeometry(1, 1)
  shadowGeometry.rotateX(-Math.PI / 2)
  const shadowMaterial = new THREE.MeshBasicMaterial({
    color: 0x000000,
    alphaMap: shadowTexture,
    transparent: true,
    opacity: 0.42,
    depthWrite: false,
    polygonOffset: true,
    polygonOffsetFactor: -1,
    toneMapped: false,
  })
  const contactItems: { x: number; z: number; sx: number; sz: number }[] = []
  for (const root of furnitureRoots) {
    const box = new THREE.Box3().setFromObject(root)
    if (box.isEmpty()) continue
    const itemSize = box.getSize(new THREE.Vector3())
    if (itemSize.x < 0.18 || itemSize.z < 0.18) continue
    contactItems.push({
      x: (box.min.x + box.max.x) / 2,
      z: (box.min.z + box.max.z) / 2,
      sx: itemSize.x * 0.94,
      sz: itemSize.z * 0.94,
    })
  }
  let contactShadows: THREE.InstancedMesh | null = null
  if (contactItems.length > 0) {
    contactShadows = new THREE.InstancedMesh(shadowGeometry, shadowMaterial, contactItems.length)
    const matrix = new THREE.Matrix4()
    const quaternion = new THREE.Quaternion()
    const scale = new THREE.Vector3()
    const position = new THREE.Vector3()
    contactItems.forEach((item, index) => {
      position.set(item.x, 0.014, item.z)
      scale.set(item.sx, 1, item.sz)
      matrix.compose(position, quaternion, scale)
      contactShadows?.setMatrixAt(index, matrix)
    })
    contactShadows.instanceMatrix.needsUpdate = true
    contactShadows.renderOrder = 2
    contactShadows.frustumCulled = false
    scene.add(contactShadows)
  }

  const rawSpawn = {
    x: collision.spawn.position[0],
    z: collision.spawn.position[2],
  }
  const safeSpawn = resolveSpawn(rawSpawn, wallCollision, footprint)
  const spawnPosition = new THREE.Vector3(safeSpawn.x, EYE_HEIGHT, safeSpawn.z)
  const spawnRoom = roomAtPoint(safeSpawn.x, safeSpawn.z, roomAreas)
  const fallbackYaw =
    Math.abs(safeSpawn.x - rawSpawn.x) + Math.abs(safeSpawn.z - rawSpawn.z) > 0.05
      ? longestSightYaw(
          safeSpawn.x,
          safeSpawn.z,
          spawnRoom,
          roomAreas,
          wallCollision,
          collision.spawn.yaw,
        )
      : collision.spawn.yaw
  const focalView = primaryFurnitureView(
    safeSpawn.x,
    safeSpawn.z,
    spawnRoom,
    bundle.furniture ?? [],
    wallCollision,
  )
  // The spawn keeps the room's long view; turning the full 90 degrees towards
  // the sofa fills the frame with upholstery and hides the floor plan's depth.
  const spawnYaw = focalView ? blendAngle(fallbackYaw, focalView.yaw, 0.35) : fallbackYaw
  const spawnPitch = options.isTouch ? TOUCH_SPAWN_PITCH : DESKTOP_SPAWN_PITCH
  const state = {
    position: spawnPosition.clone(),
    velocity: new THREE.Vector2(),
    yaw: spawnYaw,
    pitch: spawnPitch,
    velocityY: 0,
    jumpQueued: false,
    currentRoomId: null as number | null,
    lightingPreset: options.lighting,
  }

  const applySun = (preset: LightingPreset) => {
    const config = LIGHTING_PRESETS[preset]
    const direction = new THREE.Vector3(...config.sunDirection).normalize()
    const distance = Math.max(size.x, size.z) * 1.6 + 16
    sun.position.set(
      center.x + direction.x * distance,
      Math.max(wallHeight + 5, direction.y * distance),
      center.z + direction.z * distance,
    )
    sun.color.setHex(config.sunColor)
    sun.intensity = config.sunIntensity
    sun.target.position.set(center.x, 0, center.z)
    sun.target.updateMatrixWorld()
  }

  const applyLighting = (preset: LightingPreset) => {
    const config = LIGHTING_PRESETS[preset]
    state.lightingPreset = preset
    renderer.toneMappingExposure = config.exposure
    scene.environmentIntensity = config.envIntensity
    hemisphere.color.setHex(config.hemiSky)
    hemisphere.groundColor.setHex(config.hemiGround)
    hemisphere.intensity = config.hemiIntensity
    ambient.intensity = config.ambient
    fill.intensity = config.fill
    fill.color.setHex(preset === 'evening' ? 0x8fa0c4 : 0xdfe8f5)
    sun.castShadow = options.quality !== 'low'
    applySun(preset)
    for (const lamp of lamps) {
      lamp.color.setHex(config.lampColor)
      lamp.intensity = config.lampIntensity
    }
    skyUniforms.topColor.value.setHex(config.skyTop)
    skyUniforms.bottomColor.value.setHex(config.skyHorizon)
    ;(ground.material as THREE.MeshStandardMaterial).color.setHex(config.groundColor)
  }

  const applyQuality = (quality: Quality) => {
    renderer.setPixelRatio(qualityPixelRatio(quality))
    renderer.setSize(container.clientWidth, container.clientHeight, false)
    const shadows = quality !== 'low'
    renderer.shadowMap.enabled = shadows
    sun.castShadow = shadows
    const shadowSize = quality === 'high' ? 2048 : 1024
    if (sun.shadow.mapSize.x !== shadowSize) {
      sun.shadow.mapSize.set(shadowSize, shadowSize)
      sun.shadow.map?.dispose()
      sun.shadow.map = null
      renderer.shadowMap.needsUpdate = true
    }
    model.traverse((object) => {
      const mesh = object as THREE.Mesh
      if (!mesh.isMesh) return
      const isCeiling = ceilingMeshes.has(object)
      const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material]
      const transparent = materials.some(
        (material) => material && (material as THREE.Material).transparent,
      )
      mesh.castShadow = shadows && !isCeiling && !transparent
      mesh.receiveShadow = !transparent
    })
    renderer.shadowMap.needsUpdate = true
  }

  const teleportToRoom = (roomId: number): boolean => {
    const room = roomAreas.find((entry) => entry.id === roomId)
    if (!room) return false
    const [cx, cz] = room.center
    const inRoom = (x: number, z: number) =>
      roomAtPoint(x, z, roomAreas) === roomId || !room.polygon || room.polygon.length < 3
    const free = (x: number, z: number) =>
      !isBlocked(x, z, PLAYER_RADIUS, runtime.walls) &&
      inRoom(x, z) &&
      (!footprint ||
        (x >= PLAYER_RADIUS &&
          z >= PLAYER_RADIUS &&
          x <= footprint.width - PLAYER_RADIUS &&
          z <= footprint.height - PLAYER_RADIUS))
    const items = bundle.furniture ?? []
    const focalTarget = primaryFurnitureTarget(roomId, items)
    let target: Point2 | null = null
    let view: FocalView | null = null
    if (focalTarget) {
      // Standing at the room centre can leave the camera half a metre from the bed.
      // Sample the room for a free spot ~2.9 m back with a clear line of sight.
      const [bx, bz, bw, bd] = room.bounds
      const margin = PLAYER_RADIUS + 0.12
      const minX = bx + margin
      const minZ = bz + margin
      const maxX = bx + bw - margin
      const maxZ = bz + bd - margin
      if (maxX > minX && maxZ > minZ) {
        const steps = 16
        let bestScore = -Infinity
        for (let ix = 0; ix <= steps; ix += 1) {
          const x = minX + ((maxX - minX) * ix) / steps
          for (let iz = 0; iz <= steps; iz += 1) {
            const z = minZ + ((maxZ - minZ) * iz) / steps
            if (!free(x, z)) continue
            const distance = Math.hypot(
              focalTarget.position[0] - x,
              focalTarget.position[2] - z,
            )
            if (distance < 1.5) continue
            const candidate = viewToward(x, z, focalTarget, runtime.wallCollision)
            if (!candidate) continue
            const score =
              -Math.abs(distance - 2.9) * 1.2 - Math.hypot(x - cx, z - cz) * 0.12
            if (score > bestScore) {
              bestScore = score
              target = { x, z }
              view = candidate
            }
          }
        }
      }
    }
    if (!target) target = free(cx, cz) ? { x: cx, z: cz } : null
    if (!target) {
      for (let ring = 1; ring <= 14 && !target; ring += 1) {
        const radius = ring * 0.24
        for (let index = 0; index < 20; index += 1) {
          const angle = (index / 20) * Math.PI * 2
          const x = cx + Math.cos(angle) * radius
          const z = cz + Math.sin(angle) * radius
          if (free(x, z)) {
            target = { x, z }
            break
          }
        }
      }
    }
    if (!target) return false
    state.position.set(target.x, EYE_HEIGHT, target.z)
    state.velocity.set(0, 0)
    state.velocityY = 0
    const fallback = longestSightYaw(
      target.x,
      target.z,
      roomId,
      roomAreas,
      runtime.wallCollision,
      state.yaw,
    )
    const focal =
      view ??
      primaryFurnitureView(target.x, target.z, roomId, items, runtime.wallCollision)
    state.pitch = focal?.pitch ?? spawnPitch
    state.yaw = focal?.yaw ?? fallback
    return true
  }

  applyLighting(options.lighting)

  let currentQuality: Quality = options.quality
  let lastFps = 0
  const setQuality = (next: Quality) => {
    currentQuality = next
    applyQuality(next)
  }

  const runtime: Runtime = {
    renderer,
    scene,
    camera,
    ceilings,
    position: state.position,
    velocity: state.velocity,
    get yaw() {
      return state.yaw
    },
    set yaw(value: number) {
      state.yaw = value
    },
    get pitch() {
      return state.pitch
    },
    set pitch(value: number) {
      state.pitch = value
    },
    get velocityY() {
      return state.velocityY
    },
    set velocityY(value: number) {
      state.velocityY = value
    },
    keys: new Set<string>(),
    get jumpQueued() {
      return state.jumpQueued
    },
    set jumpQueued(value: boolean) {
      state.jumpQueued = value
    },
    walls: [...wallCollision, ...furnitureCollision],
    wallCollision,
    furnitureCollision,
    furnitureVisible: true,
    furnitureCollisionPending: false,
    footprint,
    spawn: { position: spawnPosition.clone(), yaw: spawnYaw },
    get currentRoomId() {
      return state.currentRoomId
    },
    set currentRoomId(value: number | null) {
      state.currentRoomId = value
    },
    get lightingPreset() {
      return state.lightingPreset
    },
    set lightingPreset(value: LightingPreset) {
      state.lightingPreset = value
    },
    frame: 0,
    setFurnitureVisible: (visible: boolean) => {
      runtime.furnitureVisible = visible
      for (const node of furnitureRoots) node.visible = visible
      if (contactShadows) contactShadows.visible = visible
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
    setLightingPreset: applyLighting,
    setQuality,
    teleportToRoom,
    getStats: () => ({
      drawCalls: renderer.info.render.calls,
      triangles: renderer.info.render.triangles,
      textures: renderer.info.memory.textures,
      geometries: renderer.info.memory.geometries,
      programs: renderer.info.programs?.length ?? 0,
      fps: lastFps,
      quality: currentQuality,
      lighting: state.lightingPreset,
    }),
    reset: () => {
      runtime.position.copy(runtime.spawn.position)
      runtime.yaw = runtime.spawn.yaw
      runtime.pitch = spawnPitch
      runtime.velocity.set(0, 0)
      runtime.velocityY = 0
      runtime.jumpQueued = false
    },
    dispose: () => {
      cancelAnimationFrame(runtime.frame)
      scene.environment = null
      environmentTarget.dispose()
      sky.geometry.dispose()
      skyMaterial.dispose()
      ground.geometry.dispose()
      ;(ground.material as THREE.Material).dispose()
      shadowGeometry.dispose()
      shadowMaterial.dispose()
      shadowTexture.dispose()
      const disposedTextures = new Set<THREE.Texture>()
      renderer.dispose()
      renderer.domElement.remove()
      scene.traverse((object) => {
        const mesh = object as THREE.Mesh
        if (mesh.geometry) mesh.geometry.dispose()
        const materials = Array.isArray(mesh.material)
          ? mesh.material
          : mesh.material
            ? [mesh.material]
            : []
        for (const material of materials) {
          for (const value of Object.values(material)) {
            if (value instanceof THREE.Texture && !disposedTextures.has(value)) {
              disposedTextures.add(value)
              value.dispose()
            }
          }
          material.dispose()
        }
      })
      scene.clear()
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
          lighting: LightingPreset
          quality: Quality
        }
        getCollision: () => {
          walls: WallBox[]
          furnitureVisible: boolean
          footprint?: Footprint
        }
        getStats: () => RenderStats
        teleport: (roomId: number) => boolean
        blockedAt: (x: number, z: number) => boolean
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
        lighting: state.lightingPreset,
        quality: currentQuality,
      }),
      getCollision: () => ({
        walls: runtime.walls,
        furnitureVisible: runtime.furnitureVisible,
        footprint: runtime.footprint,
      }),
      getStats: runtime.getStats,
      teleport: teleportToRoom,
      blockedAt: (x: number, z: number) => isBlocked(x, z, PLAYER_RADIUS, runtime.walls),
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
    const next = applyLook(runtime.yaw, runtime.pitch, event.movementX, event.movementY, 0.002)
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
        0.0042,
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
  try {
    await renderer.compileAsync(scene, camera)
  } catch {
    // Shader pre-compilation is an optimisation; rendering continues if it is unavailable.
  }
  if (options.signal.aborted) {
    runtime.dispose()
    throw new Error('The walkthrough was closed before it finished loading.')
  }

  let lastTime = performance.now()
  let frameCount = 0
  let fpsWindow = lastTime
  let lastFpsTime = lastTime
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

    let targetX = 0
    let targetZ = 0
    if (forward !== 0 || strafe !== 0) {
      const running = runtime.keys.has('ShiftLeft') || runtime.keys.has('ShiftRight')
      const speed = running ? RUN_SPEED : WALK_SPEED
      const direction = movementVector(forward, strafe, runtime.yaw)
      targetX = direction.x * speed
      targetZ = direction.z * speed
    }
    const response = forward !== 0 || strafe !== 0 ? ACCELERATION : DECELERATION
    runtime.velocity.x = THREE.MathUtils.damp(runtime.velocity.x, targetX, response, delta)
    runtime.velocity.y = THREE.MathUtils.damp(runtime.velocity.y, targetZ, response, delta)
    if (Math.abs(runtime.velocity.x) > 0.001 || Math.abs(runtime.velocity.y) > 0.001) {
      const moved: Point2 = moveWithCollision(
        { x: runtime.position.x, z: runtime.position.z },
        { x: runtime.velocity.x * delta, z: runtime.velocity.y * delta },
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
    const ceilingLimit = Math.max(EYE_HEIGHT, wallHeight - 0.12)
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
      lastFps = Math.round((frameCount * 1000) / (now - fpsWindow))
      lastFpsTime = now
      options.onFps(lastFps)
      frameCount = 0
      fpsWindow = now
    } else if (now - lastFpsTime > 4000) {
      lastFps = 0
    }
  }
  runtime.frame = requestAnimationFrame(animate)

  return runtime
}
