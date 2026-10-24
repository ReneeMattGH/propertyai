"""Deterministic, room-aware furniture placement.

Placement is driven entirely by the parsed floor plan: room polygons, wall
positions, doorway locations and room types.  Furniture is anchored to real
walls, validated against the room outline, kept clear of every doorway, and
after each collision-bearing piece is added the room is re-checked with a
walkability grid so the only route between doors can never be blocked.

The planner is deterministic: the same rooms, room types, style and variant
always produce the same layout.  No random sampling and no generative model is
involved.
"""
from __future__ import annotations

import math
from collections import deque
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from shapely import contains_xy
from shapely.geometry import Polygon as ShapelyPolygon, box as shapely_box

from furniture_assets import (
    ASSETS,
    STYLES,
    STYLE_LABELS,
    asset_spec,
    asset_visual_bounds,
    build_parts,
    material_config,
)

PLAYER_RADIUS = 0.24
WALL_GAP = 0.02
CEILING_HEIGHT = 2.8
DOOR_CLEAR_DEPTH = 0.85
DOOR_SIDE_MARGIN = 0.25
WALK_GRID_M = 0.1
SIDE_ORDER = ("north", "east", "south", "west")
OPPOSITE_SIDE = {"north": "south", "south": "north", "east": "west", "west": "east"}
SIDE_ROTATION = {"north": 0, "east": 270, "south": 180, "west": 90}
WALL_DECOR_HEIGHT = {
    "wall_mirror": 1.6,
    "art_frame": 1.45,
    "towel_rail": 1.2,
}

Rect = Dict[str, List[float]]


@lru_cache(maxsize=None)
def _asset_materials(asset_id: str) -> Tuple[str, ...]:
    return tuple(sorted({part.material for part in build_parts(asset_id, "modern")}))


def _rect(min_x: float, min_z: float, max_x: float, max_z: float) -> Rect:
    return {"min": [round(min_x, 4), round(min_z, 4)], "max": [round(max_x, 4), round(max_z, 4)]}


def _rect_from_center(cx: float, cz: float, width: float, depth: float) -> Rect:
    return _rect(cx - width / 2, cz - depth / 2, cx + width / 2, cz + depth / 2)


def _rect_overlap(a: Rect, b: Rect, pad: float = 0.0) -> bool:
    return (
        a["min"][0] - pad < b["max"][0]
        and a["max"][0] + pad > b["min"][0]
        and a["min"][1] - pad < b["max"][1]
        and a["max"][1] + pad > b["min"][1]
    )


def _room_polygon(room: Dict) -> ShapelyPolygon:
    points = room.get("polygon_m") or []
    if len(points) < 3:
        x, z, w, d = room["bounds_m"]
        points = [[x, z], [x + w, z], [x + w, z + d], [x, z + d]]
    polygon = ShapelyPolygon([(float(px), float(pz)) for px, pz in points])
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    if polygon.is_empty:
        x, z, w, d = room["bounds_m"]
        polygon = shapely_box(x, z, x + w, z + d)
    return polygon


def _visual_offsets(
    asset_id: str, rotation_deg: int
) -> Tuple[float, float, float, float]:
    """Rotated visual AABB offsets (min_x, min_z, max_x, max_z) from the item origin."""
    rotation_deg = int(rotation_deg) % 360
    min_x, _, min_z, max_x, _, max_z = asset_visual_bounds(asset_id)
    if rotation_deg == 0:
        return min_x, min_z, max_x, max_z
    corners = [
        _rotate_offset(x, z, rotation_deg)
        for x in (min_x, max_x)
        for z in (min_z, max_z)
    ]
    xs = [point[0] for point in corners]
    zs = [point[1] for point in corners]
    return min(xs), min(zs), max(xs), max(zs)


def _world_size(asset_id: str, rotation_deg: int) -> Tuple[float, float, float]:
    min_x, min_z, max_x, max_z = _visual_offsets(asset_id, rotation_deg)
    _, min_y, _, _, max_y, _ = asset_visual_bounds(asset_id)
    return max_x - min_x, max_y - min_y, max_z - min_z


def _rotation_vector(rotation_deg: int) -> Tuple[float, float]:
    radians = math.radians(rotation_deg)
    return math.sin(radians), math.cos(radians)


def _rotate_offset(local_x: float, local_z: float, rotation_deg: int) -> Tuple[float, float]:
    radians = math.radians(rotation_deg)
    return (
        local_x * math.cos(radians) + local_z * math.sin(radians),
        -local_x * math.sin(radians) + local_z * math.cos(radians),
    )


def door_clearance_zones(doorways: Sequence[Dict]) -> List[Dict]:
    """Doorway approach rectangles; no furniture may occupy these on either side."""
    zones: List[Dict] = []
    for door in doorways:
        # Parsed plans distinguish swung doors ("door") from plain wall
        # openings ("opening"); both are walkable and need the same clearance.
        if str(door.get("kind") or "door") not in {
            "door",
            "opening",
            "passage",
            "archway",
            "manual_door",
        }:
            continue
        orientation = door.get("orientation")
        center = door.get("center_m") or [0.0, 0.0]
        width = float(door.get("width_m") or 0.9)
        width = max(0.6, min(1.4, width))
        if orientation == "horizontal":
            rect = _rect(
                center[0] - width / 2 - DOOR_SIDE_MARGIN,
                center[1] - DOOR_CLEAR_DEPTH,
                center[0] + width / 2 + DOOR_SIDE_MARGIN,
                center[1] + DOOR_CLEAR_DEPTH,
            )
        else:
            rect = _rect(
                center[0] - DOOR_CLEAR_DEPTH,
                center[1] - width / 2 - DOOR_SIDE_MARGIN,
                center[0] + DOOR_CLEAR_DEPTH,
                center[1] + width / 2 + DOOR_SIDE_MARGIN,
            )
        rect["door_id"] = door.get("id")
        zones.append(rect)
    return zones


