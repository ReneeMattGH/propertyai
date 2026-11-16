"""Detect staircases from a parsed, axis-aligned floor plan image.

The parser already vectorises walls and rooms. Stair treads are the other
strong repetitive pattern on a plan: a run of thin, evenly spaced parallel
lines. This module isolates black line work (coloured walls, furniture and
dimension lines are ignored), clusters tread lines, merges perpendicular
flights into L-shaped stairs and returns them in metres relative to the same
origin as the rooms and walls.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

# A tread line is only trusted when it is a thin ink stroke of roughly this
# length. Short strokes are text or symbols; long strokes are walls.
MIN_TREAD_LENGTH_M = 0.45
MAX_TREAD_LENGTH_M = 2.8
# Treads are drawn between these spacings in real stair construction.
MIN_TREAD_SPACING_M = 0.14
MAX_TREAD_SPACING_M = 0.5
MIN_TREADS = 5
MIN_RUN_M = 1.8
MIN_WIDTH_M = 0.65
MAX_WIDTH_M = 2.5
# Two perpendicular flights belong to one L-shaped stair when their rectangles
# touch within this distance.
JUNCTION_TOLERANCE_M = 0.4
STAIR_RISE_M = 3.0


def _ink_mask(image: np.ndarray) -> np.ndarray:
    """Black/grey line work only; coloured plan symbology is excluded."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    _, saturation, value = cv2.split(hsv)
    mask = ((value < 150) & (saturation < 90)).astype(np.uint8) * 255
    # Drop single-pixel noise without erasing 1 px tread lines.
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    return mask


def _merge_collinear(raw: Sequence[Tuple[float, float, float]]) -> List[Dict]:
    """Merge Hough fragments that lie on the same tread line."""
    ordered = sorted(raw, key=lambda item: item[0])
    groups: List[Dict] = []
    for position, low, high in ordered:
        merged = False
        for group in groups:
            if abs(group["position"] - position) > 3:
                continue
            overlap = min(group["high"], high) - max(group["low"], low)
            shorter = min(group["high"] - group["low"], high - low)
            if overlap < -0.25 * max(1.0, shorter):
                continue
            count = group["count"]
            group["position"] = (group["position"] * count + position) / (count + 1)
            group["low"] = min(group["low"], low)
            group["high"] = max(group["high"], high)
            group["count"] = count + 1
            merged = True
            break
        if not merged:
            groups.append(
                {
                    "position": float(position),
                    "low": float(low),
                    "high": float(high),
                    "count": 1,
                }
            )
    return groups


def _cluster_treads(lines: Sequence[Dict], scale: float) -> List[List[Dict]]:
    """Group neighbouring tread lines whose spans overlap consistently."""
    clusters: List[Dict] = []
    for line in lines:
        joined = False
        for cluster in clusters:
            previous = cluster["lines"][-1]
            spacing = (line["position"] - previous["position"]) / scale
            overlap = min(previous["high"], line["high"]) - max(
                previous["low"], line["low"]
            )
            shorter = min(
                previous["high"] - previous["low"], line["high"] - line["low"]
            )
            if (
                MIN_TREAD_SPACING_M <= spacing <= MAX_TREAD_SPACING_M
                and overlap >= 0.55 * max(shorter, 1.0)
            ):
                cluster["lines"].append(line)
                joined = True
                break
        if not joined:
            clusters.append({"lines": [line]})
    return [cluster["lines"] for cluster in clusters if len(cluster["lines"]) >= MIN_TREADS]


def _span_coverage(mask: np.ndarray, lines: Sequence[Dict], horizontal: bool) -> float:
    coverages: List[float] = []
    height, width = mask.shape
    for line in lines:
        low = max(0, int(round(line["low"])))
        high = min((width if horizontal else height) - 1, int(round(line["high"])))
        if high <= low:
            continue
        if horizontal:
            row = min(height - 1, max(0, int(round(line["position"]))))
            sample = mask[row, low:high]
        else:
            column = min(width - 1, max(0, int(round(line["position"]))))
            sample = mask[low:high, column]
        if len(sample):
            coverages.append(float(np.count_nonzero(sample)) / len(sample))
    return float(np.mean(coverages)) if coverages else 1.0


