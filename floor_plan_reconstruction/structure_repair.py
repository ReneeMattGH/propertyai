"""Deterministic structural reconciliation for imperfect floor plans.

The floor-plan parser can report the same physical opening twice (once on each
face of a thick wall), can label a narrow exterior window as a doorway when a
noisy arc happens to overlap it, and can miss a doorway between two rooms that
clearly share a wall.  This module repairs only what the detected geometry
supports:

  * duplicate doorway/window reports of one opening are collapsed;
  * a doorway and a window claiming the same gap keep the doorway;
  * an implausibly narrow exterior "door" is preserved as a window instead of
    cutting a new hole through the building envelope;
  * a missing connection is inferred only between rooms that share a real wall
    face, when the room types make the connection plausible, when the shared
    face is wide enough for the player, and when no window or doorway already
    occupies the gap.

It never invents rooms or walls, never bridges external boundaries, and never
turns a bathroom into a thoroughfare.  Every change is written to the repair
log with the evidence that justified it.
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from shapely.geometry import Polygon

HARD_MIN_PASSAGE_M = 0.50
COMFORT_DOOR_M = 0.8
MAX_DOOR_M = 1.8
EXTERIOR_WINDOW_MAX_M = 0.62
DUPLICATE_LINE_TOLERANCE_M = 0.60
DUPLICATE_DOOR_LINE_TOLERANCE_M = 0.55
INFER_MAX_GAP_M = 1.10
INFER_MIN_FACE_M = 0.60
INFER_DOOR_WIDTH_M = 0.9
MAX_INFERRED_CONNECTIONS = 6

WET_TYPES = {"bathroom"}
PRIVATE_TYPES = {"bedroom"}
CIRCULATION_TYPES = {"hallway", "entrance"}
PUBLIC_TYPES = {"living_room", "dining_room", "kitchen", "balcony"}


def _axes(orientation: str) -> Tuple[int, int]:
    return (0, 1) if orientation == "horizontal" else (1, 0)


def _value(point: Sequence[float], index: int, default: float = 0.0) -> float:
    try:
        return float(point[index])
    except (TypeError, ValueError, IndexError):
        return default


def _opening_geometry(entry: Dict) -> Optional[Dict]:
    orientation = entry.get("orientation")
    if orientation not in ("horizontal", "vertical"):
        return None
    center = entry.get("center_m") or [0.0, 0.0]
    width = float(entry.get("width_m") or 0.9)
    if not math.isfinite(width) or width <= 0:
        return None
    run_axis, line_axis = _axes(orientation)
    run = _value(center, run_axis)
    return {
        "orientation": orientation,
        "run_axis": run_axis,
        "line_axis": line_axis,
        "run": run,
        "line": _value(center, line_axis),
        "half": width / 2.0,
        "span": (run - width / 2.0, run + width / 2.0),
    }


def _same_gap(first: Dict, second: Dict, line_tolerance: float) -> bool:
    a = _opening_geometry(first)
    b = _opening_geometry(second)
    if a is None or b is None or a["orientation"] != b["orientation"]:
        return False
    if abs(a["line"] - b["line"]) > line_tolerance:
        return False
    overlap = min(a["span"][1], b["span"][1]) - max(a["span"][0], b["span"][0])
    if overlap <= 0:
        return False
    shorter = min(a["span"][1] - a["span"][0], b["span"][1] - b["span"][0])
    return overlap >= max(0.30, 0.55 * shorter)


def _door_score(door: Dict) -> Tuple:
    rooms = [value for value in (door.get("rooms") or []) if value is not None]
    distinct = len(set(int(value) for value in rooms))
    support = float(door.get("arc_support") or 0.0) + float(door.get("leaf_support") or 0.0)
    kind = 1 if door.get("kind") == "door" else 0
    return (distinct, round(support, 3), kind, -int(door.get("id") or 0))


def _as_window(door: Dict) -> Dict:
    center = door.get("center_m") or [0.0, 0.0]
    rooms = [value for value in (door.get("rooms") or []) if value is not None]
    return {
        "id": 100000 + int(door.get("id") or 0),
        "orientation": door.get("orientation"),
        "center_m": [float(center[0]), float(center[1])],
        "width_m": round(float(door.get("width_m") or 0.6), 3),
        "room": int(rooms[0]) if rooms else None,
        "sill_m": 0.9,
        "head_m": 2.1,
        "inferred_sill_height": True,
        "arc_support": door.get("arc_support"),
        "leaf_support": door.get("leaf_support"),
        "preserved_from_door": door.get("id"),
    }


def reconcile_openings(
    doorways: List[Dict],
    windows: List[Dict],
    repairs: List[Dict],
    ambiguities: List[Dict],
) -> None:
    """Collapse duplicate opening reports and preserve exterior fabric."""

    preserved: List[Dict] = []
    kept_doors: List[Dict] = []
    for door in doorways:
        width = float(door.get("width_m") or 0.0)
        rooms = [value for value in (door.get("rooms") or []) if value is not None]
        if (
            door.get("exterior")
            and width > 0
            and width < EXTERIOR_WINDOW_MAX_M
            and len(set(int(value) for value in rooms)) <= 1
        ):
            window = _as_window(door)
            windows.append(window)
            preserved.append(door)
            repairs.append(
                {
                    "code": "exterior_opening_preserved_as_window",
                    "object_ids": [door.get("id")],
                    "door_id": door.get("id"),
                    "detail": (
                        f"Exterior opening {door.get('id')} is only {width:.2f} m wide; "
                        "it was preserved as a window instead of cutting a door-sized "
                        "hole through the building envelope."
                    ),
                    "before_m": round(width, 3),
                }
            )
            continue
        kept_doors.append(door)
    if preserved:
        removed = {id(door) for door in preserved}
        doorways[:] = [door for door in doorways if id(door) not in removed]
        ambiguities.append(
            {
                "code": "exterior_narrow_openings_preserved",
                "object_ids": [door.get("id") for door in preserved],
                "message": (
                    f"{len(preserved)} narrow exterior opening(s) were kept as windows; "
                    "they are not walkable doorways."
                ),
            }
        )

    ordered = sorted(kept_doors, key=lambda door: int(door.get("id") or 0))
    retained: List[Dict] = []
    removed_ids: List[int] = []
    for door in ordered:
        duplicate = next(
            (
                other
                for other in retained
                if _same_gap(door, other, DUPLICATE_DOOR_LINE_TOLERANCE_M)
            ),
            None,
        )
        if duplicate is None:
            retained.append(door)
            continue
        if _door_score(door) > _door_score(duplicate):
            retained.remove(duplicate)
            retained.append(door)
            removed_ids.append(int(duplicate.get("id")))
            keeper = door
        else:
            removed_ids.append(int(door.get("id")))
            keeper = duplicate
        repairs.append(
            {
                "code": "duplicate_doorway_removed",
                "object_ids": [keeper.get("id"), removed_ids[-1]],
                "door_id": keeper.get("id"),
                "detail": (
                    "Two doorway reports described the same physical opening on "
                    "opposite wall faces; the better-supported report was kept."
                ),
            }
        )
    if removed_ids:
        removable = set(removed_ids)
        doorways[:] = [
            door for door in doorways if int(door.get("id") or -1) not in removable
        ]
        ambiguities.append(
            {
                "code": "duplicate_doorways_collapsed",
                "object_ids": sorted(removable),
                "message": (
                    f"{len(removable)} duplicate doorway report(s) were collapsed; the "
                    "same gap had been detected on both faces of a wall."
                ),
            }
        )

    remaining: List[Dict] = []
    superseded: List[int] = []
    for window in windows:
        door = next(
            (
                candidate
                for candidate in doorways
                if _same_gap(window, candidate, DUPLICATE_LINE_TOLERANCE_M)
            ),
            None,
        )
        if door is not None:
            superseded.append(int(window.get("id") or -1))
            repairs.append(
                {
                    "code": "window_superseded_by_doorway",
                    "object_ids": [window.get("id"), door.get("id")],
                    "door_id": door.get("id"),
                    "window_id": window.get("id"),
                    "detail": (
                        "A window and a doorway described the same wall gap; the "
                        "doorway was kept so the opening stays walkable."
                    ),
                }
            )
            continue
        remaining.append(window)
    if superseded:
        removable = set(superseded)
        windows[:] = [
            window for window in windows if int(window.get("id") or -1) not in removable
        ]
        ambiguities.append(
            {
                "code": "windows_superseded_by_doorways",
                "object_ids": sorted(removable),
                "message": (
                    f"{len(removable)} window report(s) duplicated a doorway on the same "
                    "gap and were removed so they cannot block the passage."
                ),
            }
        )

    ordered_windows = sorted(
        windows, key=lambda window: int(window.get("id") or 0)
    )
    kept_windows: List[Dict] = []
    dropped: List[int] = []
    for window in ordered_windows:
        duplicate = next(
            (
                other
                for other in kept_windows
                if _same_gap(window, other, 0.45)
            ),
            None,
        )
        if duplicate is None:
            kept_windows.append(window)
            continue
        window_width = float(window.get("width_m") or 0.0)
        other_width = float(duplicate.get("width_m") or 0.0)
        if window_width > other_width + 0.01:
            kept_windows.remove(duplicate)
            kept_windows.append(window)
            dropped.append(int(duplicate.get("id") or -1))
        else:
            dropped.append(int(window.get("id") or -1))
    if dropped:
        removable = set(dropped)
        windows[:] = [
            window for window in windows if int(window.get("id") or -1) not in removable
        ]
        repairs.append(
            {
                "code": "duplicate_window_removed",
                "object_ids": sorted(removable),
                "detail": (
                    "The same window was detected on both faces of a thick wall; one "
                    "report was removed to avoid a duplicate collision volume."
                ),
            }
        )


# ---------------------------------------------------------------------------
# Missing-connection inference
# ---------------------------------------------------------------------------


def _room_type(room: Dict, metadata: Optional[Dict[int, Dict]]) -> str:
    value = room.get("room_type")
    if not value and metadata:
        entry = metadata.get(int(room.get("id", -1))) or {}
        value = entry.get("room_type")
    return str(value or "unknown")


def _type_compatibility(first: str, second: str) -> float:
    pair = {first, second}
    if first == second == "unknown":
        return 0.55
    if "unknown" in pair:
        return 0.55
    if first in CIRCULATION_TYPES or second in CIRCULATION_TYPES:
        return 1.0
    if "bathroom" in pair:
        other = second if first == "bathroom" else first
        if other in CIRCULATION_TYPES:
            return 1.0
        if other in PRIVATE_TYPES:
            return 0.75
        if other == "unknown":
            return 0.45
        return -1.0
    if "kitchen" in pair:
        other = second if first == "kitchen" else first
        if other in {"living_room", "dining_room"}:
            return 1.0
        if other == "bedroom":
            return 0.35
        if other == "balcony":
            return 0.5
        return -1.0
    if first in PRIVATE_TYPES and second in PRIVATE_TYPES:
        return 0.45
    if "balcony" in pair:
        other = second if first == "balcony" else first
        return 1.0 if other in PUBLIC_TYPES | PRIVATE_TYPES else 0.6
    if first == second == "living_room":
        return 0.9
    if first in PUBLIC_TYPES and second in PUBLIC_TYPES:
        return 0.85
    if (first in PRIVATE_TYPES and second in PUBLIC_TYPES) or (
        first in PUBLIC_TYPES and second in PRIVATE_TYPES
    ):
        return 0.65
    return 0.55


def _min_dimension(room: Dict) -> Optional[float]:
    bounds = room.get("bounds_m") or []
    if len(bounds) < 4:
        return None
    try:
        return min(abs(float(bounds[2])), abs(float(bounds[3])))
    except (TypeError, ValueError):
        return None


def _segments(polygon: Polygon) -> List[Tuple[float, float, float, float]]:
    coords = list(polygon.exterior.coords)
    return [
        (coords[index][0], coords[index][1], coords[index + 1][0], coords[index + 1][1])
        for index in range(len(coords) - 1)
    ]


def _shared_face(
    first: Polygon, second: Polygon, max_gap: float
) -> Optional[Dict]:
    """Return the best wall face shared by two room polygons."""

    best: Optional[Dict] = None
    for ax1, az1, ax2, az2 in _segments(first):
        a_dx, a_dz = ax2 - ax1, az2 - az1
        a_length = math.hypot(a_dx, a_dz)
        if a_length < 0.25:
            continue
        a_horizontal = abs(a_dz) <= max(0.12, 0.08 * a_length)
        a_vertical = abs(a_dx) <= max(0.12, 0.08 * a_length)
        if not a_horizontal and not a_vertical:
            continue
        for bx1, bz1, bx2, bz2 in _segments(second):
            b_dx, b_dz = bx2 - bx1, bz2 - bz1
            b_length = math.hypot(b_dx, b_dz)
            if b_length < 0.25:
                continue
            b_horizontal = abs(b_dz) <= max(0.12, 0.08 * b_length)
            b_vertical = abs(b_dx) <= max(0.12, 0.08 * b_length)
            if a_horizontal and b_horizontal:
                line_a = (az1 + az2) / 2.0
                line_b = (bz1 + bz2) / 2.0
                gap = abs(line_a - line_b)
                if gap > max_gap:
                    continue
                low = max(min(ax1, ax2), min(bx1, bx2))
                high = min(max(ax1, ax2), max(bx1, bx2))
                if high - low < INFER_MIN_FACE_M:
                    continue
                candidate = {
                    "orientation": "horizontal",
                    "line": (line_a + line_b) / 2.0,
                    "run": (low, high),
                    "gap": gap,
                }
            elif a_vertical and b_vertical:
                line_a = (ax1 + ax2) / 2.0
                line_b = (bx1 + bx2) / 2.0
                gap = abs(line_a - line_b)
                if gap > max_gap:
                    continue
                low = max(min(az1, az2), min(bz1, bz2))
                high = min(max(az1, az2), max(bz1, bz2))
                if high - low < INFER_MIN_FACE_M:
                    continue
                candidate = {
                    "orientation": "vertical",
                    "line": (line_a + line_b) / 2.0,
                    "run": (low, high),
                    "gap": gap,
                }
            else:
                continue
            score = (candidate["run"][1] - candidate["run"][0]) - candidate["gap"]
            if best is None or score > best["score"]:
                candidate["score"] = score
                best = candidate
    return best


def _subtract(
    interval: Tuple[float, float], blocks: Iterable[Tuple[float, float]]
) -> List[Tuple[float, float]]:
    pieces = [interval]
    for block_low, block_high in blocks:
        updated: List[Tuple[float, float]] = []
        for low, high in pieces:
            if block_high <= low or block_low >= high:
                updated.append((low, high))
                continue
            if block_low > low:
                updated.append((low, block_low))
            if block_high < high:
                updated.append((block_high, high))
        pieces = updated
    return pieces


def _free_intervals(
    face: Dict, doorways: Sequence[Dict], windows: Sequence[Dict]
) -> List[Tuple[float, float]]:
    orientation = face["orientation"]
    run_axis, line_axis = _axes(orientation)
    blocks: List[Tuple[float, float]] = []
    for entry in list(doorways) + list(windows):
        if entry.get("orientation") != orientation:
            continue
        center = entry.get("center_m") or [0.0, 0.0]
        if abs(_value(center, line_axis) - face["line"]) > DUPLICATE_LINE_TOLERANCE_M:
            continue
        width = float(entry.get("width_m") or 0.0)
        if width <= 0:
            continue
        run = _value(center, run_axis)
        blocks.append((run - width / 2.0 - 0.05, run + width / 2.0 + 0.05))
    return [
        (low, high)
        for low, high in _subtract(face["run"], blocks)
        if high - low >= 0.20
    ]


def _wall_panel(
    walls: Sequence[Dict], face: Dict
) -> List[Dict]:
    orientation = face["orientation"]
    run_axis, line_axis = _axes(orientation)
    panel = []
    for wall in walls:
        if wall.get("orientation") != orientation:
            continue
        start = wall.get("start_m") or [0.0, 0.0]
        end = wall.get("end_m") or [0.0, 0.0]
        line = _value(start, line_axis)
        thickness = float(wall.get("thickness_m") or 0.12)
        if abs(line - face["line"]) > max(0.55, thickness + 0.3):
            continue
        wall_low = min(_value(start, run_axis), _value(end, run_axis))
        wall_high = max(_value(start, run_axis), _value(end, run_axis))
        overlap = min(wall_high, face["run"][1]) - max(wall_low, face["run"][0])
        if overlap <= 0.10:
            continue
        panel.append(wall)
    return panel


def _components(
    room_ids: Sequence[int], doorways: Sequence[Dict]
) -> List[List[int]]:
    graph = {room_id: set() for room_id in room_ids}
    for door in doorways:
        rooms = [value for value in (door.get("rooms") or []) if value is not None]
        distinct = sorted({int(value) for value in rooms})
        for index, first in enumerate(distinct):
            for second in distinct[index + 1 :]:
                if first in graph and second in graph:
                    graph[first].add(second)
                    graph[second].add(first)
    seen = set()
    components: List[List[int]] = []
    for room_id in sorted(graph):
        if room_id in seen:
            continue
        stack = [room_id]
        component = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            seen.add(current)
            stack.extend(graph[current] - component)
        components.append(sorted(component))
    return components


def _connection_candidate(
    first_id: int,
    second_id: int,
    rooms: Dict[int, Dict],
    polygons: Dict[int, Polygon],
    walls: Sequence[Dict],
    doorways: Sequence[Dict],
    windows: Sequence[Dict],
    metadata: Optional[Dict[int, Dict]],
) -> Optional[Dict]:
    first_room = rooms.get(first_id)
    second_room = rooms.get(second_id)
    if first_room is None or second_room is None:
        return None
    room_widths = []
    for room in (first_room, second_room):
        minimum = _min_dimension(room)
        if minimum is not None and minimum < HARD_MIN_PASSAGE_M - 0.02:
            return None
        if minimum is not None:
            room_widths.append(minimum)
    compatibility = _type_compatibility(
        _room_type(first_room, metadata), _room_type(second_room, metadata)
    )
    if compatibility < 0:
        return None
    first_polygon = polygons.get(first_id)
    second_polygon = polygons.get(second_id)
    if first_polygon is None or second_polygon is None:
        return None
    face = _shared_face(first_polygon, second_polygon, INFER_MAX_GAP_M)
    if face is None:
        return None
    panel = _wall_panel(walls, face)
    if panel and all(bool(wall.get("exterior")) for wall in panel):
        return None
    free = _free_intervals(face, doorways, windows)
    if not free:
        return None
    face_mid = (face["run"][0] + face["run"][1]) / 2.0
    containing = [piece for piece in free if piece[0] <= face_mid <= piece[1]]
    piece = max(containing, key=lambda item: item[1] - item[0]) if containing else max(
        free, key=lambda item: item[1] - item[0]
    )
    available = piece[1] - piece[0]
    if available < HARD_MIN_PASSAGE_M:
        return None
    # A doorway cannot be wider than the narrower room it opens into.
    width_limit = min(room_widths) - 0.04 if room_widths else INFER_DOOR_WIDTH_M
    width = min(INFER_DOOR_WIDTH_M, available - 0.06, width_limit)
    if width < HARD_MIN_PASSAGE_M:
        return None
    center_run = (piece[0] + piece[1]) / 2.0
    line = face["line"]
    if panel:
        panel_lines = sorted(
            _value(wall.get("start_m") or [0.0, 0.0], _axes(face["orientation"])[1])
            for wall in panel
        )
        preferred = panel_lines[len(panel_lines) // 2]
        if abs(preferred - line) <= 0.45:
            line = preferred
    score = compatibility * 2.0 + min(1.2, available * 0.18) + max(
        0.0, 0.35 - face["gap"]
    )
    return {
        "orientation": face["orientation"],
        "center": (
            [center_run, line]
            if face["orientation"] == "horizontal"
            else [line, center_run]
        ),
        "width": round(width, 3),
        "available": available,
        "compatibility": compatibility,
        "shared_m": round(face["run"][1] - face["run"][0], 3),
        "gap_m": round(face["gap"], 3),
        "score": score,
        "has_wall": bool(panel),
    }


def infer_missing_connections(
    rooms: Sequence[Dict],
    polygons: Dict[int, Polygon],
    walls: Sequence[Dict],
    doorways: List[Dict],
    windows: Sequence[Dict],
    repairs: List[Dict],
    ambiguities: List[Dict],
    room_metadata: Optional[Dict[int, Dict]] = None,
) -> None:
    room_by_id = {
        int(room["id"]): room for room in rooms if room.get("id") is not None
    }
    if len(room_by_id) < 2:
        return
    existing_ids = [
        int(door["id"])
        for door in doorways
        if door.get("id") is not None and str(door.get("id")).lstrip("-").isdigit()
    ]
    next_id = (max(existing_ids) + 1) if existing_ids else 0
    inferred = 0
    while inferred < MAX_INFERRED_CONNECTIONS:
        components = _components(sorted(room_by_id), doorways)
        if len(components) <= 1:
            break
        candidates: List[Dict] = []
        for first_index in range(len(components)):
            for second_index in range(first_index + 1, len(components)):
                first_component = components[first_index]
                second_component = components[second_index]
                for first_id in first_component:
                    for second_id in second_component:
                        candidate = _connection_candidate(
                            first_id,
                            second_id,
                            room_by_id,
                            polygons,
                            walls,
                            doorways,
                            windows,
                            room_metadata,
                        )
                        if candidate is None:
                            continue
                        candidate["first"] = first_id
                        candidate["second"] = second_id
                        smaller = min(len(first_component), len(second_component))
                        larger = max(len(first_component), len(second_component))
                        candidate["score"] += 0.25 * (smaller / max(1, larger))
                        candidates.append(candidate)
        if not candidates:
            break
        best = max(
            candidates,
            key=lambda entry: (entry["score"], -entry["first"], -entry["second"]),
        )
        orientation = best["orientation"]
        open_plan = not best["has_wall"]
        door = {
            "id": next_id,
            "orientation": orientation,
            "center_m": [round(best["center"][0], 3), round(best["center"][1], 3)],
            "width_m": best["width"],
            "detected_width_m": best["width"],
            "effective_width_m": best["width"],
            "rooms": [best["first"], best["second"]],
            "exterior": False,
            "kind": "opening" if open_plan else "door",
            "inferred": True,
            "open_plan": open_plan,
            "inference": {
                "reason": (
                    "open_boundary_between_rooms"
                    if open_plan
                    else "shared_wall_missing_opening"
                ),
                "shared_m": best["shared_m"],
                "wall_gap_m": best["gap_m"],
                "type_compatibility": round(best["compatibility"], 2),
                "confidence": round(min(0.99, best["score"] / 3.2), 2),
            },
        }
        doorways.append(door)
        repairs.append(
            {
                "code": "open_plan_connection_inferred" if open_plan else "doorway_inferred",
                "object_ids": [next_id, best["first"], best["second"]],
                "door_id": next_id,
                "detail": (
                    (
                        f"Rooms {best['first']} and {best['second']} share a "
                        f"{best['shared_m']:.2f} m open boundary with no wall; the rooms "
                        "are treated as one open-plan space."
                    )
                    if open_plan
                    else (
                        f"Rooms {best['first']} and {best['second']} share a "
                        f"{best['shared_m']:.2f} m wall face with no opening; a "
                        f"{best['width']:.2f} m doorway was inferred from the geometry."
                    )
                ),
                "rooms": [best["first"], best["second"]],
                "shared_m": best["shared_m"],
                "wall_gap_m": best["gap_m"],
                "inferred": True,
                "open_plan": open_plan,
            }
        )
        ambiguities.append(
            {
                "code": (
                    "open_plan_boundary_inferred"
                    if open_plan
                    else "doorway_inferred_from_shared_wall"
                ),
                "object_ids": [next_id, best["first"], best["second"]],
                "door_id": next_id,
                "room_id": best["second"],
                "message": (
                    (
                        f"Rooms {best['first']} and {best['second']} meet along an open "
                        "boundary with no wall; they were kept as one continuous space "
                        "instead of inventing a door."
                    )
                    if open_plan
                    else (
                        f"No opening was detected between rooms {best['first']} and "
                        f"{best['second']}, but their shared wall and room types support a "
                        "connection; the missing doorway was inferred."
                    )
                ),
            }
        )
        next_id += 1
        inferred += 1