def window_protection_zones(windows: Sequence[Dict], room_id: Optional[int] = None) -> List[Rect]:
    """Strip in front of each window that tall furniture must not cover."""
    zones: List[Rect] = []
    for window in windows:
        if room_id is not None and window.get("room") != room_id:
            continue
        orientation = window.get("orientation")
        center = window.get("center_m") or [0.0, 0.0]
        width = max(0.3, float(window.get("width_m") or 1.0))
        if orientation == "horizontal":
            zones.append(
                _rect(center[0] - width / 2, center[1] - 0.2, center[0] + width / 2, center[1] + 0.2)
            )
        elif orientation == "vertical":
            zones.append(
                _rect(center[0] - 0.2, center[1] - width / 2, center[0] + 0.2, center[1] + width / 2)
            )
    return zones


class RoomLayout:
    """Mutable placement state for one room."""

    def __init__(
        self,
        room: Dict,
        room_type: str,
        name: str,
        doorways: Sequence[Dict],
        variant: int,
        spawn_room_id: Optional[int],
        style: str,
        windows: Sequence[Dict] = (),
    ):
        self.room = room
        self.room_id = int(room["id"])
        self.room_type = room_type
        self.name = name
        self.style = style
        self.variant = variant
        self.polygon = _room_polygon(room)
        # The parser rounds bounds and polygon separately; use the polygon's own
        # extent so wall-anchored footprints sit against the real room edge.
        min_x, min_z, max_x, max_z = self.polygon.bounds
        self.inner = self.polygon
        x, z, w, d = min_x, min_z, max_x - min_x, max_z - min_z
        self.bounds = (x, z, w, d)
        self.center = (x + w / 2, z + d / 2)
        self.room_doors = [
            door
            for door in doorways
            if self.room_id in [rid for rid in (door.get("rooms") or []) if rid is not None]
        ]
        # Only this room's doors reserve circulation space here; clearance
        # rectangles for neighbouring rooms' doors would otherwise eat into
        # the walls and leave no space for required furniture.
        self.door_zones = [
            zone
            for zone in door_clearance_zones(self.room_doors)
            if _rect_overlap(zone, _rect(x, z, x + w, z + d), 0.0)
        ]
        self.window_zones: List[Rect] = [
            zone
            for zone in window_protection_zones(windows, self.room_id)
            if _rect_overlap(zone, _rect(x, z, x + w, z + d), 0.0)
        ]
        self.items: List[Dict] = []
        self.reserved: List[Dict] = [
            {"rect": dict(zone), "reason": "doorway clearance"} for zone in self.door_zones
        ]
        spawn = room.get("spawn_m")
        if spawn_room_id == self.room_id and spawn:
            rect = _rect(spawn[0] - 0.55, spawn[1] - 0.55, spawn[0] + 0.55, spawn[1] + 0.55)
            self.reserved.append({"rect": rect, "reason": "walkthrough spawn point"})
        self.entry_points = self._entry_points(spawn if spawn_room_id == self.room_id else None)
        self.skipped: List[Dict] = []
        self.notes: List[str] = []
        self._counter = 0

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------
    def side_length(self, side: str) -> float:
        x, z, w, d = self.bounds
        return w if side in ("north", "south") else d

    def side_order(self, exclude: Iterable[str] = ()) -> List[str]:
        exclude = set(exclude)
        return sorted(
            [side for side in SIDE_ORDER if side not in exclude],
            key=lambda side: (
                -round(self.side_length(side), 3),
                (SIDE_ORDER.index(side) + self.variant) % len(SIDE_ORDER),
            ),
        )

    def _along_candidates(self, side: str, width: float, target: Optional[float]) -> List[float]:
        x, z, w, d = self.bounds
        if side in ("north", "south"):
            start, end = x, x + w
        else:
            start, end = z, z + d
        low = start + width / 2 + 0.03
        high = end - width / 2 - 0.03
        if low > high:
            return []
        mid = (low + high) / 2
        span = high - low
        fractions = [0.5, 0.0, 1.0, 0.25, 0.75]
        if self.variant % 3 == 1:
            fractions = [0.0, 1.0, 0.5, 0.25, 0.75]
        elif self.variant % 3 == 2:
            fractions = [1.0, 0.0, 0.5, 0.75, 0.25]
        values: List[float] = []
        if target is not None:
            values.append(min(high, max(low, target)))
        values.extend(low + span * fraction for fraction in fractions)
        values.append(mid)
        unique: List[float] = []
        for value in values:
            if all(abs(value - existing) > 0.02 for existing in unique):
                unique.append(value)
        return unique

    def _footprint_inside(self, footprint: Rect) -> bool:
        candidate = shapely_box(
            footprint["min"][0], footprint["min"][1], footprint["max"][0], footprint["max"][1]
        )
        return bool(self.inner.covers(candidate))

    def _wall_center(
        self, side: str, rotation_deg: int, asset_id: str, along: float
    ) -> Tuple[float, float]:
        x, z, w, d = self.bounds
        min_x, min_z, max_x, max_z = _visual_offsets(asset_id, rotation_deg)
        center_x_offset = (min_x + max_x) / 2
        center_z_offset = (min_z + max_z) / 2
        if side == "north":
            return along - center_x_offset, z + WALL_GAP - min_z
        if side == "south":
            return along - center_x_offset, z + d - WALL_GAP - max_z
        if side == "west":
            return x + WALL_GAP - min_x, along - center_z_offset
        return x + w - WALL_GAP - max_x, along - center_z_offset

    def _front_reserve(self, footprint: Rect, side: str, reserve: float) -> Optional[Rect]:
        if reserve <= 0:
            return None
        if side == "north":
            return _rect(
                footprint["min"][0], footprint["min"][1], footprint["max"][0], footprint["max"][1] + reserve
            )
        if side == "south":
            return _rect(
                footprint["min"][0], footprint["max"][1] - reserve, footprint["max"][0], footprint["max"][1]
            )
        if side == "west":
            return _rect(
                footprint["min"][0], footprint["min"][1], footprint["max"][0] + reserve, footprint["max"][1]
            )
        return _rect(
            footprint["max"][0] - reserve, footprint["min"][1], footprint["max"][0], footprint["max"][1]
        )

    # ------------------------------------------------------------------
    # Placement
    # ------------------------------------------------------------------
    def try_place(
        self,
        asset_id: str,
        center: Tuple[float, float],
        rotation_deg: int,
        *,
        label: Optional[str] = None,
        placement: str = "floor",
        gap: float = 0.06,
        front_reserve: float = 0.0,
        wall: Optional[str] = None,
        parent_id: Optional[str] = None,
        y: float = 0.0,
        overhead: bool = False,
        collision: Optional[bool] = None,
    ) -> Tuple[Optional[Dict], str]:
        if asset_id not in ASSETS:
            return None, f"Unknown furniture asset '{asset_id}'."
        spec = asset_spec(asset_id)
        min_x, min_z, max_x, max_z = _visual_offsets(asset_id, rotation_deg % 360)
        _, visual_min_y, _, _, visual_max_y, _ = asset_visual_bounds(asset_id)
        height = visual_max_y - visual_min_y
        world_w = max_x - min_x
        world_d = max_z - min_z
        footprint = _rect(
            center[0] + min_x,
            center[1] + min_z,
            center[0] + max_x,
            center[1] + max_z,
        )
        if placement == "wall_mount":
            position_y = y - (visual_min_y + visual_max_y) / 2
        elif placement == "surface" and asset_id == "pendant":
            position_y = y - visual_max_y
        else:
            position_y = y - visual_min_y
        if not self._footprint_inside(footprint):
            return None, "its footprint would extend outside the room."
        collides = spec.collision if collision is None else bool(collision)
        if (collides and height >= 1.2) or placement == "wall_mount":
            for zone in self.window_zones:
                if _rect_overlap(footprint, zone):
                    return None, "it would cover a window."
        if not overhead and collides:
            for zone in self.door_zones:
                if _rect_overlap(footprint, zone):
                    return None, "it would obstruct a doorway."
            for reserved in self.reserved:
                if _rect_overlap(footprint, reserved["rect"]):
                    return None, f"it would overlap the {reserved['reason']}."
        for existing in self.items:
            if not existing.get("collision"):
                continue
            if not collides:
                continue
            if _rect_overlap(footprint, existing["footprint"], gap):
                return None, "it would overlap another piece of furniture."
        candidate = {
            "id": "",
            "room_id": self.room_id,
            "room_type": self.room_type,
            "room_name": self.name,
            "asset_id": asset_id,
            "model_id": asset_id,
            "label": label or spec.label,
            "position": [round(center[0], 4), round(position_y, 4), round(center[1], 4)],
            "rotation_deg": rotation_deg % 360,
            "rotation": round(math.radians(rotation_deg % 360), 6),
            "scale": [1.0, 1.0, 1.0],
            "size_m": [round(world_w, 4), round(height, 4), round(world_d, 4)],
            "height_m": round(height, 4),
            "footprint": footprint,
            "bbox": {
                "min": [
                    round(center[0] + min_x, 4),
                    round(position_y + visual_min_y, 4),
                    round(center[1] + min_z, 4),
                ],
                "max": [
                    round(center[0] + max_x, 4),
                    round(position_y + visual_max_y, 4),
                    round(center[1] + max_z, 4),
                ],
            },
            "collision": collides,
            "collision_bounds": footprint if collides else None,
            "materials": list(_asset_materials(asset_id)),
            "material_style": self.style,
            "placement": placement,
            "wall": wall,
            "parent_item_id": parent_id,
            "status": "placed",
        }
        self.items.append(candidate)
        if collides and not self._navigation_ok():
            self.items.pop()
            return None, "it would block the walking route between doorways."
        self._counter += 1
        candidate["id"] = f"r{self.room_id}_{asset_id}_{self._counter:02d}"
        if collides and front_reserve > 0:
            reserve = self._front_reserve(footprint, wall or "north", front_reserve)
            if reserve:
                self.reserved.append(
                    {"rect": reserve, "reason": f"{candidate['label'].lower()} clearance", "item_id": candidate["id"]}
                )
        return candidate, ""

    def place_on_wall(
        self,
        asset_id: str,
        *,
        sides: Optional[Sequence[str]] = None,
        target_along: Optional[float] = None,
        label: Optional[str] = None,
        gap: float = 0.06,
        front_reserve: float = 0.0,
        y: Optional[float] = None,
        overhead: bool = False,
        placement: str = "wall",
        collision: Optional[bool] = None,
    ) -> Tuple[Optional[Dict], str]:
        sides = list(sides) if sides is not None else self.side_order()
        last_reason = "no wall run in this room is long enough."
        for side in sides:
            rotation = SIDE_ROTATION[side]
            world_w, _, _ = _world_size(asset_id, rotation)
            along_values = self._along_candidates(side, world_w, target_along)
            if not along_values:
                continue
            for along in along_values:
                center = self._wall_center(side, rotation, asset_id, along)
                item, reason = self.try_place(
                    asset_id,
                    center,
                    rotation,
                    label=label,
                    gap=gap,
                    front_reserve=front_reserve,
                    wall=side,
                    y=y or 0.0,
                    overhead=overhead,
                    placement=placement,
                    collision=collision,
                )
                if item:
                    return item, ""
                last_reason = reason
        return None, last_reason

    def place_center(
        self,
        asset_id: str,
        *,
        label: Optional[str] = None,
        gap: float = 0.06,
        y: float = 0.0,
        overhead: bool = False,
    ) -> Tuple[Optional[Dict], str]:
        offsets = [(0.0, 0.0), (-0.45, 0.0), (0.45, 0.0), (0.0, -0.45), (0.0, 0.45)]
        last_reason = "no free floor area in the middle of the room."
        for dx, dz in offsets:
            item, reason = self.try_place(
                asset_id,
                (self.center[0] + dx, self.center[1] + dz),
                0,
                label=label,
                gap=gap,
                y=y,
                overhead=overhead,
                placement="center",
            )
            if item:
                return item, ""
            last_reason = reason
        return None, last_reason

    def place_in_front(
        self,
        parent: Dict,
        asset_id: str,
        distance: float,
        *,
        label: Optional[str] = None,
        gap: float = 0.05,
        rotation: Optional[int] = None,
    ) -> Tuple[Optional[Dict], str]:
        rotation = parent["rotation_deg"] if rotation is None else rotation
        fx, fz = _rotation_vector(rotation)
        parent_width, _, parent_depth = _world_size(parent["asset_id"], parent["rotation_deg"])
        child_width, _, child_depth = _world_size(asset_id, rotation)
        # The forward axis is world +x for 90/270 degrees and world +z otherwise.
        parent_extent = parent_depth if rotation % 180 == 0 else parent_width
        child_extent = child_depth if rotation % 180 == 0 else child_width
        px, _, pz = parent["position"]
        center = (
            px + fx * (parent_extent / 2 + distance + child_extent / 2),
            pz + fz * (parent_extent / 2 + distance + child_extent / 2),
        )
        return self.try_place(
            asset_id,
            center,
            rotation,
            label=label,
            gap=gap,
            placement="front",
            parent_id=parent["id"],
        )

    def place_on_surface(
        self,
        parent: Dict,
        asset_id: str,
        *,
        label: Optional[str] = None,
        local_x: float = 0.0,
        local_z: float = 0.0,
        y: float = 0.0,
        collision: bool = False,
    ) -> Tuple[Optional[Dict], str]:
        dx, dz = _rotate_offset(local_x, local_z, parent["rotation_deg"])
        px, py, pz = parent["position"]
        return self.try_place(
            asset_id,
            (px + dx, pz + dz),
            parent["rotation_deg"],
            label=label,
            gap=0.0,
            y=py + y,
            overhead=True,
            placement="surface",
            parent_id=parent["id"],
            collision=collision,
        )

    def place_wall_decor(
        self,
        asset_id: str,
        *,
        wall: str,
        target_along: float,
        label: Optional[str] = None,
        y: Optional[float] = None,
    ) -> Tuple[Optional[Dict], str]:
        return self.place_on_wall(
            asset_id,
            sides=[wall],
            target_along=target_along,
            label=label,
            y=WALL_DECOR_HEIGHT.get(asset_id, 1.4) if y is None else y,
            overhead=True,
            placement="wall_mount",
            collision=False,
        )

    def skip(self, asset_id: str, reason: str, required: bool = True) -> None:
        self.skipped.append(
            {
                "asset_id": asset_id,
                "label": asset_spec(asset_id).label,
                "reason": reason,
                "required": required,
            }
        )

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    def _entry_points(self, spawn: Optional[Sequence[float]]) -> List[Tuple[float, float]]:
        points: List[Tuple[float, float]] = []
        interior = self.polygon.representative_point()
        points.append((float(interior.x), float(interior.y)))
        cx, cz = self.room.get("center_m") or self.center
        for door in self.room_doors:
            center = door.get("center_m") or [cx, cz]
            dx, dz = cx - center[0], cz - center[1]
            length = math.hypot(dx, dz) or 1.0
            points.append((center[0] + dx / length * 0.55, center[1] + dz / length * 0.55))
        if spawn:
            points.append((float(spawn[0]), float(spawn[1])))
        unique: List[Tuple[float, float]] = []
        for point in points:
            if all(math.hypot(point[0] - other[0], point[1] - other[1]) > 0.14 for other in unique):
                unique.append(point)
        return unique

    def _navigation_ok(self) -> bool:
        if len(self.entry_points) < 2:
            return True
        x, z, w, d = self.bounds
        xs = np.arange(x + WALK_GRID_M / 2, x + w, WALK_GRID_M)
        zs = np.arange(z + WALK_GRID_M / 2, z + d, WALK_GRID_M)
        if xs.size == 0 or zs.size == 0:
            return False
        grid_x, grid_z = np.meshgrid(xs, zs)
        free = contains_xy(self.polygon, grid_x, grid_z)
        for item in self.items:
            if not item.get("collision"):
                continue
            bounds = item["collision_bounds"]
            margin = PLAYER_RADIUS + 0.02
            blocked = (
                (grid_x >= bounds["min"][0] - margin)
                & (grid_x <= bounds["max"][0] + margin)
                & (grid_z >= bounds["min"][1] - margin)
                & (grid_z <= bounds["max"][1] + margin)
            )
            free &= ~blocked
        indices = []
        for point in self.entry_points:
            index = self._nearest_free_index(free, xs, zs, point)
            if index is None:
                return False
            indices.append(index)
        start = indices[0]
        visited = np.zeros(free.shape, dtype=bool)
        queue = deque([start])
        visited[start] = True
        while queue:
            row, col = queue.popleft()
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nr, nc = row + dr, col + dc
                if 0 <= nr < free.shape[0] and 0 <= nc < free.shape[1]:
                    if free[nr, nc] and not visited[nr, nc]:
                        visited[nr, nc] = True
                        queue.append((nr, nc))
        return all(visited[index] for index in indices)

    @staticmethod
    def _nearest_free_index(free: np.ndarray, xs: np.ndarray, zs: np.ndarray, point) -> Optional[Tuple[int, int]]:
        col = int(np.argmin(np.abs(xs - point[0])))
        row = int(np.argmin(np.abs(zs - point[1])))
        if free[row, col]:
            return row, col
        free_rows, free_cols = np.nonzero(free)
        if free_rows.size == 0:
            return None
        distances = (xs[free_cols] - point[0]) ** 2 + (zs[free_rows] - point[1]) ** 2
        best = int(np.argmin(distances))
        return int(free_rows[best]), int(free_cols[best])

    def navigation_check(self) -> bool:
        return self._navigation_ok()