def _find_flights(
    mask: np.ndarray,
    origin: Tuple[float, float],
    scale: float,
) -> List[Dict]:
    height, width = mask.shape[:2]
    segments = cv2.HoughLinesP(
        mask,
        1,
        np.pi / 360,
        threshold=30,
        minLineLength=int(MIN_TREAD_LENGTH_M * scale),
        maxLineGap=int(0.22 * scale),
    )
    if segments is None:
        return []
    increments: Dict[str, List[Tuple[float, float, float]]] = {
        "horizontal": [],
        "vertical": [],
    }
    for x1, y1, x2, y2 in segments.reshape(-1, 4):
        length = math.hypot(x2 - x1, y2 - y1) / scale
        if not (MIN_TREAD_LENGTH_M <= length <= MAX_TREAD_LENGTH_M):
            continue
        if abs(y2 - y1) <= 2 and length >= MIN_TREAD_LENGTH_M:
            increments["horizontal"].append(
                (float(min(y1, y2)), float(min(x1, x2)), float(max(x1, x2)))
            )
        elif abs(x2 - x1) <= 2 and length >= MIN_TREAD_LENGTH_M:
            increments["vertical"].append(
                (float(min(x1, x2)), float(min(y1, y2)), float(max(y1, y2)))
            )

    flights: List[Dict] = []
    for orientation, raw in increments.items():
        if len(raw) < MIN_TREADS:
            continue
        horizontal = orientation == "horizontal"
        clusters = _cluster_treads(_merge_collinear(raw), scale)
        for lines in clusters:
            first, last = lines[0], lines[-1]
            low = float(np.median([line["low"] for line in lines]))
            high = float(np.median([line["high"] for line in lines]))
            span_px = high - low
            run_px = last["position"] - first["position"]
            width_m = span_px / scale
            run_m = run_px / scale
            if not (MIN_WIDTH_M <= width_m <= MAX_WIDTH_M and run_m >= MIN_RUN_M):
                continue
            spacing = np.diff([line["position"] for line in lines])
            mean_spacing = float(np.mean(spacing)) if len(spacing) else 1.0
            regularity = float(np.std(spacing)) / max(1e-6, mean_spacing)
            if regularity > 0.35:
                continue
            if horizontal:
                bbox_px = [low, first["position"], span_px, run_px]
            else:
                bbox_px = [first["position"], low, run_px, span_px]
            flights.append(
                {
                    "orientation": orientation,
                    "travel": "z" if horizontal else "x",
                    "bbox_px": bbox_px,
                    "rect_m": [
                        round((bbox_px[0] - origin[0]) / scale, 3),
                        round((bbox_px[1] - origin[1]) / scale, 3),
                        round(bbox_px[2] / scale, 3),
                        round(bbox_px[3] / scale, 3),
                    ],
                    "run_m": round(run_m, 3),
                    "width_m": round(width_m, 3),
                    "treads": len(lines),
                    "spacing_m": round(mean_spacing / scale, 3),
                    "regularity": round(regularity, 3),
                    "dash_coverage": round(_span_coverage(mask, lines, horizontal), 3),
                    "confidence": round(
                        max(0.35, min(0.98, 1.0 - regularity * 1.4))
                        * min(1.0, len(lines) / 8.0),
                        3,
                    ),
                }
            )
    return flights


