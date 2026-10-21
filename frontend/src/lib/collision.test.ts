import { describe, expect, it } from 'vitest'
import {
  applyLook,
  findSpawn,
  isBlocked,
  MAX_SUBSTEP,
  moveWithCollision,
  movementVector,
  PITCH_LIMIT,
  PLAYER_RADIUS,
  roomAtPoint,
  type RoomArea,
  type WallBox,
} from './collision'

const perimeter: WallBox[] = [
  { min: [0, 0], max: [10, 0.1] },
  { min: [0, 7.9], max: [10, 8] },
  { min: [0, 0], max: [0.1, 8] },
  { min: [9.9, 0], max: [10, 8] },
]

describe('movementVector', () => {
  it('maps forward to negative Z at yaw 0', () => {
    const vector = movementVector(1, 0, 0)
    expect(vector.x).toBeCloseTo(0)
    expect(vector.z).toBeCloseTo(-1)
  })

  it('maps forward to negative X at yaw π/2', () => {
    const vector = movementVector(1, 0, Math.PI / 2)
    expect(vector.x).toBeCloseTo(-1)
    expect(vector.z).toBeCloseTo(0)
  })

  it('maps backward and right strafe correctly', () => {
    const back = movementVector(-1, 0, 0)
    expect(back.z).toBeCloseTo(1)
    const right = movementVector(0, 1, 0)
    expect(right.x).toBeCloseTo(1)
    expect(right.z).toBeCloseTo(0)
  })

  it('normalises diagonal input to unit length', () => {
    const vector = movementVector(1, 1, 0)
    expect(Math.hypot(vector.x, vector.z)).toBeCloseTo(1)
  })

  it('preserves analog magnitudes below 1', () => {
    const vector = movementVector(0.5, 0, 0)
    expect(Math.hypot(vector.x, vector.z)).toBeCloseTo(0.5)
  })

  it('returns zero for no input', () => {
    expect(movementVector(0, 0, 1.2)).toEqual({ x: 0, z: 0 })
  })
})

describe('isBlocked', () => {
  const wall: WallBox = { min: [2, 2], max: [4, 4] }

  it('detects points inside the volume', () => {
    expect(isBlocked(3, 3, PLAYER_RADIUS, [wall])).toBe(true)
  })

  it('detects points within the player radius', () => {
    expect(isBlocked(4.2, 3, PLAYER_RADIUS, [wall])).toBe(true)
  })

  it('allows points outside the radius', () => {
    expect(isBlocked(4.5, 3, PLAYER_RADIUS, [wall])).toBe(false)
  })

  it('is not blocked when farther than the radius diagonally', () => {
    expect(isBlocked(4.2, 4.2, PLAYER_RADIUS, [wall])).toBe(false)
  })
})

describe('moveWithCollision', () => {
  const wall: WallBox = { min: [4, 0], max: [4.2, 8] }

  it('moves freely with no obstacles', () => {
    const result = moveWithCollision({ x: 1, z: 1 }, { x: 1, z: 1 }, PLAYER_RADIUS, [])
    expect(result.x).toBeCloseTo(2)
    expect(result.z).toBeCloseTo(2)
  })

  it('stops the player at the wall face', () => {
    const result = moveWithCollision({ x: 3, z: 4 }, { x: 5, z: 0 }, PLAYER_RADIUS, [wall])
    expect(result.x).toBeLessThanOrEqual(4 - PLAYER_RADIUS + 1e-6)
    expect(result.x).toBeGreaterThan(3)
  })

  it('cannot tunnel through a thin wall in one large step', () => {
    const result = moveWithCollision({ x: 3, z: 4 }, { x: 5, z: 0 }, PLAYER_RADIUS, [wall])
    expect(result.x).toBeLessThan(4)
    expect(Math.abs(5) / MAX_SUBSTEP).toBeGreaterThan(1)
  })

  it('slides along a wall instead of sticking', () => {
    const horizontal: WallBox = { min: [0, 2], max: [10, 2.2] }
    const result = moveWithCollision(
      { x: 5, z: 1 },
      { x: 1, z: 1 },
      PLAYER_RADIUS,
      [horizontal],
    )
    expect(result.x).toBeCloseTo(6)
    expect(result.z).toBeLessThan(2 - PLAYER_RADIUS + 1e-6)
  })

  it('passes through a doorway gap', () => {
    const doorway: WallBox[] = [
      { min: [4.9, 0], max: [5.1, 3.5] },
      { min: [4.9, 4.5], max: [5.1, 8] },
    ]
    const result = moveWithCollision({ x: 4, z: 4 }, { x: 2, z: 0 }, PLAYER_RADIUS, doorway)
    expect(result.x).toBeCloseTo(6)
  })

  it('stops at a gap that is narrower than the player', () => {
    const narrow: WallBox[] = [
      { min: [4.9, 0], max: [5.1, 3.9] },
      { min: [4.9, 4.1], max: [5.1, 8] },
    ]
    const result = moveWithCollision({ x: 4, z: 4 }, { x: 2, z: 0 }, PLAYER_RADIUS, narrow)
    expect(result.x).toBeLessThan(5)
  })

  it('clamps the player inside the footprint', () => {
    const result = moveWithCollision(
      { x: 5, z: 4 },
      { x: 50, z: -50 },
      PLAYER_RADIUS,
      perimeter,
      { width: 10, height: 8 },
    )
    expect(result.x).toBeLessThanOrEqual(10 - PLAYER_RADIUS - 0.02 + 1e-6)
    expect(result.z).toBeGreaterThanOrEqual(PLAYER_RADIUS + 0.02 - 1e-6)
  })

  it('keeps the player out of perimeter walls', () => {
    const start = { x: 5, z: 4 }
    let position = start
    for (let index = 0; index < 200; index += 1) {
      position = moveWithCollision(position, { x: 0, z: -0.5 }, PLAYER_RADIUS, perimeter, {
        width: 10,
        height: 8,
      })
    }
    expect(isBlocked(position.x, position.z, PLAYER_RADIUS, perimeter)).toBe(false)
    expect(position.z).toBeGreaterThan(0.1)
  })
})