# ---------------------------------------------------------------------------
# Room planners
# ---------------------------------------------------------------------------
def _along_of(item: Dict, side: str) -> float:
    return item["position"][0] if side in ("north", "south") else item["position"][2]


def _plan_living_room(layout: RoomLayout) -> None:
    tv_stand, reason = layout.place_on_wall(
        "tv_stand",
        label="TV stand",
        gap=0.08,
        front_reserve=0.0,
    )
    television = None
    if tv_stand:
        television, _ = layout.place_on_surface(
            tv_stand,
            "television",
            label="Television",
            local_z=-(asset_spec("tv_stand").size[2] - asset_spec("television").size[2]) / 2 + 0.03,
            y=asset_spec("tv_stand").size[1],
        )
    else:
        layout.skip("tv_stand", reason)

    sofa = None
    preferred = [OPPOSITE_SIDE[tv_stand["wall"]]] if tv_stand else []
    preferred.extend(layout.side_order(exclude=preferred + ([tv_stand["wall"]] if tv_stand else [])))
    target = _along_of(tv_stand, tv_stand["wall"]) if tv_stand else None
    for side in preferred:
        sofa, reason = layout.place_on_wall(
            "sofa",
            sides=[side],
            target_along=target if side == OPPOSITE_SIDE.get(tv_stand["wall"]) else None,
            gap=0.08,
            front_reserve=0.0,
        )
        if sofa:
            break
    if not sofa:
        layout.skip("sofa", reason)
        layout.skip("coffee_table", "A living room needs a sofa before a coffee table is useful.", required=False)
        if television:
            layout.notes.append("The TV is present but no sofa could be placed in this room.")
        return

    table, table_reason = layout.place_in_front(sofa, "coffee_table", 0.32, label="Coffee table")
    if not table:
        layout.skip("coffee_table", table_reason, required=False)
    if table:
        rug_center = (
            (sofa["position"][0] + table["position"][0]) / 2,
            (sofa["position"][2] + table["position"][2]) / 2,
        )
        layout.try_place(
            "rug",
            rug_center,
            sofa["rotation_deg"] % 180,
            label="Rug",
            overhead=True,
            placement="decor",
            collision=False,
        )

    area = float(layout.room.get("area_m2") or 0)
    if area >= 15:
        armchair, armchair_reason = layout.place_on_wall(
            "armchair",
            sides=layout.side_order(exclude=[sofa["wall"]] + ([tv_stand["wall"]] if tv_stand else [])),
            gap=0.12,
        )
        if armchair:
            pass
        else:
            layout.skip("armchair", armchair_reason, required=False)
    lamp, _ = layout.place_on_wall("floor_lamp", sides=layout.side_order(exclude=[sofa["wall"]]), gap=0.1)
    if not lamp:
        layout.skip("floor_lamp", "No free corner remained for a floor lamp.", required=False)
    plant, _ = layout.place_on_wall("plant", sides=layout.side_order(exclude=[sofa["wall"]]), gap=0.1)
    if not plant:
        layout.skip("plant", "No free corner remained for a plant.", required=False)
    layout.place_wall_decor(
        "art_frame",
        wall=sofa["wall"],
        target_along=_along_of(sofa, sofa["wall"]),
        label="Wall art",
    )