def _overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def _merge_l_shape(first: Dict, second: Dict) -> Optional[Dict]:
    """Merge two perpendicular flights that meet at a corner landing."""
    if first.get("travel") == second.get("travel"):
        return None
    if first["travel"] == "x":
        x_flight, z_flight = first, second
    else:
        x_flight, z_flight = second, first

    xx, xz, xw, xd = x_flight["rect_m"]  # travels x, width in z
    zx, zz, zw, zd = z_flight["rect_m"]  # travels z, width in x
    x_center_z = xz + xd / 2
    z_center_x = zx + zw / 2
    # The x-travelling flight has to terminate on the width of the z flight,
    # and the z-travelling flight has to terminate on the width of the x flight.
    x_near = xx if abs(xx - z_center_x) <= abs(xx + xw - z_center_x) else xx + xw
    z_near = zz if abs(zz - x_center_z) <= abs(zz + zd - x_center_z) else zz + zd
    x_distance = max(0.0, abs(x_near - max(zx, min(x_near, zx + zw))))
    z_distance = max(0.0, abs(z_near - max(xz, min(z_near, xz + xd))))
    if x_distance > JUNCTION_TOLERANCE_M or z_distance > JUNCTION_TOLERANCE_M:
        return None
    x_far = xx + xw if abs(x_near - xx) < 1e-6 else xx
    z_far = zz + zd if abs(z_near - zz) < 1e-6 else zz
    if abs(x_far - x_near) < 0.8 or abs(z_far - z_near) < 0.8:
        return None

    # Solid treads are below the plan's cut plane; dashed treads are the part
    # above it, so the dashed flight is always the upper one.
    bottom, top = sorted(
        (x_flight, z_flight), key=lambda flight: flight["dash_coverage"], reverse=True
    )
    bottom_travels_x = bottom["travel"] == "x"
    if bottom_travels_x:
        bottom_start = (x_far, x_center_z)
        bottom_near = (x_near, x_center_z)
        top_near = (z_center_x, z_near)
        top_far = (z_center_x, z_far)
    else:
        # Here "top" is the x-travelling flight and "bottom" the z-travelling one.
        bottom_start = (z_center_x, z_far)
        bottom_near = (z_center_x, z_near)
        top_near = (x_near, x_center_z)
        top_far = (x_far, x_center_z)
    corner = (z_center_x, x_center_z)
    path = [bottom_start, bottom_near, corner, top_near, top_far]
    heights = [0.0]
    runs = [bottom["run_m"], top["run_m"]]
    landing_rise = STAIR_RISE_M * runs[0] / max(1e-6, runs[0] + runs[1])
    heights.extend([landing_rise / STAIR_RISE_M] * 3)
    heights.append(1.0)

    bbox_x = min(xx, zx)
    bbox_z = min(xz, zz)
    bbox_w = max(xx + xw, zx + zw) - bbox_x
    bbox_d = max(xz + xd, zz + zd) - bbox_z
    opening_rect = top["rect_m"]
    if top["travel"] == "z":
        opening = [
            round(opening_rect[0] - 0.12, 3),
            round(opening_rect[1] - 0.28, 3),
            round(opening_rect[2] + 0.24, 3),
            round(opening_rect[3] + 0.36, 3),
        ]
    else:
        opening = [
            round(opening_rect[0] - 0.28, 3),
            round(opening_rect[1] - 0.12, 3),
            round(opening_rect[2] + 0.56, 3),
            round(opening_rect[3] + 0.24, 3),
        ]
    return {
        "shape": "L",
        "bbox_m": [round(bbox_x, 3), round(bbox_z, 3), round(bbox_w, 3), round(bbox_d, 3)],
        "bbox_px": first.get("bbox_px"),
        "width_m": round(max(x_flight["width_m"], z_flight["width_m"]), 3),
        "run_m": round(x_flight["run_m"] + z_flight["run_m"], 3),
        "rise_m": STAIR_RISE_M,
        "bottom_m": [round(path[0][0], 3), round(path[0][1], 3)],
        "top_m": [round(path[-1][0], 3), round(path[-1][1], 3)],
        "path_m": [[round(x, 3), round(z, 3)] for x, z in path],
        "path_rise": [round(value, 4) for value in heights],
        "opening_m": opening,
        "flights": [bottom, top],
        "confidence": round(
            min(0.98, (x_flight["confidence"] + z_flight["confidence"]) / 2 + 0.08), 3
        ),
        "treads": int(
            round(
                (x_flight["run_m"] / max(1e-6, x_flight["spacing_m"]))
                + (z_flight["run_m"] / max(1e-6, z_flight["spacing_m"]))
            )
        )
        + 2,
    }


