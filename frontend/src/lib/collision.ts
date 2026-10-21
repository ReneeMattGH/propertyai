export interface WallBox {
  min: [number, number]
  max: [number, number]
  exterior?: boolean
}

export interface Point2 {
  x: number
  z: number
}

export interface Footprint {
  width: number
  height: number
}

export interface RoomArea {
  id: number
  center: [number, number]
  bounds: [number, number, number, number]
  polygon?: [number, number][]
}

export const PLAYER_RADIUS = 0.24
export const MAX_SUBSTEP = 0.08
export const PITCH_LIMIT = Math.PI / 2 - 0.08

export function isBlocked(
  x: number,
  z: number,
  radius: number,
  walls: WallBox[],
): boolean {
  for (const wall of walls) {
    const closestX = Math.max(wall.min[0], Math.min(x, wall.max[0]))
    const closestZ = Math.max(wall.min[1], Math.min(z, wall.max[1]))
    const dx = x - closestX
    const dz = z - closestZ
    if (dx * dx + dz * dz < radius * radius) {
      return true
    }
  }
  return false
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

export function movementVector(
  forward: number,
  strafe: number,
  yaw: number,
): Point2 {
  const magnitude = Math.hypot(forward, strafe)
  if (magnitude < 1e-6) {
    return { x: 0, z: 0 }
  }
  const f = forward / Math.max(1, magnitude)
  const s = strafe / Math.max(1, magnitude)
  const sin = Math.sin(yaw)
  const cos = Math.cos(yaw)
  return {
    x: f * -sin + s * cos,
    z: f * -cos + s * -sin,
  }
}

export function moveWithCollision(
  position: Point2,
  delta: Point2,
  radius: number,
  walls: WallBox[],
  footprint?: Footprint,
): Point2 {
  let { x, z } = position
  const distance = Math.hypot(delta.x, delta.z)
  const steps = Math.max(1, Math.ceil(distance / MAX_SUBSTEP))
  const stepX = delta.x / steps
  const stepZ = delta.z / steps

  for (let step = 0; step < steps; step += 1) {
    const nextX = x + stepX
    if (!isBlocked(nextX, z, radius, walls)) {
      x = nextX
    }
    const nextZ = z + stepZ
    if (!isBlocked(x, nextZ, radius, walls)) {
      z = nextZ
    }
    if (footprint) {
      x = clamp(x, radius + 0.02, footprint.width - radius - 0.02)
      z = clamp(z, radius + 0.02, footprint.height - radius - 0.02)
    }
  }

  return { x, z }
}

export function applyLook(
  yaw: number,
  pitch: number,
  deltaX: number,
  deltaY: number,
  sensitivity = 0.0022,
): { yaw: number; pitch: number } {
  return {
    yaw: yaw - deltaX * sensitivity,
    pitch: clamp(pitch - deltaY * sensitivity, -PITCH_LIMIT, PITCH_LIMIT),
  }
}

export function roomAtPoint(
  x: number,
  z: number,
  rooms: RoomArea[],
): number | null {
  const insidePolygon = (room: RoomArea): boolean => {
    const polygon = room.polygon
    if (!polygon || polygon.length < 3) return false
    let inside = false
    for (let index = 0, previous = polygon.length - 1; index < polygon.length; previous = index++) {
      const [xi, zi] = polygon[index]
      const [xj, zj] = polygon[previous]
      if (zi > z !== zj > z && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) {
        inside = !inside
      }
    }
    return inside
  }
  for (const room of rooms) {
    if (insidePolygon(room)) return room.id
  }
  for (const room of rooms) {
    if (room.polygon && room.polygon.length >= 3) continue
    const [roomX, roomZ, width, depth] = room.bounds
    if (x >= roomX && x <= roomX + width && z >= roomZ && z <= roomZ + depth) {
      return room.id
    }
  }
  return null
}

export function findSpawn(
  walls: WallBox[],
  footprint?: Footprint,
  radius = PLAYER_RADIUS,
): Point2 {
  const width = footprint?.width ?? 10
  const height = footprint?.height ?? 8
  const candidates: Point2[] = [{ x: width / 2, z: height / 2 }]
  const margin = radius + 0.4
  for (let gx = 0; gx <= 6; gx += 1) {
    for (let gz = 0; gz <= 6; gz += 1) {
      candidates.push({
        x: margin + ((width - margin * 2) * gx) / 6,
        z: margin + ((height - margin * 2) * gz) / 6,
      })
    }
  }
  return (
    candidates.find((candidate) => !isBlocked(candidate.x, candidate.z, radius, walls)) ?? {
      x: width / 2,
      z: height / 2,
    }
  )
}