def _pick_bed(layout: RoomLayout) -> str:
    area = float(layout.room.get("area_m2") or 0)
    if area >= 17:
        return "bed_king"
    if area >= 12:
        return "bed_queen"
    if area >= 8:
        return "bed_double"
    return "bed_single"


def _plan_bedroom(layout: RoomLayout) -> None:
    bed_asset = _pick_bed(layout)
    bed, reason = layout.place_on_wall(bed_asset, gap=0.08)
    if not bed:
        smaller = ["bed_double", "bed_single"]
        for candidate in smaller:
            if ASSETS[candidate].size[0] >= ASSETS[bed_asset].size[0]:
                continue
            bed, reason = layout.place_on_wall(candidate, gap=0.08)
            if bed:
                bed_asset = candidate
                break
    if not bed:
        layout.skip(bed_asset, reason)
        layout.skip("wardrobe", "A wardrobe needs a bed-sized footprint to remain usable.", required=False)
        layout.notes.append("The room is too small for a bed, so it was left unfurnished.")
        return

    bed_width = _world_size(bed_asset, bed["rotation_deg"])[0]
    nightstand_width = asset_spec("nightstand").size[0]
    offset = bed_width / 2 + nightstand_width / 2 + 0.04
    along = _along_of(bed, bed["wall"])
    nightstands = []
    for target in (along - offset, along + offset):
        nightstand, _ = layout.place_on_wall(
            "nightstand",
            sides=[bed["wall"]],
            target_along=target,
            gap=0.02,
        )
        if nightstand:
            nightstands.append(nightstand)
            layout.place_on_surface(
                nightstand,
                "bed_lamp",
                label="Bedside lamp",
                y=asset_spec("nightstand").size[1],
            )
    if not nightstands:
        layout.skip("nightstand", "No space beside the bed for a bedside table.", required=False)

    area = float(layout.room.get("area_m2") or 0)
    wardrobe_asset = "wardrobe_wide" if area >= 15 else "wardrobe"
    wardrobe, wardrobe_reason = layout.place_on_wall(
        wardrobe_asset,
        sides=layout.side_order(exclude=[bed["wall"]]),
        gap=0.1,
        front_reserve=0.55,
    )
    if not wardrobe:
        wardrobe, wardrobe_reason = layout.place_on_wall(
            "wardrobe",
            sides=layout.side_order(exclude=[bed["wall"]]),
            gap=0.1,
            front_reserve=0.5,
        )
    if not wardrobe:
        layout.skip(wardrobe_asset, wardrobe_reason)

    if area >= 11:
        desk, desk_reason = layout.place_on_wall(
            "desk",
            sides=layout.side_order(exclude=[bed["wall"]] + ([wardrobe["wall"]] if wardrobe else [])),
            gap=0.1,
            front_reserve=0.55,
        )
        if desk:
            chair, _ = layout.place_in_front(desk, "desk_chair", 0.12, label="Desk chair", gap=0.02, rotation=(desk["rotation_deg"] + 180) % 360)
            if not chair:
                layout.skip("desk_chair", "The desk has no space for a chair without blocking circulation.", required=False)
        else:
            layout.skip("desk", desk_reason, required=False)

    if area >= 12:
        rug_center = (bed["position"][0], bed["position"][2])
        rug, _ = layout.try_place(
            "rug",
            rug_center,
            bed["rotation_deg"] % 180,
            label="Rug",
            overhead=True,
            placement="decor",
            collision=False,
        )
    layout.place_wall_decor(
        "art_frame",
        wall=bed["wall"],
        target_along=along,
        label="Wall art above bed",
        y=1.55,
    )


