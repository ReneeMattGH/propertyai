"""Normalized intermediate house representation and structural validation.

Every reconstruction stage (geometry, furniture, collision, navigation and
spawn selection) works from the same metric coordinate system: x and z in
metres, room polygons being the walkable interior bounded by wall faces and
doorways being openings centred on a wall line.

``normalize_house`` reconciles the raw parser output before meshes exist:

  * invalid room polygons are repaired from their bounds or a valid buffer;
  * duplicate opening reports are collapsed and a doorway wins over a window
    claiming the same gap; narrow exterior "doors" stay windows;
  * doorways are snapped to the wall line they cut, sized from the physical
    gap between wall faces, and moved inside the clear span so the geometry
    builder and collision builder cut the same opening;
  * walls that physically cross a doorway opening are trimmed back;
  * doorway rooms are re-derived by probing the room polygons on both sides;
  * a missing connection is inferred only between rooms that share a real wall
    face when the room types make the connection plausible.

It never invents rooms or walls.  Every inferred doorway is recorded as an
ambiguity with the evidence that justified it; genuinely ambiguous plans are
reported instead of being forced open.
"""
from __future__ import annotations

import copy
import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from shapely.geometry import Point, Polygon, box as shapely_box

from structure_repair import (
    HARD_MIN_PASSAGE_M,
    infer_missing_connections,
    reconcile_openings,
)

PLAYER_RADIUS = 0.24
PLAYER_CLEARANCE_M = 2 * PLAYER_RADIUS + 0.06
MIN_DOOR_CLEAR_M = 0.8
MAX_DOOR_CLEAR_M = 1.8
WALL_HEIGHT = 2.8
DOOR_HEIGHT = 2.1
DOOR_CLEAR_DEPTH = 0.85
DOOR_SIDE_MARGIN = 0.25
WALL_LINE_TOLERANCE_M = 0.45
WALL_TRIM_CLEARANCE_M = 0.05
MIN_WALL_PIECE_M = 0.08
MIN_ROOM_AREA_M2 = 0.35
MODEL_VERSION = "1.0"

RoomId = int
DoorId = int


def _axes(orientation: str) -> Tuple[int, int]:
    """Return (run_axis, line_axis) for a horizontal/vertical opening."""
    return (0, 1) if orientation == "horizontal" else (1, 0)


def _value(point, index: int, default: float = 0.0) -> float:
    try:
        return float(point[index])
    except (TypeError, ValueError, IndexError):
        return default


def _room_polygon(room: Dict) -> Optional[Polygon]:
    points = room.get("polygon_m") or []
    if len(points) >= 3:
        polygon = Polygon([(float(px), float(pz)) for px, pz in points])
    else:
        bounds = room.get("bounds_m") or []
        if len(bounds) < 4:
            return None
        x, z, w, d = (float(value) for value in bounds[:4])
        if w <= 0 or d <= 0:
            return None
        polygon = shapely_box(x, z, x + w, z + d)
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    if polygon.is_empty:
        return None
    if polygon.geom_type == "MultiPolygon":
        polygon = max(polygon.geoms, key=lambda part: part.area)
    if polygon.area < MIN_ROOM_AREA_M2:
        return None
    return polygon


def _coords(polygon: Polygon) -> List[List[float]]:
    ring = list(polygon.exterior.coords)
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring = ring[:-1]
    return [[round(float(x), 3), round(float(z), 3)] for x, z in ring]


def _bounds(polygon: Polygon) -> List[float]:
    min_x, min_z, max_x, max_z = polygon.bounds
    return [
        round(float(min_x), 3),
        round(float(min_z), 3),
        round(float(max_x - min_x), 3),
        round(float(max_z - min_z), 3),
    ]


def _center(polygon: Polygon) -> List[float]:
    point = polygon.representative_point()
    return [round(float(point.x), 3), round(float(point.y), 3)]


def _median(values: Sequence[float], default: float = 0.12) -> float:
    values = sorted(value for value in values if value and value > 0)
    if not values:
        return default
    middle = len(values) // 2
    if len(values) % 2:
        return float(values[middle])
    return float((values[middle - 1] + values[middle]) / 2)


def _room_at(
    polygons: Dict[int, Polygon], x: float, z: float, tolerance: float = 0.04
) -> Optional[int]:
    point = Point(x, z)
    hits = [
        room_id
        for room_id, polygon in polygons.items()
        if polygon.buffer(tolerance).covers(point)
    ]
    return hits[0] if len(hits) == 1 else None


def _subtract_interval(
    segments: Iterable[Tuple[float, float]], cut_start: float, cut_end: float
) -> List[Tuple[float, float]]:
    result: List[Tuple[float, float]] = []
    for start, end in segments:
        if cut_end <= start or cut_start >= end:
            result.append((start, end))
            continue
        if cut_start > start:
            result.append((start, cut_start))
        if cut_end < end:
            result.append((cut_end, end))
    return result


def _normalize_room(room: Dict, repairs: List[Dict], warnings: List[str]) -> bool:
    room_id = room.get("id")
    if room_id is None:
        return False
    polygon = _room_polygon(room)
    if polygon is None:
        return False
    original = room.get("polygon_m") or []
    normalized = _coords(polygon)
    if len(original) < 3 or any(
        abs(float(a) - float(b)) > 0.02
        for old, new in zip(original, normalized)
        for a, b in zip(old, new)
    ) or len(original) != len(normalized):
        room["polygon_m"] = normalized
        if original:
            repairs.append(
                {
                    "code": "room_polygon_repaired",
                    "object_ids": [int(room_id)],
                    "detail": "Room outline was invalid and was repaired from its valid area.",
                    "before": original,
                    "after": normalized,
                }
            )
        else:
            warnings.append(f"Room {room_id} had no outline; its bounds were used.")
    room["bounds_m"] = _bounds(polygon)
    room["center_m"] = _center(polygon)
    if not room.get("spawn_m"):
        room["spawn_m"] = list(room["center_m"])
    room["area_m2"] = round(float(polygon.area), 2)
    return True