describe('applyLook', () => {
  it('turns yaw with horizontal movement', () => {
    expect(applyLook(0, 0, 100, 0).yaw).toBeLessThan(0)
    expect(applyLook(0, 0, -100, 0).yaw).toBeGreaterThan(0)
  })

  it('clamps pitch to the limit', () => {
    expect(applyLook(0, 0, 0, -10000).pitch).toBeCloseTo(PITCH_LIMIT)
    expect(applyLook(0, 0, 0, 10000).pitch).toBeCloseTo(-PITCH_LIMIT)
  })

  it('scales with sensitivity', () => {
    const normal = applyLook(0, 0, 10, 0, 0.002)
    const sensitive = applyLook(0, 0, 10, 0, 0.004)
    expect(sensitive.yaw).toBeCloseTo(normal.yaw * 2)
  })
})

describe('roomAtPoint', () => {
  const rooms: RoomArea[] = [
    { id: 0, center: [3, 3], bounds: [0, 0, 6, 6] },
    { id: 1, center: [9, 3], bounds: [6.2, 0, 5.8, 6] },
  ]

  it('returns the room containing the point', () => {
    expect(roomAtPoint(2, 2, rooms)).toBe(0)
    expect(roomAtPoint(8, 4, rooms)).toBe(1)
  })

  it('returns null in a doorway gap or outside the footprint', () => {
    expect(roomAtPoint(6.1, 3, rooms)).toBeNull()
    expect(roomAtPoint(20, 20, rooms)).toBeNull()
  })

  it('prefers an exact polygon over a rectangular bounds approximation', () => {
    const lShaped: RoomArea[] = [
      {
        id: 7,
        center: [2, 2],
        bounds: [0, 0, 4, 4],
        polygon: [
          [0, 0],
          [2, 0],
          [2, 2],
          [4, 2],
          [4, 4],
          [0, 4],
        ],
      },
    ]
    expect(roomAtPoint(1, 1, lShaped)).toBe(7)
    expect(roomAtPoint(3.5, 1, lShaped)).toBeNull()
    expect(roomAtPoint(3.5, 3.5, lShaped)).toBe(7)
  })

  it('does not treat falsy room id 0 as missing', () => {
    expect(roomAtPoint(1, 1, [{ id: 0, center: [1, 1], bounds: [0, 0, 2, 2] }])).toBe(0)
  })
})

describe('findSpawn', () => {
  it('returns a walkable point near the middle of the footprint', () => {
    const spawn = findSpawn(perimeter, { width: 10, height: 8 })
    expect(isBlocked(spawn.x, spawn.z, PLAYER_RADIUS, perimeter)).toBe(false)
    expect(spawn).toEqual({ x: 5, z: 4 })
  })

  it('falls back to another walkable cell when the centre is blocked', () => {
    const blocked: WallBox[] = [...perimeter, { min: [3, 2], max: [7, 6] }]
    const spawn = findSpawn(blocked, { width: 10, height: 8 })
    expect(isBlocked(spawn.x, spawn.z, PLAYER_RADIUS, blocked)).toBe(false)
  })
})