def _plan_kitchen(layout: RoomLayout) -> None:
    area = float(layout.room.get("area_m2") or 0)
    counter_asset = "kitchen_counter_240" if area >= 12 else "kitchen_counter_180"
    counter, reason = layout.place_on_wall(counter_asset, gap=0.06)
    if not counter:
        counter_asset = "kitchen_counter_120"
        counter, reason = layout.place_on_wall(counter_asset, gap=0.06)
    if not counter:
        layout.skip(counter_asset, reason)
    else:
        width = asset_spec(counter_asset).size[0]
        layout.place_on_surface(
            counter,
            "kitchen_sink",
            label="Sink",
            local_x=-width * 0.22,
            local_z=0.06,
            y=0.9175,
        )
        layout.place_on_surface(
            counter,
            "kitchen_stove",
            label="Stove",
            local_x=width * 0.22,
            local_z=0.04,
            y=0.9175,
        )

    refrigerator, fridge_reason = layout.place_on_wall(
        "refrigerator",
        sides=layout.side_order(exclude=[counter["wall"]] if counter else ()),
        gap=0.08,
        front_reserve=0.55,
    )
    if not refrigerator:
        layout.skip("refrigerator", fridge_reason)
        if not counter:
            layout.notes.append("The kitchen is too small for cabinetry and appliances.")

    table_asset = "dining_table_4" if area >= 13 else "dining_table_round"
    table, table_reason = layout.place_center(table_asset, gap=0.12)
    if table:
        layout.place_on_surface(
            table,
            "pendant",
            label="Pendant light",
            local_x=0,
            local_z=0,
            y=CEILING_HEIGHT,
        )
        _place_chairs(layout, table, 4 if table_asset == "dining_table_4" else 2)
    else:
        layout.skip(table_asset, table_reason, required=False)