def _matching_walls(
    door: Dict, walls: List[Dict], orientation: str
) -> List[Tuple[int, Dict, float]]:
    run_axis, line_axis = _axes(orientation)
    center = door.get("center_m") or [0.0, 0.0]
    line = _value(center, line_axis)
    run = _value(center, run_axis)
    width = float(door.get("detected_width_m") or door.get("width_m") or 0.9)
    half = min(MAX_DOOR_CLEAR_M, max(HARD_MIN_PASSAGE_M, width)) / 2
    matches: List[Tuple[int, Dict, float]] = []
    for index, wall in enumerate(walls):
        if wall.get("orientation") != orientation:
            continue
        start = wall.get("start_m") or [0.0, 0.0]
        end = wall.get("end_m") or [0.0, 0.0]
        wall_line = _value(start, line_axis)
        run_start = min(_value(start, run_axis), _value(end, run_axis))
        run_end = max(_value(start, run_axis), _value(end, run_axis))
        thickness = float(wall.get("thickness_m") or 0.12)
        if abs(wall_line - line) > max(WALL_LINE_TOLERANCE_M, thickness + 0.3):
            continue
        if run < run_start - half - 0.3 or run > run_end + half + 0.3:
            continue
        distance = abs(wall_line - line)
        matches.append((index, wall, distance))
    matches.sort(key=lambda item: (item[2], -float(item[1].get("thickness_m") or 0.0)))
    return matches


def _trim_walls_at_openings(
    walls: List[Dict], doors: List[Dict], repairs: List[Dict]
) -> None:
    """Remove wall runs that physically cross a doorway opening.

    The geometry builder cuts host walls for every doorway.  A second wall that
    happens to span the same opening (a duplicate segment, a mis-clustered wall
    line, or a perpendicular stub through the doorway) is not cut and silently
    blocks the player.  Those walls are trimmed back to the opening edge before
    any mesh or collision volume is generated.
    """
    for _ in range(3):
        changed = False
        for door in doors:
            orientation = door.get("orientation")
            if orientation not in ("horizontal", "vertical"):
                continue
            run_axis, line_axis = _axes(orientation)
            center = door.get("center_m") or [0.0, 0.0]
            door_line = _value(center, line_axis)
            door_run = _value(center, run_axis)
            width = float(door.get("effective_width_m") or door.get("width_m") or 0.9)
            half = min(MAX_DOOR_CLEAR_M, max(HARD_MIN_PASSAGE_M, width)) / 2
            host_thickness = _median(
                [
                    float(wall.get("thickness_m") or 0.12)
                    for wall in walls
                    if wall.get("orientation") == orientation
                    and abs(_value(wall.get("start_m") or [0, 0], line_axis) - door_line)
                    <= max(WALL_LINE_TOLERANCE_M, float(wall.get("thickness_m") or 0.12) + 0.3)
                ]
            )
            replacement: List[Dict] = []
            for wall in walls:
                wall_orientation = wall.get("orientation")
                start = wall.get("start_m") or [0.0, 0.0]
                end = wall.get("end_m") or [0.0, 0.0]
                wall_thickness = float(wall.get("thickness_m") or 0.12)
                if wall_orientation == orientation:
                    wall_line = _value(start, line_axis)
                    wall_run_start = min(_value(start, run_axis), _value(end, run_axis))
                    wall_run_end = max(_value(start, run_axis), _value(end, run_axis))
                    line_distance = abs(wall_line - door_line)
                    if line_distance <= wall_thickness + 0.2:
                        # Host wall: the builder subtracts this opening itself.
                        replacement.append(wall)
                        continue
                    if line_distance > 0.55:
                        replacement.append(wall)
                        continue
                    overlap = min(wall_run_end, door_run + half) - max(
                        wall_run_start, door_run - half
                    )
                    if overlap < 0.1:
                        replacement.append(wall)
                        continue
                    pieces = _subtract_interval(
                        [(wall_run_start, wall_run_end)],
                        door_run - half - WALL_TRIM_CLEARANCE_M,
                        door_run + half + WALL_TRIM_CLEARANCE_M,
                    )
                    pieces = [
                        (piece_start, piece_end)
                        for piece_start, piece_end in pieces
                        if piece_end - piece_start >= MIN_WALL_PIECE_M
                    ]
                    if not pieces:
                        repairs.append(
                            {
                                "code": "blocking_wall_removed",
                                "object_ids": [door.get("id")],
                                "door_id": door.get("id"),
                                "wall_orientation": wall_orientation,
                                "detail": (
                                    "A duplicate wall completely covered the doorway opening "
                                    "and was removed."
                                ),
                            }
                        )
                        changed = True
                        continue
                    if pieces == [(wall_run_start, wall_run_end)]:
                        replacement.append(wall)
                        continue
                    for piece_start, piece_end in pieces:
                        clone = dict(wall)
                        if wall_orientation == "horizontal":
                            clone["start_m"] = [piece_start, wall_line]
                            clone["end_m"] = [piece_end, wall_line]
                        else:
                            clone["start_m"] = [wall_line, piece_start]
                            clone["end_m"] = [wall_line, piece_end]
                        replacement.append(clone)
                    repairs.append(
                        {
                            "code": "blocking_wall_trimmed",
                            "object_ids": [door.get("id")],
                            "door_id": door.get("id"),
                            "wall_orientation": wall_orientation,
                            "detail": (
                                "A wall segment overlapped the doorway opening and was "
                                "trimmed back to the opening edge."
                            ),
                        }
                    )
                    changed = True
                    continue

                if wall_orientation not in ("horizontal", "vertical"):
                    replacement.append(wall)
                    continue
                wall_run_axis, wall_line_axis = _axes(wall_orientation)
                wall_line = _value(start, wall_line_axis)
                wall_run_start = min(_value(start, wall_run_axis), _value(end, wall_run_axis))
                wall_run_end = max(_value(start, wall_run_axis), _value(end, wall_run_axis))
                # A perpendicular wall blocks the doorway only when its wall box
                # physically intrudes into the clear opening: its run must cross
                # the host wall band and its thickness must overlap the opening
                # span.  Walls that merely touch the jamb are left alone.
                band = max(host_thickness, wall_thickness) / 2 + 0.12
                band_start = door_line - band
                band_end = door_line + band
                if wall_run_end <= band_start or wall_run_start >= band_end:
                    replacement.append(wall)
                    continue
                opening_low = door_run - half + 0.02
                opening_high = door_run + half - 0.02
                wall_face_low = wall_line - wall_thickness / 2
                wall_face_high = wall_line + wall_thickness / 2
                if (
                    min(wall_face_high, opening_high)
                    - max(wall_face_low, opening_low)
                    <= 0.02
                ):
                    replacement.append(wall)
                    continue
                pieces = []
                if wall_run_start < band_start - 0.02:
                    pieces.append((wall_run_start, min(wall_run_end, band_start)))
                if wall_run_end > band_end + 0.02:
                    pieces.append((max(wall_run_start, band_end), wall_run_end))
                pieces = [
                    (piece_start, piece_end)
                    for piece_start, piece_end in pieces
                    if piece_end - piece_start >= MIN_WALL_PIECE_M
                ]
                if not pieces:
                    repairs.append(
                        {
                            "code": "blocking_wall_removed",
                            "object_ids": [door.get("id")],
                            "door_id": door.get("id"),
                            "wall_orientation": wall_orientation,
                            "detail": (
                                "A perpendicular wall crossed the doorway band and was "
                                "removed from the opening."
                            ),
                        }
                    )
                    changed = True
                    continue
                for piece_start, piece_end in pieces:
                    clone = dict(wall)
                    if wall_orientation == "horizontal":
                        clone["start_m"] = [piece_start, wall_line]
                        clone["end_m"] = [piece_end, wall_line]
                    else:
                        clone["start_m"] = [wall_line, piece_start]
                        clone["end_m"] = [wall_line, piece_end]
                    replacement.append(clone)
                repairs.append(
                    {
                        "code": "blocking_wall_trimmed",
                        "object_ids": [door.get("id")],
                        "door_id": door.get("id"),
                        "wall_orientation": wall_orientation,
                        "detail": (
                            "A perpendicular wall crossed the doorway opening and was "
                            "trimmed outside the opening band."
                        ),
                    }
                )
                changed = True
            walls[:] = replacement
        if not changed:
            break