def _single_flight(flight: Dict) -> Dict:
    rect = flight["rect_m"]
    if flight["travel"] == "x":
        z = rect[1] + rect[3] / 2
        start = (rect[0], z)
        end = (rect[0] + rect[2], z)
        opening = [rect[0] - 0.28, rect[1] - 0.12, rect[2] + 0.56, rect[3] + 0.24]
    else:
        x = rect[0] + rect[2] / 2
        start = (x, rect[1])
        end = (x, rect[1] + rect[3])
        opening = [rect[0] - 0.12, rect[1] - 0.28, rect[2] + 0.24, rect[3] + 0.56]
    return {
        "shape": "straight",
        "bbox_m": rect,
        "bbox_px": flight.get("bbox_px"),
        "width_m": flight["width_m"],
        "run_m": flight["run_m"],
        "rise_m": STAIR_RISE_M,
        "bottom_m": [round(start[0], 3), round(start[1], 3)],
        "top_m": [round(end[0], 3), round(end[1], 3)],
        "path_m": [
            [round(start[0], 3), round(start[1], 3)],
            [round(end[0], 3), round(end[1], 3)],
        ],
        "path_rise": [0.0, 1.0],
        "opening_m": [round(value, 3) for value in opening],
        "flights": [flight],
        "confidence": flight["confidence"],
        "treads": int(round(flight["run_m"] / max(1e-6, flight["spacing_m"]))) + 1,
    }


def detect_stairs(
    image: np.ndarray,
    origin: Tuple[float, float],
    scale_pixels_per_meter: float,
) -> List[Dict]:
    """Return detected staircases in metres, ordered by confidence."""
    if image is None or image.size == 0:
        return []
    scale = float(scale_pixels_per_meter)
    if scale <= 1:
        return []
    mask = _ink_mask(image)
    flights = _find_flights(mask, origin, scale)
    if not flights:
        return []

    # Prefer the densest cluster when two detectors see the same treads.
    flights.sort(key=lambda flight: flight["confidence"], reverse=True)
    kept: List[Dict] = []
    for flight in flights:
        fx, fz, fw, fd = flight["rect_m"]
        duplicate = False
        for other in kept:
            ox, oz, ow, od = other["rect_m"]
            if other["orientation"] != flight["orientation"]:
                continue
            overlap_w = _overlap(fx, fx + fw, ox, ox + ow)
            overlap_d = _overlap(fz, fz + fd, oz, oz + od)
            if overlap_w * overlap_d > 0.45 * min(fw * fd, ow * od):
                duplicate = True
                break
        if not duplicate:
            kept.append(flight)

    stairs: List[Dict] = []
    used = set()
    for index, flight in enumerate(kept):
        if index in used:
            continue
        best: Optional[Tuple[float, int, Dict]] = None
        for other_index, other in enumerate(kept):
            if other_index == index or other_index in used:
                continue
            merged = _merge_l_shape(flight, other)
            if merged is None:
                continue
            score = merged["confidence"] + min(flight["run_m"], other["run_m"]) * 0.01
            if best is None or score > best[0]:
                best = (score, other_index, merged)
        if best is not None:
            used.add(index)
            used.add(best[1])
            stairs.append(best[2])
        else:
            used.add(index)
            single = _single_flight(flight)
            # Very wide tick clusters are usually two parallel dog-leg runs
            # drawn side by side. They are kept for diagnostics but not
            # treated as a single walkable flight.
            single["ambiguous_width"] = flight["width_m"] > 1.6
            stairs.append(single)

    for stair_id, stair in enumerate(stairs):
        stair["id"] = stair_id
    stairs.sort(key=lambda stair: stair["confidence"], reverse=True)
    return stairs