def _plan_bathroom(layout: RoomLayout) -> None:
    area = float(layout.room.get("area_m2") or 0)
    toilet, toilet_reason = layout.place_on_wall("toilet", gap=0.08, front_reserve=0.5)
    if not toilet:
        layout.skip("toilet", toilet_reason)
        layout.notes.append("No usable wall space was found for a toilet.")
        return
    vanity, vanity_reason = layout.place_on_wall(
        "vanity",
        sides=layout.side_order(exclude=[toilet["wall"]]),
        gap=0.08,
        front_reserve=0.5,
    )
    if not vanity:
        layout.skip("vanity", vanity_reason)
    else:
        layout.place_on_surface(
            vanity,
            "wall_mirror",
            label="Mirror above vanity",
            local_z=-(asset_spec("vanity").size[2] - asset_spec("wall_mirror").size[2]) / 2 + 0.005,
            y=1.12,
        )
        layout.place_wall_decor(
            "towel_rail",
            wall=vanity["wall"],
            target_along=_along_of(vanity, vanity["wall"]) + asset_spec("vanity").size[0] / 2 + 0.35,
        )

    if area >= 8:
        tub, tub_reason = layout.place_on_wall(
            "bathtub",
            sides=layout.side_order(exclude=[toilet["wall"]] + ([vanity["wall"]] if vanity else [])),
            gap=0.1,
            front_reserve=0.5,
        )
        if tub:
            return
        layout.skip("bathtub", tub_reason, required=False)
    shower, shower_reason = layout.place_on_wall(
        "shower",
        sides=layout.side_order(exclude=[toilet["wall"]] + ([vanity["wall"]] if vanity else [])),
        gap=0.1,
        front_reserve=0.5,
    )
    if not shower:
        layout.skip("shower", shower_reason, required=False)


def _place_chairs(layout: RoomLayout, table: Dict, count: int) -> None:
    width, _, depth = _world_size(table["asset_id"], table["rotation_deg"])
    chair_depth = asset_spec("dining_chair").size[2]
    chair_width = asset_spec("dining_chair").size[0]
    tx, _, tz = table["position"]
    rotation = table["rotation_deg"]
    offsets = []
    gap = 0.02
    side_offset = depth / 2 + chair_depth / 2 + gap
    if count >= 4:
        offsets.extend(
            [
                (-width / 4, -side_offset, rotation),
                (width / 4, -side_offset, rotation),
                (-width / 4, side_offset, (rotation + 180) % 360),
                (width / 4, side_offset, (rotation + 180) % 360),
            ]
        )
    else:
        offsets.extend(
            [
                (0.0, -side_offset, rotation),
                (0.0, side_offset, (rotation + 180) % 360),
            ]
        )
    if count >= 6:
        end_offset = width / 2 + chair_depth / 2 + gap
        offsets.extend(
            [
                (-end_offset, 0.0, (rotation + 270) % 360),
                (end_offset, 0.0, (rotation + 90) % 360),
            ]
        )
    placed = 0
    for dx, dz, chair_rotation in offsets:
        rx, rz = _rotate_offset(dx, dz, rotation)
        chair, reason = layout.try_place(
            "dining_chair",
            (tx + rx, tz + rz),
            chair_rotation,
            gap=0.0,
            placement="table",
            parent_id=table["id"],
        )
        if chair:
            placed += 1
        else:
            layout.skip("dining_chair", reason, required=False)
    if count >= 4 and placed < 2:
        layout.notes.append("Only a small number of dining chairs fitted without blocking circulation.")