def _available_span(
    door: Dict,
    walls: List[Dict],
    orientation: str,
    line: float,
    run: float,
    thickness: float,
) -> Optional[Tuple[float, float, float]]:
    """Return the physical clear span ``(start, end, width)`` at a doorway.

    The span is bounded on each side by collinear wall pieces that end beside
    the opening and by perpendicular walls whose thickness intrudes into the
    doorway band.  A wall that spans the doorway centre is the host wall that
    the geometry builder will cut, so it does not bound the opening by itself.
    """
    run_axis, line_axis = _axes(orientation)
    left = -math.inf
    right = math.inf
    face_tolerance = max(0.30, thickness / 2 + 0.10)
    for wall in walls:
        if wall.get("orientation") != orientation:
            continue
        start = wall.get("start_m") or [0.0, 0.0]
        end = wall.get("end_m") or [0.0, 0.0]
        wall_line = _value(start, line_axis)
        wall_thickness = float(wall.get("thickness_m") or 0.12)
        if abs(wall_line - line) > max(face_tolerance, wall_thickness / 2 + 0.10):
            continue
        low = min(_value(start, run_axis), _value(end, run_axis))
        high = max(_value(start, run_axis), _value(end, run_axis))
        if high <= run + 0.001:
            left = max(left, high)
        elif low >= run - 0.001:
            right = min(right, low)

    pieces: List[Tuple[float, float]] = [(left, right)]
    band = max(0.30, thickness / 2 + 0.20)
    for wall in walls:
        wall_orientation = wall.get("orientation")
        if wall_orientation not in ("horizontal", "vertical"):
            continue
        if wall_orientation == orientation:
            continue
        start = wall.get("start_m") or [0.0, 0.0]
        end = wall.get("end_m") or [0.0, 0.0]
        # A perpendicular wall runs along the doorway's line axis; it narrows
        # the opening only when that run reaches across the host wall band.
        run_low = min(_value(start, line_axis), _value(end, line_axis))
        run_high = max(_value(start, line_axis), _value(end, line_axis))
        if run_high < line - band or run_low > line + band:
            continue
        wall_position = _value(start, run_axis)
        wall_thickness = float(wall.get("thickness_m") or 0.12)
        pieces = _subtract_interval(
            pieces,
            wall_position - wall_thickness / 2 - 0.005,
            wall_position + wall_thickness / 2 + 0.005,
        )

    containing = [
        piece for piece in pieces if piece[0] - 0.02 <= run <= piece[1] + 0.02
    ]
    if not containing:
        return None
    span_start, span_end = min(containing, key=lambda piece: piece[1] - piece[0])
    if not (math.isfinite(span_start) and math.isfinite(span_end)):
        return None
    if span_end - span_start <= 0.05:
        return None
    return span_start, span_end, span_end - span_start