def _plan_dining_room(layout: RoomLayout) -> None:
    area = float(layout.room.get("area_m2") or 0)
    if area >= 16:
        table_asset, chairs = "dining_table_6", 6
    elif area >= 9:
        table_asset, chairs = "dining_table_4", 4
    else:
        table_asset, chairs = "dining_table_round", 2
    table, reason = layout.place_center(table_asset, gap=0.1)
    if not table:
        layout.skip(table_asset, reason)
        return
    layout.place_on_surface(
        table,
        "pendant",
        label="Pendant light",
        y=2.2,
    )
    _place_chairs(layout, table, chairs)
    sideboard, sideboard_reason = layout.place_on_wall(
        "sideboard",
        gap=0.1,
        front_reserve=0.45,
    )
    if not sideboard:
        layout.skip("sideboard", sideboard_reason, required=False)


def _plan_entrance(layout: RoomLayout) -> None:
    cabinet, reason = layout.place_on_wall("shoe_cabinet", gap=0.08, front_reserve=0.45)
    if not cabinet:
        layout.skip("shoe_cabinet", reason)
    else:
        layout.place_wall_decor(
            "wall_mirror",
            wall=cabinet["wall"],
            target_along=_along_of(cabinet, cabinet["wall"]),
            label="Entry mirror",
            y=1.25,
        )
    coat_rack, coat_reason = layout.place_on_wall(
        "coat_rack",
        sides=layout.side_order(exclude=[cabinet["wall"]] if cabinet else ()),
        gap=0.1,
    )
    if not coat_rack:
        layout.skip("coat_rack", coat_reason, required=False)
    bench, bench_reason = layout.place_on_wall(
        "entry_bench",
        sides=layout.side_order(exclude=[cabinet["wall"]] if cabinet else ()),
        gap=0.1,
    )
    if not bench:
        layout.skip("entry_bench", bench_reason, required=False)


def _plan_hallway(layout: RoomLayout) -> None:
    # Corridors stay clear; only a small wall decoration is added when it fits.
    area = float(layout.room.get("area_m2") or 0)
    width = float(layout.room["bounds_m"][2])
    depth = float(layout.room["bounds_m"][3])
    if area >= 3.5 and min(width, depth) >= 1.2:
        side = layout.side_order()[0]
        along = layout._along_candidates(side, asset_spec("art_frame").size[0], None)
        if along:
            layout.place_wall_decor("art_frame", wall=side, target_along=along[0])
    else:
        layout.notes.append("Hallway kept clear: no furniture is placed in a narrow corridor.")


def _plan_balcony(layout: RoomLayout) -> None:
    chair, reason = layout.place_on_wall("balcony_chair", gap=0.08)
    table, table_reason = layout.place_center("balcony_table", gap=0.06)
    if not chair:
        layout.skip("balcony_chair", reason, required=False)
    if not table:
        layout.skip("balcony_table", table_reason, required=False)
    if not chair and not table:
        layout.notes.append("Balcony left clear because no furniture fitted without blocking access.")


def _plan_unknown(layout: RoomLayout) -> None:
    layout.notes.append(
        "Room type is unspecified, so no furniture was placed. Choose a room type and regenerate to furnish it."
    )


PLANNERS = {
    "living_room": _plan_living_room,
    "bedroom": _plan_bedroom,
    "kitchen": _plan_kitchen,
    "bathroom": _plan_bathroom,
    "dining_room": _plan_dining_room,
    "entrance": _plan_entrance,
    "hallway": _plan_hallway,
    "balcony": _plan_balcony,
    "unknown": _plan_unknown,
}


def _room_status(layout: RoomLayout) -> str:
    if layout.room_type == "unknown" and not layout.items:
        return "unspecified"
    if not layout.items:
        return "unfurnished"
    required_skipped = [entry for entry in layout.skipped if entry.get("required")]
    return "partially_furnished" if required_skipped else "furnished"


def _room_entry(layout: RoomLayout) -> Dict:
    return {
        "room_id": layout.room_id,
        "name": layout.name,
        "room_type": layout.room_type,
        "area_m2": layout.room.get("area_m2"),
        "status": _room_status(layout),
        "furniture_ids": [item["id"] for item in layout.items],
        "furniture": [
            {
                "id": item["id"],
                "asset_id": item["asset_id"],
                "label": item["label"],
                "position": item["position"],
                "rotation_deg": item["rotation_deg"],
                "collision": item["collision"],
            }
            for item in layout.items
        ],
        "skipped": layout.skipped,
        "notes": layout.notes,
    }