def _room_beside_door(
    polygons: Dict[int, Polygon],
    orientation: str,
    run: float,
    line: float,
    sign: int,
    width: float,
) -> Optional[int]:
    """Probe outward from a doorway until a room polygon is reached.

    A single probe just past the wall face misses rooms whose outline is inset
    by a jamb or a second wall layer, so the probe walks outward and also tests
    offsets along the doorway run.
    """
    run_axis, line_axis = _axes(orientation)
    offsets = (0.0, -0.3 * width, 0.3 * width)
    distance = 0.10
    while distance <= 1.25 + 1e-9:
        for offset in offsets:
            probe_run = run + offset
            probe_line = line + sign * distance
            if orientation == "horizontal":
                point = (probe_run, probe_line)
            else:
                point = (probe_line, probe_run)
            room = _room_at(polygons, point[0], point[1])
            if room is not None:
                return room
        distance += 0.06
    return None


def _normalize_door(
    door: Dict,
    walls: List[Dict],
    polygons: Dict[int, Polygon],
    bounds_by_room: Dict[int, Dict],
    repairs: List[Dict],
    ambiguities: List[Dict],
) -> None:
    orientation = door.get("orientation")
    center = door.get("center_m")
    if orientation not in ("horizontal", "vertical") or not center or len(center) != 2:
        ambiguities.append(
            {
                "code": "doorway_invalid",
                "object_ids": [door.get("id")],
                "message": f"Doorway {door.get('id')} has no usable position or orientation.",
            }
        )
        return
    width = float(door.get("width_m") or 0.9)
    if not math.isfinite(width) or width <= 0:
        door["width_m"] = 0.9
        width = 0.9
        repairs.append(
            {
                "code": "doorway_width_repaired",
                "object_ids": [door.get("id")],
                "detail": "A missing doorway width was replaced with the 0.9 m default.",
            }
        )
    detected = float(door.get("detected_width_m") or width)
    if not math.isfinite(detected) or detected <= 0:
        detected = width
    requested = max(width, detected)
    provisional = min(MAX_DOOR_CLEAR_M, max(MIN_DOOR_CLEAR_M, requested))

    run_axis, line_axis = _axes(orientation)
    matches = _matching_walls(door, walls, orientation)
    host = matches[0][1] if matches else None
    thickness = float(host.get("thickness_m") or 0.12) if host else _median(
        [float(wall.get("thickness_m") or 0.12) for wall in walls]
    )
    wall_line = _value(center, line_axis)
    union_start: Optional[float] = None
    union_end: Optional[float] = None
    if host is None:
        ambiguities.append(
            {
                "code": "doorway_without_wall_support",
                "object_ids": [door.get("id")],
                "door_id": door.get("id"),
                "message": (
                    f"Doorway {door.get('id')} is not supported by a detected wall; "
                    "the opening cannot be cut into wall geometry."
                ),
            }
        )
    else:
        wall_line = _value(host.get("start_m") or [0, 0], line_axis)
        # A doorway usually sits in a gap between two collinear wall segments
        # that the parser emitted separately.  Treating either segment as the
        # sole host and clamping the door into its run moves the opening by up
        # to a metre.  The opening is supported by the union of those segments.
        line_segments = []
        for wall in walls:
            if wall.get("orientation") != orientation:
                continue
            segment_line = _value(wall.get("start_m") or [0, 0], line_axis)
            if abs(segment_line - wall_line) > max(
                WALL_LINE_TOLERANCE_M, thickness + 0.3
            ):
                continue
            line_segments.append(
                (
                    min(
                        _value(wall.get("start_m") or [0, 0], run_axis),
                        _value(wall.get("end_m") or [0, 0], run_axis),
                    ),
                    max(
                        _value(wall.get("start_m") or [0, 0], run_axis),
                        _value(wall.get("end_m") or [0, 0], run_axis),
                    ),
                )
            )
        union_start = min((start for start, _ in line_segments), default=min(
            _value(host.get("start_m") or [0, 0], run_axis),
            _value(host.get("end_m") or [0, 0], run_axis),
        ))
        union_end = max((end for _, end in line_segments), default=max(
            _value(host.get("start_m") or [0, 0], run_axis),
            _value(host.get("end_m") or [0, 0], run_axis),
        ))

    door_run = _value(center, run_axis)
    span = None
    if host is not None:
        span = _available_span(door, walls, orientation, wall_line, door_run, thickness)
    span_low = union_start
    span_high = union_end
    effective = provisional
    if span is not None:
        span_start, span_end, available = span
        if union_start is not None and union_end is not None:
            span_start = max(span_start, union_start)
            span_end = min(span_end, union_end)
            available = max(0.0, span_end - span_start)
        span_low, span_high = span_start, span_end
        if available >= HARD_MIN_PASSAGE_M - 0.001:
            effective = min(provisional, available)
        else:
            effective = HARD_MIN_PASSAGE_M
            ambiguities.append(
                {
                    "code": "doorway_structure_too_narrow",
                    "object_ids": [door.get("id")],
                    "door_id": door.get("id"),
                    "message": (
                        f"Doorway {door.get('id')} has only {available:.2f} m of clear "
                        f"wall span; it was widened to the {HARD_MIN_PASSAGE_M:.2f} m "
                        "minimum passable width."
                    ),
                }
            )
        effective = max(HARD_MIN_PASSAGE_M, min(MAX_DOOR_CLEAR_M, effective))
        if span_end - span_start >= effective:
            low = span_start + effective / 2
            high = span_end - effective / 2
            door_run = min(max(door_run, low), high)
        else:
            door_run = (span_start + span_end) / 2
    elif union_start is not None and union_end is not None:
        low = union_start + effective / 2
        high = union_end - effective / 2
        if low <= high:
            door_run = min(max(door_run, low), high)
        else:
            door_run = (union_start + union_end) / 2

    door["detected_width_m"] = round(detected, 3)
    door["effective_width_m"] = round(effective, 3)
    door["width_m"] = round(effective, 3)
    if span is not None and effective < provisional - 0.01:
        repairs.append(
            {
                "code": "doorway_limited_by_structure",
                "object_ids": [door.get("id")],
                "door_id": door.get("id"),
                "detail": (
                    f"Doorway {door.get('id')} was {width:.2f} m wide but only "
                    f"{effective:.2f} m of clear wall span is available; the opening "
                    "was limited so it cannot cut through the adjoining wall."
                ),
                "before_m": round(width, 3),
                "after_m": effective,
            }
        )
    elif effective > width + 0.01:
        repairs.append(
            {
                "code": "doorway_widened_for_clearance",
                "object_ids": [door.get("id")],
                "door_id": door.get("id"),
                "detail": (
                    f"Doorway {door.get('id')} was {width:.2f} m wide; it was widened to "
                    f"{effective:.2f} m for player clearance."
                ),
                "before_m": round(width, 3),
                "after_m": effective,
            }
        )
    elif effective < width - 0.01 and provisional < width - 0.01:
        repairs.append(
            {
                "code": "doorway_narrowed_to_limit",
                "object_ids": [door.get("id")],
                "door_id": door.get("id"),
                "detail": (
                    f"Doorway {door.get('id')} was {width:.2f} m wide; the opening was "
                    f"limited to {effective:.2f} m so it cannot merge unrelated spaces."
                ),
                "before_m": round(width, 3),
                "after_m": effective,
            }
        )

    new_center = [0.0, 0.0]
    new_center[run_axis] = round(door_run, 3)
    new_center[line_axis] = round(wall_line, 3)
    original_center = [_value(center, 0), _value(center, 1)]
    door["center_m"] = new_center
    if (
        abs(new_center[0] - original_center[0]) > 0.005
        or abs(new_center[1] - original_center[1]) > 0.005
    ):
        span_label = (
            f"spanning {round(span_low, 2)}-{round(span_high, 2)} m"
            if span_low is not None and span_high is not None
            else "along the wall run"
        )
        repairs.append(
            {
                "code": "doorway_snapped_to_wall",
                "object_ids": [door.get("id")],
                "door_id": door.get("id"),
                "detail": (
                    f"Doorway {door.get('id')} was aligned to the wall line {span_label} "
                    "so its opening is actually cut."
                ),
                "before": original_center,
                "after": new_center,
            }
        )
    center = door.get("center_m")
    door_run = _value(center, run_axis)
    door_line = _value(center, line_axis)

    previous = list(door.get("rooms") or [None, None])
    if len(previous) < 2:
        previous = (previous + [None, None])[:2]
    previous = [
        int(value) if value is not None and int(value) in polygons else None
        for value in previous
    ]
    room_a = _room_beside_door(polygons, orientation, door_run, door_line, -1, effective)
    room_b = _room_beside_door(polygons, orientation, door_run, door_line, 1, effective)
    if room_a is None and room_b is None:
        rooms = previous
        room_a = previous[0]
        room_b = previous[1]
    else:
        if room_a is None:
            room_a = next(
                (value for value in previous if value is not None and value != room_b),
                None,
            )
        if room_b is None:
            room_b = next(
                (value for value in previous if value is not None and value != room_a),
                None,
            )
        rooms = [room_a, room_b]
    if room_a is not None and room_b is not None and room_a == room_b:
        ambiguities.append(
            {
                "code": "doorway_within_single_room",
                "object_ids": [door.get("id"), room_a],
                "door_id": door.get("id"),
                "message": (
                    f"Doorway {door.get('id')} opens inside room {room_a} on both sides; "
                    "it is not a room connection."
                ),
            }
        )
        rooms = [room_a, room_b]
    elif room_a is not None and room_b is not None:
        rooms = [room_a, room_b]
    elif room_a is not None and room_b is None:
        rooms = [room_a, None]
    elif room_b is not None and room_a is None:
        rooms = [room_b, None]
    else:
        rooms = previous
    if rooms != previous:
        door["rooms"] = rooms
        repairs.append(
            {
                "code": "doorway_rooms_reconciled",
                "object_ids": [door.get("id")],
                "door_id": door.get("id"),
                "detail": (
                    f"Doorway {door.get('id')} room assignment was reconciled with the "
                    "room outlines on both sides."
                ),
                "before": previous,
                "after": rooms,
            }
        )
    else:
        door["rooms"] = rooms
    door["exterior"] = any(room is None for room in rooms)
    door.setdefault("kind", "door")

    for room_id in rooms:
        if room_id is not None and room_id not in bounds_by_room:
            ambiguities.append(
                {
                    "code": "doorway_unknown_room",
                    "object_ids": [door.get("id"), room_id],
                    "door_id": door.get("id"),
                    "message": f"Doorway {door.get('id')} references unknown room {room_id}.",
                }
            )