def furnish_floor_plan(
    rooms: Sequence[Dict],
    room_metadata: Dict[int, Dict],
    doorways: Sequence[Dict],
    windows: Sequence[Dict],
    style: str = "modern",
    variant: int = 0,
) -> Dict:
    """Furnish every room deterministically from the parsed floor plan."""
    if style not in STYLES:
        raise ValueError(f"Unknown style '{style}'. Choose one of: {', '.join(STYLES)}.")
    variant = int(variant or 0)
    spawn_room = max(rooms, key=lambda room: float(room.get("area_m2") or 0.0)) if rooms else None
    spawn_room_id = int(spawn_room["id"]) if spawn_room else None
    room_entries: List[Dict] = []
    items: List[Dict] = []
    warnings: List[str] = []
    for room in sorted(rooms, key=lambda entry: int(entry["id"])):
        room_id = int(room["id"])
        metadata = room_metadata.get(room_id) or {}
        room_type = metadata.get("room_type") or "unknown"
        name = metadata.get("name") or f"Room {room_id + 1}"
        layout = RoomLayout(
            room,
            room_type,
            name,
            doorways,
            variant,
            spawn_room_id,
            style,
            windows,
        )
        planner = PLANNERS.get(room_type, _plan_unknown)
        planner(layout)
        if not layout.navigation_check():
            raise ValueError(
                f"Furniture in {name} would disconnect the room's doorways; layout rejected."
            )
        entry = _room_entry(layout)
        room_entries.append(entry)
        items.extend(layout.items)
        if room_type == "unknown":
            warnings.append(
                f"{name} has no confirmed room type and was left unfurnished."
            )
        for skipped in layout.skipped:
            if skipped.get("required"):
                warnings.append(
                    f"{name}: {skipped['label']} could not be placed because {skipped['reason']}"
                )

    collision_items = [item for item in items if item.get("collision")]
    summary = {
        "items": len(items),
        "collision_items": len(collision_items),
        "rooms_total": len(room_entries),
        "rooms_furnished": sum(
            1 for entry in room_entries if entry["status"] in ("furnished", "partially_furnished")
        ),
        "rooms_unspecified": sum(1 for entry in room_entries if entry["status"] == "unspecified"),
        "skipped_required": sum(
            1
            for entry in room_entries
            for skipped in entry["skipped"]
            if skipped.get("required")
        ),
        "style": style,
        "variant": variant,
    }
    return {
        "status": "completed",
        "style": style,
        "style_label": STYLE_LABELS.get(style, style),
        "variant": variant,
        "items": items,
        "rooms": room_entries,
        "materials": material_config(style),
        "warnings": warnings,
        "summary": summary,
        "limits": {
            "player_radius_m": PLAYER_RADIUS,
            "door_clearance_depth_m": DOOR_CLEAR_DEPTH,
            "wall_gap_m": WALL_GAP,
            "grid_m": WALK_GRID_M,
            "furniture_collision": "axis-aligned bounds of collision-bearing items",
        },
    }


def furniture_collision_items(furnishing: Dict) -> List[Dict]:
    boxes = []
    for item in furnishing.get("items", []):
        if not item.get("collision") or not item.get("collision_bounds"):
            continue
        bounds = item["collision_bounds"]
        boxes.append(
            {
                "item_id": item["id"],
                "room_id": item["room_id"],
                "asset_id": item["asset_id"],
                "label": item["label"],
                "min": bounds["min"],
                "max": bounds["max"],
                "height_m": item.get("height_m"),
            }
        )
    return boxes


def validate_furnishing(
    furnishing: Dict,
    rooms: Sequence[Dict],
    doorways: Sequence[Dict],
    windows: Sequence[Dict] = (),
) -> Dict:
    """Independently verify that a furnishing result respects the floor plan."""
    issues: List[str] = []
    room_by_id = {int(room["id"]): room for room in rooms}
    polygons = {room_id: _room_polygon(room) for room_id, room in room_by_id.items()}
    zones = door_clearance_zones(doorways)
    doors_by_room: Dict[int, set] = {
        room_id: {
            door.get("id")
            for door in doorways
            if room_id in [rid for rid in (door.get("rooms") or []) if rid is not None]
        }
        for room_id in room_by_id
    }
    items = furnishing.get("items", [])
    seen_ids = set()
    by_room: Dict[int, List[Dict]] = {}
    for item in items:
        item_id = item.get("id")
        if not item_id or item_id in seen_ids:
            issues.append(f"Furniture item {item_id!r} has a missing or duplicate id.")
            continue
        seen_ids.add(item_id)
        room_id = int(item.get("room_id", -1))
        if room_id not in room_by_id:
            issues.append(f"{item_id} references room {room_id}, which does not exist.")
            continue
        if item.get("room_type") == "unknown":
            issues.append(f"{item_id} was placed in an unspecified room.")
        footprint = item.get("footprint")
        if not footprint:
            issues.append(f"{item_id} has no footprint.")
            continue
        candidate = shapely_box(
            footprint["min"][0], footprint["min"][1], footprint["max"][0], footprint["max"][1]
        )
        if not polygons[room_id].covers(candidate):
            issues.append(f"{item_id} extends outside room {room_id}'s boundary.")
        if item.get("collision"):
            own_doors = doors_by_room.get(room_id, set())
            for zone in zones:
                if zone.get("door_id") in own_doors and _rect_overlap(footprint, zone):
                    issues.append(f"{item_id} obstructs doorway {zone.get('door_id')}.")
        by_room.setdefault(room_id, []).append(item)
    for room_id, room_items in by_room.items():
        for index, first in enumerate(room_items):
            for second in room_items[index + 1 :]:
                if not first.get("collision") or not second.get("collision"):
                    continue
                if _rect_overlap(first["footprint"], second["footprint"]):
                    issues.append(
                        f"{first['id']} and {second['id']} have overlapping collision bounds."
                    )

    spawn_room = max(rooms, key=lambda room: float(room.get("area_m2") or 0.0)) if rooms else None
    navigation = {}
    for room in rooms:
        room_id = int(room["id"])
        room_items = by_room.get(room_id, [])
        layout = RoomLayout(
            room,
            "unknown",
            f"Room {room_id + 1}",
            doorways,
            int(furnishing.get("variant") or 0),
            int(spawn_room["id"]) if spawn_room else None,
            str(furnishing.get("style") or "modern"),
            windows,
        )
        layout.items = room_items
        layout.entry_points = layout._entry_points(
            room.get("spawn_m") if spawn_room and int(spawn_room["id"]) == room_id else None
        )
        ok = layout.navigation_check()
        navigation[str(room_id)] = {"reachable": ok, "entry_points": len(layout.entry_points)}
        if not ok:
            issues.append(
                f"Room {room_id} is not walkable: furniture disconnects its doorways."
            )
    unknown_items = [item for item in items if item.get("room_type") == "unknown"]
    if unknown_items:
        issues.append("Rooms with an unspecified type must not contain furniture.")
    summary = furnishing.get("summary", {})
    if summary.get("items") != len(items):
        issues.append("Furniture summary count does not match the items list.")
    return {
        "valid": not issues,
        "issues": issues,
        "navigation": navigation,
        "checked_items": len(items),
        "checked_rooms": len(rooms),
    }