def _furniture_constraints(room: Dict, doorways: List[Dict]) -> Dict:
    zones = []
    for door in doorways:
        rooms = [value for value in (door.get("rooms") or []) if value is not None]
        if int(room["id"]) not in rooms:
            continue
        orientation = door.get("orientation")
        center = door.get("center_m") or [0.0, 0.0]
        width = min(1.4, max(0.6, float(door.get("width_m") or 0.9)))
        run_axis, line_axis = _axes(orientation)
        low = _value(center, run_axis) - width / 2 - DOOR_SIDE_MARGIN
        high = _value(center, run_axis) + width / 2 + DOOR_SIDE_MARGIN
        line_low = _value(center, line_axis) - DOOR_CLEAR_DEPTH
        line_high = _value(center, line_axis) + DOOR_CLEAR_DEPTH
        if orientation == "horizontal":
            rect = {"min": [round(low, 3), round(line_low, 3)], "max": [round(high, 3), round(line_high, 3)]}
        else:
            rect = {"min": [round(line_low, 3), round(low, 3)], "max": [round(line_high, 3), round(high, 3)]}
        rect["door_id"] = door.get("id")
        zones.append(rect)
    return {
        "doorway_clearance_zones": zones,
        "player_radius_m": PLAYER_RADIUS,
        "player_clearance_m": round(PLAYER_CLEARANCE_M, 3),
    }


def _wall_model(walls: List[Dict], doors: List[Dict]) -> List[Dict]:
    result = []
    for index, wall in enumerate(walls):
        orientation = wall.get("orientation")
        if orientation not in ("horizontal", "vertical"):
            continue
        run_axis, line_axis = _axes(orientation)
        start = wall.get("start_m") or [0.0, 0.0]
        end = wall.get("end_m") or [0.0, 0.0]
        run_start = min(_value(start, run_axis), _value(end, run_axis))
        run_end = max(_value(start, run_axis), _value(end, run_axis))
        line = _value(start, line_axis)
        thickness = float(wall.get("thickness_m") or 0.12)
        segments = [(run_start, run_end)]
        openings = []
        for door in doors:
            if door.get("orientation") != orientation:
                continue
            center = door.get("center_m") or [0.0, 0.0]
            if abs(_value(center, line_axis) - line) > thickness + 0.2:
                continue
            width = float(door.get("effective_width_m") or door.get("width_m") or 0.9)
            run = _value(center, run_axis)
            if run < run_start - width / 2 or run > run_end + width / 2:
                continue
            cut_start = run - width / 2
            cut_end = run + width / 2
            if cut_start >= run_end or cut_end <= run_start:
                continue
            segments = _subtract_interval(segments, cut_start, cut_end)
            openings.append(
                {
                    "door_id": door.get("id"),
                    "start_m": round(cut_start, 3),
                    "end_m": round(cut_end, 3),
                    "width_m": round(width, 3),
                }
            )
        result.append(
            {
                "index": index,
                "orientation": orientation,
                "line_m": round(line, 3),
                "run_m": [round(run_start, 3), round(run_end, 3)],
                "thickness_m": round(thickness, 3),
                "exterior": bool(wall.get("exterior", False)),
                "openings": openings,
                "solid_segments": [
                    [round(segment_start, 3), round(segment_end, 3)]
                    for segment_start, segment_end in segments
                    if segment_end - segment_start >= MIN_WALL_PIECE_M
                ],
            }
        )
    return result


def validate_house_model(model: Dict) -> Dict:
    errors: List[str] = []
    warnings: List[str] = []
    ambiguities = list(model.get("ambiguities") or [])
    rooms = model.get("rooms") or []
    doorways = model.get("doorways") or []
    if not rooms:
        errors.append("The house has no usable rooms.")
    seen = set()
    for room in rooms:
        room_id = int(room["id"])
        if room_id in seen:
            errors.append(f"Room {room_id} is defined more than once.")
        seen.add(room_id)
        polygon = _room_polygon(room)
        if polygon is None:
            errors.append(f"Room {room_id} has no usable floor polygon.")
            continue
        if not polygon.is_valid:
            errors.append(f"Room {room_id} has an invalid boundary polygon.")
    for door in doorways:
        width = float(door.get("effective_width_m") or door.get("width_m") or 0.0)
        if width + 0.001 < HARD_MIN_PASSAGE_M:
            errors.append(
                f"Doorway {door.get('id')} is {width:.2f} m wide, narrower than the "
                f"{HARD_MIN_PASSAGE_M:.2f} m minimum passable width."
            )
        elif width + 0.001 < PLAYER_CLEARANCE_M:
            warnings.append(
                f"Doorway {door.get('id')} is {width:.2f} m wide; it is passable but "
                f"tighter than the {PLAYER_CLEARANCE_M:.2f} m comfort clearance."
            )
        rooms_for_door = door.get("rooms") or [None, None]
        if not door.get("exterior") and (
            len(rooms_for_door) < 2
            or rooms_for_door[0] is None
            or rooms_for_door[1] is None
            or int(rooms_for_door[0]) == int(rooms_for_door[1])
        ):
            ambiguities.append(
                {
                    "code": "doorway_not_a_connection",
                    "object_ids": [door.get("id")],
                    "door_id": door.get("id"),
                    "message": (
                        f"Doorway {door.get('id')} does not connect two distinct rooms."
                    ),
                }
            )
        if not door.get("wall_openings"):
            warnings.append(
                f"Doorway {door.get('id')} has no matching wall opening; it is an open "
                "passage between rooms."
            )
    provided = {(item.get("code"), tuple(item.get("object_ids") or [])) for item in ambiguities}
    adjacency = model.get("adjacency") or {}
    for room in rooms:
        room_id = int(room["id"])
        if len(rooms) > 1 and not adjacency.get(str(room_id)):
            key = ("room_isolated", (room_id,))
            if key not in provided:
                ambiguities.append(
                    {
                        "code": "room_isolated",
                        "object_ids": [room_id],
                        "room_id": room_id,
                        "message": (
                            f"Room {room_id} has no detected doorway. No opening is invented; "
                            "the room is reported as structurally isolated."
                        ),
                    }
                )
    for ambiguity in ambiguities:
        if ambiguity.get("code") not in {
            "doorway_widened_for_clearance",
            "room_isolated",
        }:
            warnings.append(ambiguity.get("message") or ambiguity.get("code"))
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "ambiguities": ambiguities,
    }


def normalize_house(
    floor_plan_result: Dict,
    room_metadata: Optional[Dict[int, Dict]] = None,
) -> Dict:
    """Return a normalized plan, its house model and a repair/ambiguity log."""
    plan = copy.deepcopy(floor_plan_result or {})
    rooms = list(plan.get("rooms") or [])
    walls = list(plan.get("wall_segments") or [])
    doorways = list(plan.get("doorways") or [])
    windows = list(plan.get("windows") or [])
    repairs: List[Dict] = []
    warnings: List[str] = []
    polygons: Dict[int, Polygon] = {}
    bounds_by_room: Dict[int, Dict] = {}
    usable_rooms = []
    for room in rooms:
        if room.get("id") is None:
            continue
        if not _normalize_room(room, repairs, warnings):
            warnings.append(f"Room {room.get('id')} has no usable floor area and is ignored.")
            continue
        usable_rooms.append(room)
        polygons[int(room["id"])] = _room_polygon(room)
        bounds_by_room[int(room["id"])] = room.get("bounds_m") or []
    plan["rooms"] = usable_rooms
    polygon_union = list(polygons.items())
    for first_index, (first_id, first) in enumerate(polygon_union):
        for second_id, second in polygon_union[first_index + 1 :]:
            if first.intersection(second).area > 0.08:
                warnings.append(
                    f"Room outlines {first_id} and {second_id} overlap; the plan is "
                    "ambiguous and navigation may be conservative."
                )

    ambiguities: List[Dict] = []
    reconcile_openings(doorways, windows, repairs, ambiguities)
    for door in doorways:
        _normalize_door(door, walls, polygons, bounds_by_room, repairs, ambiguities)
    infer_missing_connections(
        usable_rooms,
        polygons,
        walls,
        doorways,
        windows,
        repairs,
        ambiguities,
        room_metadata=room_metadata,
    )

    _trim_walls_at_openings(walls, doorways, repairs)
    plan["wall_segments"] = walls
    plan["doorways"] = doorways
    plan["windows"] = windows

    adjacency: Dict[str, List[int]] = {str(int(room["id"])): [] for room in usable_rooms}
    room_doors: Dict[str, List[int]] = {str(int(room["id"])): [] for room in usable_rooms}
    for door in doorways:
        door_rooms = [value for value in (door.get("rooms") or []) if value is not None]
        for room_id in door_rooms:
            if str(int(room_id)) in room_doors:
                room_doors[str(int(room_id))].append(door.get("id"))
        if len(set(int(value) for value in door_rooms)) >= 2:
            first, second = int(door_rooms[0]), int(door_rooms[1])
            if first != second:
                adjacency[str(first)].append(second)
                adjacency[str(second)].append(first)
    for room in usable_rooms:
        key = str(int(room["id"]))
        room["doorways"] = sorted(set(room_doors.get(key) or []))
        room["neighbors"] = sorted(set(adjacency.get(key) or []))

    wall_models = _wall_model(walls, doorways)
    for door in doorways:
        door["wall_openings"] = [
            {"wall_index": wall["index"], **opening}
            for wall in wall_models
            for opening in wall["openings"]
            if opening.get("door_id") == door.get("id")
        ]

    spawn = resolve_spawn(usable_rooms, doorways)
    if spawn:
        for room in usable_rooms:
            if int(room["id"]) == spawn["room_id"]:
                room["spawn_m"] = list(spawn["position"])
                break
    else:
        warnings.append(
            "No walkable spawn point could be resolved from the room outlines; "
            "the walkthrough cannot start."
        )

    model_rooms = []
    for room in usable_rooms:
        room_id = int(room["id"])
        polygon = polygons[room_id]
        usable = polygon.buffer(-PLAYER_RADIUS)
        model_rooms.append(
            {
                "id": room_id,
                "room_type": room.get("room_type"),
                "polygon_m": _coords(polygon),
                "bounds_m": room.get("bounds_m"),
                "center_m": room.get("center_m"),
                "spawn_m": room.get("spawn_m"),
                "area_m2": room.get("area_m2"),
                "usable_floor_area_m2": round(
                    float(usable.area if not usable.is_empty else polygon.area), 2
                ),
                "doorways": room.get("doorways") or [],
                "neighbors": room.get("neighbors") or [],
                "furniture_constraints": _furniture_constraints(room, doorways),
            }
        )

    model_doorways = []
    for door in doorways:
        orientation = door.get("orientation")
        if orientation not in ("horizontal", "vertical"):
            continue
        width = float(door.get("effective_width_m") or door.get("width_m") or 0.9)
        center = door.get("center_m") or [0.0, 0.0]
        run_axis, line_axis = _axes(orientation)
        model_doorways.append(
            {
                "id": door.get("id"),
                "orientation": orientation,
                "position": [round(_value(center, 0), 3), round(_value(center, 1), 3)],
                "width_m": round(width, 3),
                "detected_width_m": door.get("detected_width_m"),
                "rooms": [
                    int(value) if value is not None else None
                    for value in (door.get("rooms") or [None, None])
                ],
                "exterior": bool(door.get("exterior", False)),
                "kind": door.get("kind") or "door",
                "wall_openings": door.get("wall_openings") or [],
                "passage": {
                    "run_m": [
                        round(_value(center, run_axis) - width / 2, 3),
                        round(_value(center, run_axis) + width / 2, 3),
                    ],
                    "line_m": round(_value(center, line_axis), 3),
                    "clear_height_m": DOOR_HEIGHT,
                },
            }
        )

    model = {
        "version": MODEL_VERSION,
        "coordinate_system": "metres; x/z horizontal, y vertical",
        "rooms": model_rooms,
        "doorways": model_doorways,
        "walls": wall_models,
        "adjacency": {key: sorted(set(values)) for key, values in adjacency.items()},
        "spawn": spawn,
        "repairs": repairs,
        "ambiguities": ambiguities,
        "warnings": warnings,
    }
    validation = validate_house_model(model)
    model["validation"] = validation
    return {
        "plan": plan,
        "model": model,
        "repairs": repairs,
        "ambiguities": validation["ambiguities"],
        "warnings": list(dict.fromkeys(warnings + validation["warnings"])),
        "errors": validation["errors"],
        "valid": validation["valid"],
    }


def safe_spawn_point(room: Dict) -> Optional[List[float]]:
    """Return a point that is inside the room and clear of its wall faces."""
    polygon = _room_polygon(room)
    if polygon is None:
        return None
    preferred = room.get("spawn_m")
    candidates: List[Tuple[float, float]] = []
    if preferred and len(preferred) == 2:
        candidates.append((float(preferred[0]), float(preferred[1])))
    for inset in (PLAYER_RADIUS + 0.1, PLAYER_RADIUS + 0.05, 0.05):
        inner = polygon.buffer(-inset)
        if not inner.is_empty:
            point = inner.representative_point()
            candidates.append((float(point.x), float(point.y)))
    center = room.get("center_m")
    if center and len(center) == 2:
        candidates.append((float(center[0]), float(center[1])))
    for x, z in candidates:
        if polygon.buffer(-0.02).covers(Point(x, z)):
            return [round(x, 3), round(z, 3)]
    point = polygon.representative_point()
    return [round(float(point.x), 3), round(float(point.y), 3)]


def select_spawn_room(rooms: Sequence[Dict], doorways: Sequence[Dict]) -> Optional[Dict]:
    """Prefer the room that contains an exterior door, otherwise the largest."""
    if not rooms:
        return None
    entrance_room_ids = set()
    for door in doorways:
        if not door.get("exterior"):
            continue
        for room_id in door.get("rooms") or []:
            if room_id is not None:
                entrance_room_ids.add(int(room_id))
    pool = [
        room for room in rooms if int(room.get("id", -1)) in entrance_room_ids
    ] or list(rooms)
    return max(pool, key=lambda room: float(room.get("area_m2") or 0.0))


def room_spawn_point(room: Dict, doorways: Sequence[Dict]) -> Optional[List[float]]:
    """Choose a deterministic spawn point near the room's entrance when possible."""
    polygon = _room_polygon(room)
    if polygon is None:
        return None
    room_id = int(room["id"])
    candidates: List[Tuple[float, float]] = []
    exital = [
        door
        for door in doorways
        if door.get("exterior")
        and room_id in [int(value) for value in (door.get("rooms") or []) if value is not None]
    ]
    interior = [
        door
        for door in doorways
        if not door.get("exterior")
        and room_id in [int(value) for value in (door.get("rooms") or []) if value is not None]
    ]
    for door in exital + interior:
        orientation = door.get("orientation")
        center = door.get("center_m") or [0.0, 0.0]
        room_center = room.get("center_m") or [0.0, 0.0]
        if orientation == "horizontal":
            sign = 1.0 if float(room_center[1]) >= float(center[1]) else -1.0
            candidates.append(
                (float(center[0]), float(center[1]) + sign * (PLAYER_CLEARANCE_M + 0.35))
            )
        elif orientation == "vertical":
            sign = 1.0 if float(room_center[0]) >= float(center[0]) else -1.0
            candidates.append(
                (float(center[0]) + sign * (PLAYER_CLEARANCE_M + 0.35), float(center[1]))
            )
    fallback = safe_spawn_point(room)
    if fallback:
        candidates.append((float(fallback[0]), float(fallback[1])))
    clear = polygon.buffer(-(PLAYER_RADIUS + 0.08))
    for x, z in candidates:
        if (clear if not clear.is_empty else polygon).covers(Point(x, z)):
            return [round(x, 3), round(z, 3)]
    return fallback


def resolve_spawn(
    rooms: Sequence[Dict], doorways: Sequence[Dict]
) -> Optional[Dict]:
    room = select_spawn_room(rooms, doorways)
    if room is None:
        return None
    point = room_spawn_point(room, doorways)
    if point is None:
        return None
    return {"room_id": int(room["id"]), "position": point}


def point_hits_boxes(
    x: float, z: float, boxes: Sequence[Dict], margin: float = PLAYER_RADIUS
) -> bool:
    for entry in boxes:
        minimum = entry.get("min") or [0.0, 0.0]
        maximum = entry.get("max") or [0.0, 0.0]
        if (
            float(minimum[0]) - margin <= x <= float(maximum[0]) + margin
            and float(minimum[1]) - margin <= z <= float(maximum[1]) + margin
        ):
            return True
    return False


def clear_spawn_in_room(
    room: Dict,
    walls: Sequence[Dict],
    furniture: Sequence[Dict] = (),
    preferred: Optional[Sequence[float]] = None,
) -> Optional[List[float]]:
    """Find a walkable spawn point in a room, away from walls and furniture."""
    polygon = _room_polygon(room)
    if polygon is None:
        return None
    inner = polygon.buffer(-(PLAYER_RADIUS + 0.06))
    if inner.is_empty:
        inner = polygon
    min_x, min_z, max_x, max_z = inner.bounds
    candidates: List[Tuple[float, float]] = []
    if preferred and len(preferred) >= 2:
        candidates.append((float(preferred[0]), float(preferred[1])))
    resolved = room_spawn_point(room, []) or safe_spawn_point(room)
    if resolved:
        candidates.append((float(resolved[0]), float(resolved[1])))
    point = inner.representative_point()
    candidates.append((float(point.x), float(point.y)))
    step = 0.2
    x = min_x + step / 2
    while x < max_x:
        z = min_z + step / 2
        while z < max_z:
            candidates.append((x, z))
            z += step
        x += step
    for x, z in candidates:
        if not inner.covers(Point(x, z)):
            continue
        if point_hits_boxes(x, z, walls, PLAYER_RADIUS + 0.03):
            continue
        if point_hits_boxes(x, z, furniture, PLAYER_RADIUS + 0.03):
            continue
        return [round(x, 3), round(z, 3)]
    return None
