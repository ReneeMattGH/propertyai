"""
Floor plan parser.

Pipeline:
  1. Load and validate the plan image (PDF rasterisation is optional).
  2. Select a wall mask that separates structural line work from floors and
     furniture, trying Otsu/adaptive thresholds in both polarities.
  3. Extract axis-aligned wall bands with run-length analysis, then scan each
     wall line for intervals and gaps. Gaps are classified as drawing noise,
     doorways, windows or open passages.
  4. Seal only plausible door/window gaps (plus tiny raster gaps), segment
     rooms from the resulting barrier, and retry with stronger closure if a
     drawing still leaks.
  5. Detect door swing arcs to distinguish doors from windows where possible.
  6. Estimate scale from user-supplied dimensions or from typical room sizes.

Slightly rotated scans are straightened from their dominant wall angle before
line extraction; only axis-aligned walls are reconstructed, and diagonal walls
are reported as a limitation. All outputs are plain dicts so they can be
persisted as JSON. Every stage writes intermediate images so a failed parse is
inspectable.
"""
from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

MIN_IMAGE_SIDE = 250
MAX_IMAGE_SIDE = 5000
MIN_WALL_PIXEL_RATIO = 0.002
MAX_WALL_PIXEL_RATIO = 0.65
DEFAULT_HOUSE_WIDTH_M = 12.0
DEFAULT_ROOM_MIN_SIDE_M = 3.2
MIN_DOOR_M = 0.45
MAX_DOOR_M = 2.2
DOOR_ARC_THRESHOLD = 0.30
MIN_USABLE_ROOM_AREA_M2 = 0.55
MIN_USABLE_ROOM_SIDE_M = 0.45
MAX_SKEW_ANGLE_DEG = 20.0
MIN_SKEW_ANGLE_DEG = 1.0
MIN_SKEW_SUPPORT = 0.35


class FloorPlanParseError(Exception):
    pass


def _load_rgb_image(path: Path) -> Image.Image:
    with Image.open(path) as im:
        im.load()
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            background = Image.new("RGBA", im.size, (255, 255, 255, 255))
            im = Image.alpha_composite(background, im)
        return im.convert("RGB")


def _rasterize_pdf(path: Path, job_id: str) -> Path:
    try:
        from pdf2image import convert_from_path
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise FloorPlanParseError(
            "PDF floor plans require the pdf2image package and the poppler utilities "
            "(pdftoppm). Export the plan as PNG/JPG instead."
        ) from exc
    try:
        pages = convert_from_path(str(path), dpi=200, first_page=1, last_page=1)
    except Exception as exc:
        raise FloorPlanParseError(
            "Could not rasterise the PDF floor plan. Install poppler (pdftoppm) or "
            f"upload a PNG/JPG. Details: {exc}"
        ) from exc
    if not pages:
        raise FloorPlanParseError("The PDF contains no pages")
    target = path.parent / f"{path.stem}_page1.png"
    pages[0].save(target)
    return target


def _run_lengths(mask: np.ndarray) -> np.ndarray:
    """Length of the horizontal foreground run ending at each pixel."""
    out = np.zeros(mask.shape, dtype=np.int32)
    for y in range(mask.shape[0]):
        row = mask[y] > 0
        if not row.any():
            continue
        changes = np.flatnonzero(np.diff(np.concatenate(([False], row, [False]))))
        for start, end in zip(changes[::2], changes[1::2]):
            out[y, start:end] = end - start
    return out


def _clean_specks(binary: np.ndarray, min_area: int) -> np.ndarray:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    clean = np.zeros_like(binary)
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_AREA] >= min_area:
            clean[labels == label] = 255
    return clean


class ImprovedFloorPlanParser:
    """Extract rooms, walls, doorways, windows and scale from a floor plan image."""

    def check_opencv(self) -> bool:
        try:
            import cv2  # noqa: F401

            return True
        except ImportError:
            return False

    def check_pillow(self) -> bool:
        try:
            from PIL import Image  # noqa: F401

            return True
        except ImportError:
            return False

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------
    async def validate_inputs(
        self, floor_plan_path: str, photo_paths: Optional[List[str]] = None
    ) -> Dict:
        return await asyncio.to_thread(
            self._validate_inputs_sync, floor_plan_path, photo_paths or []
        )

    def _validate_inputs_sync(
        self, floor_plan_path: str, photo_paths: Optional[List[str]] = None
    ) -> Dict:
        photo_paths = list(photo_paths or [])
        result: Dict = {
            "floor_plan_valid": False,
            "photos_valid": True,
            "photos_required": False,
            "floor_plan": {},
            "photos": {"count": len(photo_paths), "valid_count": 0, "invalid": []},
            "errors": [],
            "warnings": [],
        }
        plan_path = Path(floor_plan_path)
        if not plan_path.exists():
            result["errors"].append("Floor plan file not found")
        elif plan_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".pdf"}:
            result["errors"].append(
                f"Unsupported floor plan format '{plan_path.suffix}'. Allowed: PNG, JPG, JPEG, PDF"
            )
        else:
            try:
                image = self._read_plan_image(plan_path, "validation")
                height, width = image.shape[:2]
                gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                wall_ratio = float(np.mean(gray < 128))
                plan_info = {
                    "width_px": int(width),
                    "height_px": int(height),
                    "wall_pixel_ratio": round(wall_ratio, 4),
                    "contrast": round(float(np.std(gray)), 2),
                }
                errors = []
                if min(width, height) < MIN_IMAGE_SIDE:
                    errors.append(
                        f"Floor plan is too small ({width}x{height}px). "
                        f"Minimum side is {MIN_IMAGE_SIDE}px."
                    )
                if plan_info["contrast"] < 8:
                    errors.append("Floor plan appears blank or has no visible line work.")
                if wall_ratio < MIN_WALL_PIXEL_RATIO:
                    errors.append("No wall line work detected in the floor plan.")
                if wall_ratio > MAX_WALL_PIXEL_RATIO:
                    errors.append(
                        "Floor plan is too dark to interpret (wall pixels cover most of the image)."
                    )
                plan_info["errors"] = errors
                result["floor_plan"] = plan_info
                result["errors"].extend(errors)
                result["floor_plan_valid"] = not errors
            except Exception as exc:
                result["errors"].append(str(exc))

        valid_photos = 0
        for photo_path in photo_paths:
            photo = Path(photo_path)
            try:
                with Image.open(photo) as im:
                    im.load()
                    width, height = im.size
                if min(width, height) < 160:
                    raise ValueError(f"image too small ({width}x{height}px)")
                if photo.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                    raise ValueError(f"unsupported format '{photo.suffix}'")
                valid_photos += 1
            except (UnidentifiedImageError, OSError, ValueError) as exc:
                result["photos"]["invalid"].append({"file": photo.name, "error": str(exc)})
        result["photos"]["valid_count"] = valid_photos
        result["photos_valid"] = valid_photos == len(photo_paths)
        if photo_paths and valid_photos == 0:
            result["errors"].append("No readable property photographs were provided.")
        elif valid_photos < len(photo_paths):
            result["errors"].append(
                f"{len(photo_paths) - valid_photos} photograph(s) could not be read."
            )
        return result

    def _read_plan_image(self, path: Path, job_id: str) -> np.ndarray:
        if path.suffix.lower() == ".pdf":
            path = _rasterize_pdf(path, job_id)
        image = _load_rgb_image(path)
        if max(image.size) > MAX_IMAGE_SIDE:
            scale = MAX_IMAGE_SIDE / max(image.size)
            image = image.resize(
                (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                Image.LANCZOS,
            )
        return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------
    async def parse_floor_plan(
        self,
        job_id: str,
        floor_plan_path: str,
        known_width: Optional[float],
        known_length: Optional[float],
        progress_callback=None,
        preprocessing: Optional[Dict] = None,
    ) -> Dict:
        return await asyncio.to_thread(
            self._parse_floor_plan_sync,
            job_id,
            floor_plan_path,
            known_width,
            known_length,
            progress_callback,
            preprocessing,
        )

    def _parse_floor_plan_sync(
        self,
        job_id: str,
        floor_plan_path: str,
        known_width: Optional[float],
        known_length: Optional[float],
        progress_callback=None,
        preprocessing: Optional[Dict] = None,
    ) -> Dict:
        def emit(stage: str):
            if progress_callback is None:
                return
            try:
                progress_callback(stage)
            except Exception:
                pass

        warnings: List[str] = []
        preprocessing = preprocessing or {}
        wall_polarity = str(preprocessing.get("wall_polarity") or "auto").lower()
        if wall_polarity not in {"auto", "dark", "light"}:
            wall_polarity = "auto"
        sealing_style = str(preprocessing.get("sealing") or "auto").lower()
        if sealing_style not in {"auto", "minimal", "aggressive"}:
            sealing_style = "auto"
        options_used = {"wall_polarity": wall_polarity, "sealing": sealing_style}
        try:
            image = self._read_plan_image(Path(floor_plan_path), job_id)
        except FloorPlanParseError as exc:
            return {"status": "failed", "error": str(exc), "error_code": "unreadable_plan"}
        except Exception as exc:
            return {
                "status": "failed",
                "error": f"Could not read floor plan: {exc}",
                "error_code": "unreadable_plan",
            }

        height, width = image.shape[:2]
        diagnostics_dir = Path(__file__).parent / "geometry" / job_id
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        self._write_normalized(diagnostics_dir, image, width, height)
        emit("preprocessing")

        if min(width, height) < MIN_IMAGE_SIDE:
            return {
                "status": "failed",
                "error": f"Floor plan is too small ({width}x{height}px). "
                f"Minimum side is {MIN_IMAGE_SIDE}px.",
                "error_code": "plan_too_small",
            }

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        min_side = min(width, height)
        min_line = max(14, int(min_side * 0.02))

        wall_mask, mask_meta, probe = self._extract_wall_mask(
            gray, min_line, warnings, known_width, known_length, wall_polarity
        )

        deskew_deg = 0.0
        skew = self._detect_skew_angle(wall_mask, min_side)
        if abs(skew) >= MIN_SKEW_ANGLE_DEG:
            # A single resampled angle can leave thin, anti-aliased walls on a
            # pixel boundary where run-length extraction under-reads them. Probe
            # the detected angle and two small perturbations, then keep the
            # candidate whose wall mask actually yields the most valid rooms.
            source_image = image
            candidates = []
            for offset in (0.0, -0.2, 0.2):
                angle = float(skew + offset)
                candidate_image = self._rotate_plan_image(source_image, angle)
                candidate_gray = cv2.cvtColor(candidate_image, cv2.COLOR_BGR2GRAY)
                candidate_side = min(candidate_image.shape[:2])
                candidate_line = max(14, int(candidate_side * 0.02))
                candidate_warnings: List[str] = []
                candidate_mask, candidate_meta, candidate_probe = self._extract_wall_mask(
                    candidate_gray,
                    candidate_line,
                    candidate_warnings,
                    known_width,
                    known_length,
                    wall_polarity,
                )
                candidate_probe = candidate_probe or {}
                candidates.append(
                    {
                        "angle": angle,
                        "image": candidate_image,
                        "gray": candidate_gray,
                        "mask": candidate_mask,
                        "meta": candidate_meta,
                        "probe": candidate_probe,
                        "warnings": candidate_warnings,
                        "valid": bool(candidate_probe.get("valid")),
                        "rooms": len(candidate_probe.get("rooms") or []),
                        "coverage": float(candidate_probe.get("coverage") or 0.0),
                    }
                )
            best = max(
                candidates,
                key=lambda entry: (
                    entry["valid"],
                    entry["rooms"],
                    entry["coverage"],
                    -abs(entry["angle"] - skew),
                ),
            )
            deskew_deg = best["angle"]
            image = best["image"]
            gray = best["gray"]
            wall_mask = best["mask"]
            mask_meta = best["meta"]
            probe = best["probe"]
            warnings.extend(best["warnings"])
            height, width = image.shape[:2]
            min_side = min(width, height)
            min_line = max(14, int(min_side * 0.02))
            self._write_normalized(diagnostics_dir, image, width, height)
            warnings.append(
                f"The plan was rotated {abs(deskew_deg):.1f}°; it was straightened from its "
                "dominant wall direction before reconstruction."
            )
        emit("detecting_rooms")
        cv2.imwrite(str(diagnostics_dir / "walls_mask.png"), wall_mask)
        wall_ratio = float(np.count_nonzero(wall_mask)) / float(width * height)
        if wall_ratio < MIN_WALL_PIXEL_RATIO:
            return self._fail_with_diagnostics(
                job_id,
                diagnostics_dir,
                image,
                wall_mask,
                None,
                "No wall line work was detected. Use a clearer plan with visible wall "
                "lines and neutral background.",
                "no_wall_pixels",
                warnings,
                {"wall_pixel_ratio": round(wall_ratio, 5), "mask": mask_meta},
            )

        probe = probe or {}
        segments = probe.get("segments") or []
        if not segments:
            return self._fail_with_diagnostics(
                job_id,
                diagnostics_dir,
                image,
                wall_mask,
                None,
                "No straight wall runs could be extracted. The plan may be blank, skewed, "
                "or drawn with diagonal walls; upload a clearer orthographic plan.",
                "no_wall_segments",
                warnings,
                {"mask": mask_meta, "structural": probe.get("structural")},
            )

        lines = probe.get("lines") or []
        line_diagnostics = probe.get("line_diagnostics") or {}
        if not lines:
            return self._fail_with_diagnostics(
                job_id,
                diagnostics_dir,
                image,
                wall_mask,
                None,
                "Wall lines were found but none were long enough to form a building. "
                "Upload a plan where exterior walls are continuous and visible.",
                "no_wall_lines",
                warnings,
                {"mask": mask_meta, "structural": probe.get("structural")},
            )

        walls_bbox = probe.get("bbox") or self._bounds_of_lines(lines)
        if walls_bbox["width"] < min_line * 2 or walls_bbox["height"] < min_line * 2:
            return self._fail_with_diagnostics(
                job_id,
                diagnostics_dir,
                image,
                wall_mask,
                None,
                "Detected wall work is too small to reconstruct.",
                "walls_too_small",
                warnings,
                {"walls_bbox": walls_bbox, "mask": mask_meta},
            )

        provisional_scale = probe.get("provisional_scale") or self._provisional_scale(
            walls_bbox, known_width, known_length
        )
        door_max_px = max(10, int(MAX_DOOR_M * provisional_scale))
        seal_caps = probe.get("caps") or {
            "interior_px": max(door_max_px + max(4, int(0.01 * min_side)), int(0.08 * min_side)),
            "exterior_px": max(int(2.4 * door_max_px), int(0.22 * min_side)),
        }

        attempts: List[Dict] = []
        selected = None
        for attempt in self._segmentation_attempts(seal_caps, sealing_style):
            built = self._build_barrier(
                lines, wall_mask, walls_bbox, attempt["interior_px"], attempt["exterior_px"]
            )
            rooms, room_labels, stats = self._segment_rooms(
                built["barrier"], (height, width), warnings
            )
            record = {
                "name": attempt["name"],
                "kernel": attempt["kernel"],
                "interior_seal_px": attempt["interior_px"],
                "exterior_seal_px": attempt["exterior_px"],
                "rooms": len(rooms),
                "gap_count": len(built["gaps"]),
                "border_leak_area": stats["border_area"],
                "largest_component_area": stats["largest_area"],
            }
            attempts.append(record)
            if rooms and selected is None:
                selected = {
                    "attempt": attempt,
                    "built": built,
                    "rooms": rooms,
                    "labels": room_labels,
                    "stats": stats,
                }
                if attempt["kernel"] == 0 and not attempt.get("seal_all"):
                    break

        if selected is None:
            built = self._build_barrier(
                lines,
                wall_mask,
                walls_bbox,
                seal_caps["interior_px"],
                seal_caps["exterior_px"],
            )
            preview_barrier = built["barrier"]
            error = (
                "No enclosed rooms were found. The parser detected walls and openings but "
                "free space still connects to the image border, so no room could be closed. "
                "The detected walls, openings and leak map are saved for preview. You can "
                "retry with a different preprocessing option or mark room outlines manually."
            )
            result = self._fail_with_diagnostics(
                job_id,
                diagnostics_dir,
                image,
                wall_mask,
                preview_barrier,
                error,
                "no_enclosed_rooms",
                warnings,
                {
                    "mask": mask_meta,
                    "structural": probe.get("structural") if probe else None,
                    "walls_bbox": walls_bbox,
                    "attempts": attempts,
                    "wall_pixel_ratio": round(wall_ratio, 5),
                },
                lines=lines,
                gaps=built["gaps"],
            )
            result["preview"] = {
                "walls_mask": f"geometry/{job_id}/walls_mask.png",
                "barrier": f"geometry/{job_id}/barrier.png",
                "attempts": f"geometry/{job_id}/segmentation_attempts.png",
                "normalized": f"geometry/{job_id}/floor_plan_normalized.png",
            }
            result["correction_supported"] = True
            return result

        attempt = selected["attempt"]
        built = selected["built"]
        rooms = selected["rooms"]
        room_labels = selected["labels"]
        gaps = built["gaps"]
        wall_segments = built["wall_segments"]

        scale, scale_source, scale_verified = self._resolve_scale(
            walls_bbox, rooms, known_width, known_length, warnings
        )
        rooms = self._drop_unusable_rooms(rooms, scale, warnings)
        if not rooms:
            return self._fail_with_diagnostics(
                job_id,
                diagnostics_dir,
                image,
                wall_mask,
                built["barrier"],
                "Only wall cavities or slivers were detected; no usable room remains. "
                "Upload a clearer plan with visible room outlines.",
                "no_usable_rooms",
                warnings,
                {"walls_bbox": walls_bbox, "mask": mask_meta},
            )

        edge_mask = self._build_arc_edge_mask(gray, built["barrier"])
        openings, doorways, windows = self._classify_openings(
            gaps, room_labels, rooms, built["barrier"], edge_mask, scale, warnings
        )
        emit("validating_layout")

        self._flag_diagonals(wall_mask, warnings)

        origin_x = walls_bbox["x"]
        origin_y = walls_bbox["y"]
        for room in rooms:
            x, y, w, h = room["bounds_px"]
            room["bounds_m"] = [
                round((x - origin_x) / scale, 3),
                round((y - origin_y) / scale, 3),
                round(w / scale, 3),
                round(h / scale, 3),
            ]
            room["center_m"] = [
                round((room["center_px"][0] - origin_x) / scale, 3),
                round((room["center_px"][1] - origin_y) / scale, 3),
            ]
            room["spawn_m"] = [
                round((room["spawn_px"][0] - origin_x) / scale, 3),
                round((room["spawn_px"][1] - origin_y) / scale, 3),
            ]
            room["area_m2"] = round(room["area_px"] / (scale * scale), 2)
            room["polygon_m"] = [
                [round((px - origin_x) / scale, 3), round((py - origin_y) / scale, 3)]
                for px, py in room["polygon_px"]
            ]
            if room.get("fill_ratio", 1.0) < 0.82:
                warnings.append(
                    f"Room {room['id'] + 1} is not a simple rectangle; its walkable area "
                    "is approximated by the detected outline."
                )

        for wall in wall_segments:
            wall["start_m"] = [
                round((wall["start_px"][0] - origin_x) / scale, 3),
                round((wall["start_px"][1] - origin_y) / scale, 3),
            ]
            wall["end_m"] = [
                round((wall["end_px"][0] - origin_x) / scale, 3),
                round((wall["end_px"][1] - origin_y) / scale, 3),
            ]
            wall["thickness_m"] = round(
                min(0.45, max(0.08, wall["thickness_px"] / scale)), 3
            )

        for doorway in doorways:
            doorway["center_m"] = [
                round((doorway["center_px"][0] - origin_x) / scale, 3),
                round((doorway["center_px"][1] - origin_y) / scale, 3),
            ]
        for window in windows:
            window["center_m"] = [
                round((window["center_px"][0] - origin_x) / scale, 3),
                round((window["center_px"][1] - origin_y) / scale, 3),
            ]
        for opening in openings:
            opening["center_m"] = [
                round((opening["center_px"][0] - origin_x) / scale, 3),
                round((opening["center_px"][1] - origin_y) / scale, 3),
            ]
            opening.pop("span_px", None)

        narrow_doorways = [door for door in doorways if door["width_m"] < 0.7]
        if narrow_doorways:
            widths = ", ".join(f"{door['width_m']} m" for door in narrow_doorways)
            warnings.append(
                f"{len(narrow_doorways)} doorway(s) were detected narrower than 0.7 m "
                f"({widths}); each will be widened to 0.8 m for passability."
            )
        unresolved = [room["id"] + 1 for room in rooms if not room.get("connected", True)]
        if unresolved:
            warnings.append(
                "Room(s) "
                + ", ".join(str(room_id) for room_id in unresolved)
                + " have no detected opening. They may be unreachable in the walkthrough."
            )
        if not doorways:
            warnings.append(
                "No doorway-sized gaps were detected. The reconstruction will not have "
                "passable interior openings."
            )
        if windows:
            warnings.append(
                f"{len(windows)} window opening(s) were identified on exterior walls. "
                "Window sill and head heights are inferred at 0.9 m and 2.1 m."
            )

        scale_verified = bool(scale_verified)
        if not scale_verified:
            warnings.append(
                "Scale was estimated from room sizes, not from verified dimensions. All "
                "measurements are approximate."
            )

        if options_used != {"wall_polarity": "auto", "sealing": "auto"}:
            warnings.append(
                "This reconstruction used user-selected preprocessing "
                f"(wall polarity: {options_used['wall_polarity']}, "
                f"gap closing: {options_used['sealing']})."
            )

        warnings = list(dict.fromkeys(warnings))
        self._save_success_diagnostics(
            diagnostics_dir,
            image,
            wall_mask,
            built["barrier"],
            rooms,
            doorways,
            windows,
            attempts,
        )

        segmentation_info = {
            "selected_attempt": attempt["name"],
            "preprocessing": options_used,
            "deskew_deg": round(deskew_deg, 2),
            "attempts": attempts,
            "gap_count": len(gaps),
            "sealed_gaps": sum(1 for gap in gaps if gap.get("sealed")),
            "open_passages": sum(1 for gap in gaps if not gap.get("sealed")),
            "wall_lines": len(lines),
            "line_diagnostics": line_diagnostics,
            "mask": mask_meta,
            "provisional_scale_px_per_m": round(provisional_scale, 3),
            "seal_caps_px": seal_caps,
            "diagnostics_dir": f"geometry/{job_id}",
        }

        return {
            "status": "completed",
            "image_dimensions": [int(width), int(height)],
            "preprocessing": options_used,
            "deskew_deg": round(deskew_deg, 2),
            "inverted_drawing": bool(mask_meta.get("inverted")),
            "wall_pixel_ratio": round(wall_ratio, 4),
            "scale_pixels_per_meter": round(scale, 3),
            "scale_source": scale_source,
            "scale_verified": scale_verified,
            "footprint_px": walls_bbox,
            "footprint_m": {
                "width": round(walls_bbox["width"] / scale, 3),
                "height": round(walls_bbox["height"] / scale, 3),
            },
            "rooms": rooms,
            "wall_segments": wall_segments,
            "doorways": doorways,
            "windows": windows,
            "openings": openings,
            "segmentation": segmentation_info,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # Rotation correction
    # ------------------------------------------------------------------
    def _detect_skew_angle(self, wall_mask: np.ndarray, min_side: int) -> float:
        """Return the dominant wall angle when the drawing is slightly rotated.

        Only small corrections are attempted. A plan whose longest line work is
        actually diagonal (45° architecture, dimension callouts, text) is left
        untouched so the existing diagonal-wall limitation still applies
        instead of silently inventing an axis-aligned layout.
        """
        if wall_mask is None or not np.any(wall_mask):
            return 0.0
        min_length = max(24, int(min_side * 0.08))
        segments = cv2.HoughLinesP(
            wall_mask,
            1,
            np.pi / 360,
            threshold=40,
            minLineLength=min_length,
            maxLineGap=4,
        )
        if segments is None:
            return 0.0
        total = 0.0
        entries: List[Tuple[float, float]] = []
        for x1, y1, x2, y2 in segments.reshape(-1, 4):
            length = float(math.hypot(x2 - x1, y2 - y1))
            if length < min_length:
                continue
            total += length
            angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
            angle = ((angle + 45.0) % 90.0) - 45.0
            if -MAX_SKEW_ANGLE_DEG <= angle <= MAX_SKEW_ANGLE_DEG:
                entries.append((angle, length))
        if len(entries) < 5 or total <= 0:
            return 0.0
        bins = np.zeros(int(MAX_SKEW_ANGLE_DEG * 8) + 1)
        for angle, length in entries:
            index = int(round((angle + MAX_SKEW_ANGLE_DEG) * 4))
            if 0 <= index < len(bins):
                bins[index] += length
        peak_index = int(np.argmax(bins))
        if bins[peak_index] <= 0:
            return 0.0
        peak = peak_index / 4.0 - MAX_SKEW_ANGLE_DEG
        selected = [(angle, length) for angle, length in entries if abs(angle - peak) <= 1.5]
        support = sum(length for _, length in selected)
        if support / total < MIN_SKEW_SUPPORT:
            return 0.0
        angle = sum(angle * length for angle, length in selected) / support
        if abs(angle) < MIN_SKEW_ANGLE_DEG or abs(angle) > MAX_SKEW_ANGLE_DEG:
            return 0.0
        return angle

    def _rotate_plan_image(self, image: np.ndarray, angle_deg: float) -> np.ndarray:
        height, width = image.shape[:2]
        matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), angle_deg, 1.0)
        cos = abs(matrix[0, 0])
        sin = abs(matrix[0, 1])
        new_width = max(1, int(height * sin + width * cos))
        new_height = max(1, int(height * cos + width * sin))
        matrix[0, 2] += new_width / 2.0 - width / 2.0
        matrix[1, 2] += new_height / 2.0 - height / 2.0
        border = np.median(
            np.concatenate([image[0, :], image[-1, :], image[:, 0], image[:, -1]], axis=0),
            axis=0,
        )
        return cv2.warpAffine(
            image,
            matrix,
            (new_width, new_height),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=tuple(int(value) for value in border),
        )

    # ------------------------------------------------------------------
    # Mask selection
    # ------------------------------------------------------------------
    def _extract_wall_mask(
        self,
        gray: np.ndarray,
        min_line: int,
        warnings: List[str],
        known_width: Optional[float] = None,
        known_length: Optional[float] = None,
        polarity: str = "auto",
    ) -> Tuple[np.ndarray, Dict, Optional[Dict]]:
        height, width = gray.shape
        smooth = cv2.GaussianBlur(gray, (3, 3), 0)
        min_side = min(height, width)
        max_thickness = max(6, int(0.12 * min_side))

        candidates: List[Dict] = []

        def add_candidate(source: np.ndarray, threshold: int, method: str, inverted: bool):
            _, binary = cv2.threshold(source, threshold, 255, cv2.THRESH_BINARY_INV)
            binary = _clean_specks(binary, max(20, int(0.00002 * height * width)))
            foreground = float(np.count_nonzero(binary)) / float(height * width)
            if foreground < 0.0005 or foreground > 0.72:
                return
            score, detail = self._mask_structure_score(binary, min_line, max_thickness)
            candidates.append(
                {
                    "mask": binary,
                    "threshold": int(threshold),
                    "method": method,
                    "inverted": inverted,
                    "foreground_ratio": foreground,
                    "score": score,
                    "detail": detail,
                }
            )

        otsu, _ = cv2.threshold(smooth, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        otsu = int(round(float(otsu)))
        percentiles = [int(np.percentile(smooth, 5)), int(np.percentile(smooth, 10))]
        dark_thresholds = {otsu, 128}
        dark_thresholds.update(min(otsu, value + 8) for value in percentiles)
        for threshold in sorted(dark_thresholds):
            add_candidate(smooth, max(12, min(245, threshold)), "otsu/percentile", False)

        block = max(21, int(min_side * 0.06) // 2 * 2 + 1)
        try:
            adaptive = cv2.adaptiveThreshold(
                smooth, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, block, 9
            )
            adaptive = _clean_specks(adaptive, max(20, int(0.00002 * height * width)))
            foreground = float(np.count_nonzero(adaptive)) / float(height * width)
            if 0.0005 <= foreground <= 0.72:
                score, detail = self._mask_structure_score(adaptive, min_line, max_thickness)
                candidates.append(
                    {
                        "mask": adaptive,
                        "threshold": None,
                        "method": "adaptive_gaussian",
                        "inverted": False,
                        "foreground_ratio": foreground,
                        "score": score,
                        "detail": detail,
                    }
                )
        except cv2.error:
            pass

        inverted_source = 255 - smooth
        inverted_thresholds = {otsu, int(np.percentile(inverted_source, 10)) + 8}
        for threshold in sorted(inverted_thresholds):
            add_candidate(
                inverted_source,
                max(12, min(245, threshold)),
                "otsu/percentile (light lines)",
                True,
            )

        if polarity in {"dark", "light"}:
            wanted_inverted = polarity == "light"
            matching = [item for item in candidates if item["inverted"] == wanted_inverted]
            if not matching:
                warnings.append(
                    f"Requested {'light' if wanted_inverted else 'dark'}-line wall "
                    "analysis found no usable wall candidates in this floor plan."
                )
            candidates = matching

        if not candidates:
            fallback = np.zeros_like(gray)
            return fallback, {
                "method": "none",
                "inverted": polarity == "light",
                "score": 0.0,
                "foreground_ratio": 0.0,
                "selection": "none",
                "requested_polarity": polarity,
            }, None

        return self._select_wall_mask(
            candidates, min_line, min_side, known_width, known_length, warnings, polarity
        )

    def _select_wall_mask(
        self,
        candidates: List[Dict],
        min_line: int,
        min_side: int,
        known_width: Optional[float],
        known_length: Optional[float],
        warnings: List[str],
        requested_polarity: str = "auto",
    ) -> Tuple[np.ndarray, Dict, Optional[Dict]]:
        """Pick the mask whose layout is physically plausible, not merely the busiest.

        A low threshold or adaptive mask captures door arcs, dimension lines and
        text as well as walls.  Those extra marks can cut rooms into fragments, so
        structural pixel counts alone are not a reliable selector.  Each candidate
        is probed through line extraction and room segmentation and ranked by
        plausibility of the resulting building.
        """
        for candidate in candidates:
            candidate["probe"] = self._probe_layout(
                candidate["mask"], min_line, min_side, known_width, known_length
            )

        def layout_distance(a: Dict, b: Dict) -> int:
            probe_a, probe_b = a.get("probe") or {}, b.get("probe") or {}
            if len(probe_a.get("rooms") or []) != len(probe_b.get("rooms") or []):
                return 2
            footprint_a = probe_a.get("footprint_m") or {}
            footprint_b = probe_b.get("footprint_m") or {}
            if not footprint_a or not footprint_b:
                return 2
            for axis in ("width", "height"):
                value = footprint_a.get(axis, 0) or 0
                reference = footprint_b.get(axis, 0) or 0
                if reference <= 0 or abs(value - reference) / reference > 0.05:
                    return 1
            coverage_a = probe_a.get("coverage") or 0
            coverage_b = probe_b.get("coverage") or 0
            if abs(coverage_a - coverage_b) > 0.05:
                return 1
            return 0

        valid = [c for c in candidates if c.get("probe") and c["probe"].get("valid")]
        clusters: List[List[Dict]] = []
        for candidate in sorted(valid, key=lambda item: item["score"], reverse=True):
            for cluster in clusters:
                if layout_distance(candidate, cluster[0]) <= 1:
                    cluster.append(candidate)
                    break
            else:
                clusters.append([candidate])

        best: Dict
        cluster_size = 0
        cluster_count = 0
        if clusters:
            best_cluster = max(
                clusters,
                key=lambda cluster: (
                    len(cluster),
                    max(float(item["probe"].get("score") or 0) for item in cluster),
                    max(item["score"] for item in cluster),
                ),
            )
            cluster_size = len(best_cluster)
            cluster_count = len(clusters)
            best = max(
                best_cluster,
                key=lambda item: (
                    round(float((item["probe"].get("coverage") or 0)), 2),
                    item["score"],
                ),
            )
        else:
            best = max(candidates, key=lambda item: item["score"])
        probe = best.get("probe")
        layout_valid = bool(probe and probe.get("valid"))

        if best["inverted"]:
            warnings.append(
                "Floor plan appears to use light lines on a dark background; the parser "
                "inverted the image for analysis."
            )
        if best["foreground_ratio"] > 0.45:
            warnings.append(
                "The wall mask covers a large part of the image; floors or textures may "
                "be included. Detected walls were filtered by thickness."
            )
        if not layout_valid:
            rejected = [
                item["method"]
                for item in candidates
                if item.get("probe") and not item["probe"].get("valid")
            ]
            if rejected:
                warnings.append(
                    "No threshold produced a physically plausible room layout; the parser "
                    "used the strongest wall structure instead. Review the wall preview."
                )

        candidate_summaries = []
        for item in sorted(candidates, key=lambda entry: entry["score"], reverse=True):
            item_probe = item.get("probe") or {}
            candidate_summaries.append(
                {
                    "method": item["method"],
                    "threshold": item["threshold"],
                    "inverted": item["inverted"],
                    "foreground_ratio": round(item["foreground_ratio"], 4),
                    "structural_score": round(item["score"], 2),
                    "rooms": len(item_probe.get("rooms") or []),
                    "layout_valid": bool(item_probe.get("valid")),
                    "layout_issues": item_probe.get("issues") or [],
                    "footprint_m": item_probe.get("footprint_m"),
                    "scale_pixels_per_meter": item_probe.get("scale_pixels_per_meter"),
                    "room_coverage": item_probe.get("coverage"),
                    "selected": item is best,
                }
            )

        meta = {
            "method": best["method"],
            "threshold": best["threshold"],
            "inverted": best["inverted"],
            "foreground_ratio": round(best["foreground_ratio"], 4),
            "score": round(best["score"], 2),
            "candidate_count": len(candidates),
            "selection": "layout_validated" if layout_valid else "structural_score",
            "requested_polarity": requested_polarity,
            "layout_clusters": cluster_count,
            "selected_cluster_size": cluster_size,
            "selected_layout": {
                "valid": layout_valid,
                "rooms": len(probe.get("rooms") or []) if probe else 0,
                "issues": probe.get("issues") if probe else [],
                "score": round(float(probe.get("score") or 0.0), 3) if probe else 0.0,
            },
            "candidates": candidate_summaries[:8],
        }
        return best["mask"], meta, probe

    def _probe_layout(
        self,
        mask: np.ndarray,
        min_line: int,
        min_side: int,
        known_width: Optional[float],
        known_length: Optional[float],
    ) -> Dict:
        structural = self._extract_structural_bands(mask, min_line, min_side)
        segments = structural["segments"]
        probe: Dict = {
            "segments": segments,
            "structural": structural["diagnostics"],
            "lines": [],
            "line_diagnostics": {},
            "bbox": None,
            "caps": None,
            "provisional_scale": None,
            "rooms": [],
            "coverage": 0.0,
            "valid": False,
            "issues": [],
            "score": 0.0,
        }
        if not segments:
            probe["issues"].append("no structural wall runs")
            return probe

        lines, line_diagnostics = self._group_wall_lines(
            mask, segments, min_line, min_side, []
        )
        probe["lines"] = lines
        probe["line_diagnostics"] = line_diagnostics
        if not lines:
            probe["issues"].append("no stable wall lines")
            return probe

        bbox = self._bounds_of_lines(lines)
        probe["bbox"] = bbox
        if bbox["width"] < min_line * 2 or bbox["height"] < min_line * 2:
            probe["issues"].append("wall footprint too small")
            return probe

        provisional_scale = self._provisional_scale(bbox, known_width, known_length)
        probe["provisional_scale"] = provisional_scale
        door_max_px = max(10, int(MAX_DOOR_M * provisional_scale))
        caps = {
            "interior_px": max(
                door_max_px + max(4, int(0.01 * min_side)), int(0.08 * min_side)
            ),
            "exterior_px": max(int(2.4 * door_max_px), int(0.22 * min_side)),
        }
        probe["caps"] = caps

        built = self._build_barrier(
            lines, mask, bbox, caps["interior_px"], caps["exterior_px"]
        )
        rooms, _, _ = self._segment_rooms(built["barrier"], mask.shape, [])
        probe["rooms"] = rooms
        if not rooms:
            probe["issues"].append("no enclosed rooms")
            return probe

        scale, _, _ = self._resolve_scale(
            bbox, rooms, known_width, known_length, []
        )
        probe["scale_pixels_per_meter"] = float(scale)
        width_m = bbox["width"] / scale
        height_m = bbox["height"] / scale
        probe["footprint_m"] = {"width": round(width_m, 2), "height": round(height_m, 2)}
        footprint_area = bbox["width"] * bbox["height"]
        coverage = sum(room["area_px"] for room in rooms) / float(footprint_area or 1)
        probe["coverage"] = round(coverage, 3)
        median_side_m = float(
            np.median([min(room["bounds_px"][2], room["bounds_px"][3]) for room in rooms])
        ) / scale
        probe["median_room_min_side_m"] = round(median_side_m, 2)

        issues = probe["issues"]
        if not 3.0 <= width_m <= 35.0 or not 3.0 <= height_m <= 45.0:
            issues.append(
                f"implausible building size {width_m:.1f}x{height_m:.1f} m"
            )
        if not 1.6 <= median_side_m <= 12.0:
            issues.append(f"implausible median room width {median_side_m:.1f} m")
        if not 0.25 <= coverage <= 0.97:
            issues.append(f"implausible room coverage {coverage:.0%}")
        room_density_limit = max(4.0, (width_m * height_m) / 6.0)
        if len(rooms) > room_density_limit:
            issues.append(f"{len(rooms)} room fragments for a {width_m * height_m:.0f} m2 plan")

        probe["valid"] = not issues
        probe["score"] = round(
            coverage + min(0.25, len(rooms) / 40.0), 4
        )
        return probe

    def _mask_structure_score(
        self, binary: np.ndarray, min_line: int, max_thickness: int
    ) -> Tuple[float, Dict]:
        horizontal, vertical = self._classify_structural(binary, min_line)
        segments = self._segments_from_structural(
            horizontal, vertical, min_line, max_thickness
        )
        total_length = sum(segment["length_px"] for segment in segments)
        foreground = max(1, int(np.count_nonzero(binary)))
        structural_ratio = sum(
            segment["length_px"] * max(1, segment["thickness_px"]) for segment in segments
        ) / foreground
        score = total_length * (0.5 + min(1.0, structural_ratio))
        return float(score), {
            "segments": len(segments),
            "total_wall_length": int(total_length),
            "structural_ratio": round(float(structural_ratio), 3),
        }

    def _classify_structural(
        self, mask: np.ndarray, min_line: int, ratio: float = 2.0
    ) -> Tuple[np.ndarray, np.ndarray]:
        horizontal_runs = _run_lengths(mask)
        vertical_runs = _run_lengths(mask.T).T
        horizontal = (
            (horizontal_runs >= min_line)
            & (horizontal_runs >= ratio * np.maximum(1, vertical_runs))
        ).astype(np.uint8) * 255
        vertical = (
            (vertical_runs >= min_line)
            & (vertical_runs >= ratio * np.maximum(1, horizontal_runs))
        ).astype(np.uint8) * 255
        return horizontal, vertical

    def _segments_from_structural(
        self,
        horizontal: np.ndarray,
        vertical: np.ndarray,
        min_line: int,
        max_thickness: int,
    ) -> List[Dict]:
        segments: List[Dict] = []
        for mask, orientation in ((horizontal, "horizontal"), (vertical, "vertical")):
            count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
            for index in range(1, count):
                x, y, w, h, area = (
                    int(stats[index, cv2.CC_STAT_LEFT]),
                    int(stats[index, cv2.CC_STAT_TOP]),
                    int(stats[index, cv2.CC_STAT_WIDTH]),
                    int(stats[index, cv2.CC_STAT_HEIGHT]),
                    int(stats[index, cv2.CC_STAT_AREA]),
                )
                if area < min_line:
                    continue
                if orientation == "horizontal":
                    if w < min_line or h > max_thickness:
                        continue
                    segments.append(
                        {
                            "orientation": orientation,
                            "start_px": [x, y + h // 2],
                            "end_px": [x + w, y + h // 2],
                            "thickness_px": h,
                            "length_px": w,
                        }
                    )
                else:
                    if h < min_line or w > max_thickness:
                        continue
                    segments.append(
                        {
                            "orientation": orientation,
                            "start_px": [x + w // 2, y],
                            "end_px": [x + w // 2, y + h],
                            "thickness_px": w,
                            "length_px": h,
                        }
                    )
        return segments

    def _extract_structural_bands(
        self, mask: np.ndarray, min_line: int, min_side: int
    ) -> Dict:
        horizontal, vertical = self._classify_structural(mask, min_line)
        max_thickness = max(6, int(0.12 * min_side))
        segments = self._segments_from_structural(
            horizontal, vertical, min_line, max_thickness
        )
        return {
            "segments": segments,
            "diagnostics": {
                "horizontal_pixels": int(np.count_nonzero(horizontal)),
                "vertical_pixels": int(np.count_nonzero(vertical)),
                "segments": len(segments),
                "max_thickness_px": max_thickness,
            },
        }

    # ------------------------------------------------------------------
    # Wall lines and gap scanning
    # ------------------------------------------------------------------
    def _group_wall_lines(
        self,
        wall_mask: np.ndarray,
        segments: List[Dict],
        min_line: int,
        min_side: int,
        warnings: List[str],
    ) -> Tuple[List[Dict], Dict]:
        clusters: List[Dict] = []
        for orientation in ("horizontal", "vertical"):
            group = [s for s in segments if s["orientation"] == orientation]
            group.sort(key=lambda s: s["start_px"][1] if orientation == "horizontal" else s["start_px"][0])
            line_clusters: List[Dict] = []
            for segment in group:
                position = (
                    segment["start_px"][1]
                    if orientation == "horizontal"
                    else segment["start_px"][0]
                )
                placed = False
                for cluster in line_clusters:
                    tolerance = max(
                        3, int(0.5 * min(segment["thickness_px"], cluster["thickness_px"]))
                    )
                    if abs(position - cluster["pos"]) <= tolerance:
                        cluster["segments"].append(segment)
                        cluster["pos"] = float(
                            np.mean(
                                [
                                    item["start_px"][1]
                                    if orientation == "horizontal"
                                    else item["start_px"][0]
                                    for item in cluster["segments"]
                                ]
                            )
                        )
                        cluster["thickness_px"] = int(
                            round(
                                np.median(
                                    [item["thickness_px"] for item in cluster["segments"]]
                                )
                            )
                        )
                        placed = True
                        break
                if not placed:
                    line_clusters.append(
                        {
                            "orientation": orientation,
                            "pos": float(position),
                            "thickness_px": segment["thickness_px"],
                            "segments": [segment],
                        }
                    )
            clusters.extend(line_clusters)

        weighted = [
            segment["thickness_px"]
            for segment in segments
            for _ in range(max(1, segment["length_px"] // 8))
            if segment["length_px"] >= min_line
        ]
        thickness_hint = float(np.median(weighted)) if weighted else 1.0
        min_line_thickness = max(1, int(thickness_hint * 0.5)) if thickness_hint >= 3 else 1

        lines: List[Dict] = []
        for cluster in clusters:
            total_segment_length = sum(item["length_px"] for item in cluster["segments"])
            if total_segment_length < max(min_line, int(0.02 * min_side)):
                continue
            if cluster["thickness_px"] < min_line_thickness:
                continue
            intervals = self._scan_wall_line(wall_mask, cluster, min_line)
            if not intervals:
                continue
            cluster["intervals"] = intervals
            cluster["pos"] = int(round(cluster["pos"]))
            cluster["thickness_px"] = max(1, int(cluster["thickness_px"]))
            lines.append(cluster)

        if not lines:
            warnings.append(
                "No stable wall lines were found; check that the plan is orthographic and "
                "that wall thickness is consistent."
            )
        diagnostics = {
            "thickness_hint_px": round(thickness_hint, 2),
            "minimum_line_thickness_px": min_line_thickness,
            "candidate_clusters": len(clusters),
            "kept_lines": len(lines),
        }
        return lines, diagnostics

    def _scan_wall_line(
        self, wall_mask: np.ndarray, line: Dict, min_line: int
    ) -> List[Tuple[int, int]]:
        orientation = line["orientation"]
        position = int(round(line["pos"]))
        thickness = max(1, int(line["thickness_px"]))
        half = thickness // 2
        margin = 1
        if orientation == "horizontal":
            lower = max(0, position - half - margin)
            upper = min(wall_mask.shape[0], position + half + margin + 1)
            band = wall_mask[lower:upper, :]
            coverage = (band > 0).sum(axis=0)
        else:
            lower = max(0, position - half - margin)
            upper = min(wall_mask.shape[1], position + half + margin + 1)
            band = wall_mask[:, lower:upper]
            coverage = (band > 0).sum(axis=1)

        required = max(1, int(math.ceil(thickness * 0.4)))
        present = (coverage >= required).astype(np.uint8)
        if present.size == 0:
            return []
        present = cv2.morphologyEx(
            present.reshape(1, -1), cv2.MORPH_CLOSE, np.ones((1, 3), np.uint8)
        ).reshape(-1)
        changes = np.flatnonzero(np.diff(np.concatenate(([0], present, [0]))))
        intervals = [
            (int(start), int(end))
            for start, end in zip(changes[::2], changes[1::2])
            if end - start >= 3
        ]
        merged: List[Tuple[int, int]] = []
        for start, end in intervals:
            if merged and start - merged[-1][1] <= 2:
                merged[-1] = (merged[-1][0], end)
            else:
                merged.append((start, end))
        minimum_length = max(4, min_line // 2)
        return [item for item in merged if item[1] - item[0] >= minimum_length]

    def _bounds_of_lines(self, lines: List[Dict]) -> Dict:
        xs: List[float] = []
        ys: List[float] = []
        for line in lines:
            half = line["thickness_px"] / 2.0
            for start, end in line["intervals"]:
                if line["orientation"] == "horizontal":
                    xs.extend([start, end])
                    ys.extend([line["pos"] - half, line["pos"] + half])
                else:
                    ys.extend([start, end])
                    xs.extend([line["pos"] - half, line["pos"] + half])
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        return {
            "x": int(round(min_x)),
            "y": int(round(min_y)),
            "width": int(round(max_x - min_x)),
            "height": int(round(max_y - min_y)),
        }

    def _make_wall_segments(self, lines: List[Dict], walls_bbox: Dict) -> List[Dict]:
        segments: List[Dict] = []
        boundary_tolerance = 0.0
        for line in lines:
            orientation = line["orientation"]
            if orientation == "horizontal":
                exterior = min(
                    abs(line["pos"] - walls_bbox["y"]),
                    abs(line["pos"] - (walls_bbox["y"] + walls_bbox["height"])),
                ) <= max(8, line["thickness_px"])
            else:
                exterior = min(
                    abs(line["pos"] - walls_bbox["x"]),
                    abs(line["pos"] - (walls_bbox["x"] + walls_bbox["width"])),
                ) <= max(8, line["thickness_px"])
            line["exterior"] = bool(exterior)
            for start, end in line["intervals"]:
                if end - start <= 1:
                    continue
                if orientation == "horizontal":
                    start_px = [int(start), int(line["pos"])]
                    end_px = [int(end), int(line["pos"])]
                else:
                    start_px = [int(line["pos"]), int(start)]
                    end_px = [int(line["pos"]), int(end)]
                segments.append(
                    {
                        "id": len(segments),
                        "orientation": orientation,
                        "start_px": start_px,
                        "end_px": end_px,
                        "thickness_px": int(line["thickness_px"]),
                        "length_px": int(end - start),
                        "exterior": bool(exterior),
                    }
                )
        return segments

    # ------------------------------------------------------------------
    # Barrier and adaptive segmentation
    # ------------------------------------------------------------------
    def _segmentation_attempts(self, seal_caps: Dict, style: str = "auto") -> List[Dict]:
        primary = {
            "name": "primary",
            "kernel": 0,
            "interior_px": seal_caps["interior_px"],
            "exterior_px": seal_caps["exterior_px"],
        }
        close_3 = {
            "name": "morph_close_3",
            "kernel": 3,
            "interior_px": seal_caps["interior_px"],
            "exterior_px": seal_caps["exterior_px"],
        }
        wide = {
            "name": "seal_wide_exterior_and_interior",
            "kernel": 0,
            "seal_all": True,
            "interior_px": int(seal_caps["interior_px"] * 1.8),
            "exterior_px": int(seal_caps["exterior_px"] * 1.8),
        }
        close_5 = {
            "name": "morph_close_5",
            "kernel": 5,
            "seal_all": True,
            "interior_px": int(seal_caps["interior_px"] * 1.8),
            "exterior_px": int(seal_caps["exterior_px"] * 1.8),
        }
        close_9 = {
            "name": "morph_close_9",
            "kernel": 9,
            "seal_all": True,
            "interior_px": int(seal_caps["interior_px"] * 2.2),
            "exterior_px": int(seal_caps["exterior_px"] * 2.2),
        }
        if style == "minimal":
            return [primary, close_3]
        if style == "aggressive":
            return [wide, close_5, close_9, primary]
        return [primary, close_3, wide, close_5, close_9]

    def _build_barrier(
        self,
        lines: List[Dict],
        wall_mask: np.ndarray,
        walls_bbox: Dict,
        interior_seal_px: int,
        exterior_seal_px: int,
    ) -> Dict:
        height, width = wall_mask.shape
        barrier = np.zeros((height, width), dtype=np.uint8)
        wall_segments = self._make_wall_segments(lines, walls_bbox)
        gaps: List[Dict] = []

        for line in lines:
            orientation = line["orientation"]
            position = int(line["pos"])
            thickness = max(1, int(line["thickness_px"]))
            half = thickness // 2
            intervals = line["intervals"]
            for start, end in intervals:
                if orientation == "horizontal":
                    cv2.rectangle(
                        barrier, (start, position - half), (end, position + half), 255, -1
                    )
                else:
                    cv2.rectangle(
                        barrier, (position - half, start), (position + half, end), 255, -1
                    )
            seal_cap = exterior_seal_px if line.get("exterior") else interior_seal_px
            for (start_a, end_a), (start_b, end_b) in zip(intervals, intervals[1:]):
                gap_width = start_b - end_a
                if gap_width <= 0:
                    continue
                sealed = gap_width <= seal_cap
                if gap_width <= 3:
                    sealed = True
                gap = {
                    "id": len(gaps),
                    "orientation": orientation,
                    "line_px": position,
                    "span_px": [int(end_a), int(start_b)],
                    "width_px": int(gap_width),
                    "wall_thickness_px": thickness,
                    "exterior": bool(line.get("exterior")),
                    "sealed": bool(sealed),
                    "seal_reason": (
                        "drawing_gap"
                        if gap_width <= 3
                        else ("opening_candidate" if sealed else "open_passage")
                    ),
                    "rooms": [None, None],
                    "kind": None,
                    "arc_support": 0.0,
                }
                gaps.append(gap)
                if sealed:
                    if orientation == "horizontal":
                        cv2.rectangle(
                            barrier,
                            (end_a, position - half - 1),
                            (start_b, position + half + 1),
                            255,
                            -1,
                        )
                    else:
                        cv2.rectangle(
                            barrier,
                            (position - half - 1, end_a),
                            (position + half + 1, start_b),
                            255,
                            -1,
                        )

        if np.count_nonzero(barrier):
            barrier = cv2.morphologyEx(
                barrier, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)
            )
        return {"barrier": barrier, "gaps": gaps, "wall_segments": wall_segments}

    def _segment_rooms(
        self, barrier: np.ndarray, image_shape: Tuple[int, int], warnings: List[str]
    ) -> Tuple[List[Dict], np.ndarray, Dict]:
        height, width = image_shape
        free = cv2.bitwise_not(barrier)
        count, labels, stats, centroids = cv2.connectedComponentsWithStats(free, 4)
        border_labels = set(labels[0, :].tolist()) | set(labels[-1, :].tolist())
        border_labels |= set(labels[:, 0].tolist()) | set(labels[:, -1].tolist())
        border_labels.discard(0)

        image_area = height * width
        min_area = max(500, int(0.0015 * image_area))
        border_area = sum(
            int(stats[label, cv2.CC_STAT_AREA])
            for label in border_labels
            if 0 <= label < count
        )
        rooms: List[Dict] = []
        for label in range(1, count):
            if label in border_labels:
                continue
            area = int(stats[label, cv2.CC_STAT_AREA])
            if area < min_area or area > 0.55 * image_area:
                continue
            x = int(stats[label, cv2.CC_STAT_LEFT])
            y = int(stats[label, cv2.CC_STAT_TOP])
            w = int(stats[label, cv2.CC_STAT_WIDTH])
            h = int(stats[label, cv2.CC_STAT_HEIGHT])
            component = (labels == label).astype(np.uint8) * 255
            distance = cv2.distanceTransform(component, cv2.DIST_L2, 5)
            _, max_val, _, max_loc = cv2.minMaxLoc(distance)
            if max_val < 3:
                continue
            contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                continue
            contour = max(contours, key=cv2.contourArea)
            epsilon = max(1.5, 0.002 * cv2.arcLength(contour, True))
            approx = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2)
            if len(approx) < 4:
                approx = np.array(
                    [[x, y], [x + w, y], [x + w, y + h], [x, y + h]], dtype=np.int32
                )
            fill_ratio = area / float(max(1, w * h))
            rooms.append(
                {
                    "id": len(rooms),
                    "component_label": int(label),
                    "bounds_px": [x, y, w, h],
                    "center_px": [float(centroids[label][0]), float(centroids[label][1])],
                    "spawn_px": [float(max_loc[0]), float(max_loc[1])],
                    "area_px": area,
                    "fill_ratio": round(fill_ratio, 3),
                    "polygon_px": [[int(px), int(py)] for px, py in approx],
                }
            )
        largest_area = max(
            (int(stats[label, cv2.CC_STAT_AREA]) for label in range(1, count)), default=0
        )
        return rooms, labels, {"border_area": int(border_area), "largest_area": int(largest_area)}

    @staticmethod
    def _drop_unusable_rooms(
        rooms: List[Dict], scale: float, warnings: List[str]
    ) -> List[Dict]:
        """Remove wall cavities that are too narrow to be a real room.

        Raster sealing sometimes leaves a thin strip between two wall runs.
        Keeping it creates a phantom room that no player can enter.
        """
        if scale <= 0 or not rooms:
            return rooms
        kept: List[Dict] = []
        dropped: List[Dict] = []
        for room in rooms:
            _, _, width_px, height_px = room["bounds_px"]
            area_m2 = float(room["area_px"]) / (scale * scale)
            min_side_m = min(width_px, height_px) / scale
            if area_m2 < MIN_USABLE_ROOM_AREA_M2 or min_side_m < MIN_USABLE_ROOM_SIDE_M:
                dropped.append(
                    {
                        "bounds_px": room["bounds_px"],
                        "area_m2": round(area_m2, 2),
                        "min_side_m": round(min_side_m, 2),
                    }
                )
                continue
            kept.append(room)
        if dropped:
            smallest = min(dropped, key=lambda entry: entry["area_m2"])
            warnings.append(
                f"Ignored {len(dropped)} wall cavity/sliver region(s) that are too small to "
                f"be rooms (smallest {smallest['area_m2']} m², "
                f"{smallest['min_side_m']} m wide)."
            )
        for index, room in enumerate(kept):
            room["id"] = index
        return kept

    # ------------------------------------------------------------------
    # Opening classification
    # ------------------------------------------------------------------
    def _build_arc_edge_mask(self, gray: np.ndarray, barrier: np.ndarray) -> np.ndarray:
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 35, 95)
        walls = cv2.dilate(barrier, np.ones((5, 5), np.uint8), iterations=1)
        edges = cv2.bitwise_and(edges, cv2.bitwise_not(walls))
        return cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)

    def _arc_support(
        self,
        edge_mask: np.ndarray,
        center: Tuple[float, float],
        radius: float,
        direction: int,
        side: int,
        orientation: str,
    ) -> float:
        hits = 0
        total = 0
        cx, cy = center
        height, width = edge_mask.shape
        for degrees in np.linspace(3, 87, 36):
            angle = math.radians(float(degrees))
            along = direction * radius * math.cos(angle)
            perpendicular = side * radius * math.sin(angle)
            if orientation == "horizontal":
                px, py = cx + along, cy + perpendicular
            else:
                px, py = cx + perpendicular, cy + along
            ix, iy = int(round(px)), int(round(py))
            if 0 <= ix < width and 0 <= iy < height:
                total += 1
                if edge_mask[iy, ix] > 0:
                    hits += 1
        return hits / total if total else 0.0

    def _best_arc_support(self, edge_mask: np.ndarray, gap: Dict) -> float:
        width = gap["width_px"]
        if width < 8:
            return 0.0
        start, end = gap["span_px"]
        position = gap["line_px"]
        orientation = gap["orientation"]
        best = 0.0
        for scale in (1.0, 1.12, 0.88):
            radius = width * scale
            if orientation == "horizontal":
                hinges = [((start, position), 1), ((end, position), -1)]
            else:
                hinges = [((position, start), 1), ((position, end), -1)]
            for center, direction in hinges:
                for side in (1, -1):
                    best = max(
                        best,
                        self._arc_support(
                            edge_mask, center, radius, direction, side, orientation
                        ),
                    )
        return round(best, 3)

    def _leaf_support(
        self,
        edge_mask: np.ndarray,
        center: Tuple[float, float],
        width: float,
        direction: int,
        side: int,
        orientation: str,
        wall_half: float,
        along_offset: float,
    ) -> float:
        hits = 0
        total = 0
        height, width_px = edge_mask.shape
        cx, cy = center
        for fraction in np.linspace(0.15, 1.0, 26):
            distance = wall_half + width * fraction
            along = direction * along_offset
            if orientation == "horizontal":
                px, py = cx + along, cy + side * distance
            else:
                px, py = cx + side * distance, cy + along
            ix, iy = int(round(px)), int(round(py))
            if 0 <= ix < width_px and 0 <= iy < height:
                total += 1
                if edge_mask[iy, ix] > 0:
                    hits += 1
        return hits / total if total else 0.0

    def _best_leaf_support(self, edge_mask: np.ndarray, gap: Dict) -> float:
        """A door leaf is a straight line from the hinge across the opening.

        Window glazing runs parallel to the wall; a leaf runs perpendicular into
        the room.  Checking this keeps glazed exterior windows from being called
        doors simply because their frame edges resemble a swing arc.
        """
        width = gap["width_px"]
        if width < 8:
            return 0.0
        start, end = gap["span_px"]
        position = gap["line_px"]
        orientation = gap["orientation"]
        wall_half = gap["wall_thickness_px"] / 2.0
        best = 0.0
        if orientation == "horizontal":
            hinges = [((start, position), 1), ((end, position), -1)]
        else:
            hinges = [((position, start), 1), ((position, end), -1)]
        for center, direction in hinges:
            for side in (1, -1):
                for offset_fraction in (0.0, 0.03, 0.08, -0.03, -0.08):
                    best = max(
                        best,
                        self._leaf_support(
                            edge_mask,
                            center,
                            float(width),
                            direction,
                            side,
                            orientation,
                            wall_half,
                            offset_fraction * width,
                        ),
                    )
        return round(best, 3)

    def _probe_room(
        self, room_labels: np.ndarray, rooms: List[Dict], x: float, y: float
    ) -> Optional[int]:
        height, width = room_labels.shape
        ix, iy = int(round(x)), int(round(y))
        if not (0 <= ix < width and 0 <= iy < height):
            return None
        label = int(room_labels[iy, ix])
        if label == 0:
            return None
        for room in rooms:
            if room["component_label"] == label:
                return room["id"]
        return None

    def _classify_openings(
        self,
        gaps: List[Dict],
        room_labels: np.ndarray,
        rooms: List[Dict],
        barrier: np.ndarray,
        edge_mask: np.ndarray,
        scale: float,
        warnings: List[str],
    ) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        openings: List[Dict] = []
        doorways: List[Dict] = []
        windows: List[Dict] = []
        connected_rooms = {room["id"]: False for room in rooms}

        for gap in gaps:
            if not gap["sealed"]:
                continue
            orientation = gap["orientation"]
            start, end = gap["span_px"]
            center_run = (start + end) / 2.0
            position = gap["line_px"]
            thickness = gap["wall_thickness_px"]
            offset = max(8, int(thickness) + 6)
            if orientation == "horizontal":
                probes = [
                    (center_run, position - offset),
                    (center_run, position + offset),
                ]
                center = [center_run, float(position)]
            else:
                probes = [
                    (position - offset, center_run),
                    (position + offset, center_run),
                ]
                center = [float(position), center_run]
            room_a = self._probe_room(room_labels, rooms, *probes[0])
            room_b = self._probe_room(room_labels, rooms, *probes[1])
            gap["rooms"] = [room_a, room_b]
            if room_a is None and room_b is None:
                gap["kind"] = "unresolved"
                continue
            width_m = round(gap["width_px"] / scale, 3)
            if room_a is not None and room_a == room_b:
                gap["kind"] = "noise"
                continue

            arc_support = self._best_arc_support(edge_mask, gap)
            leaf_support = self._best_leaf_support(edge_mask, gap)
            gap["arc_support"] = arc_support
            gap["leaf_support"] = leaf_support
            exterior = room_a is None or room_b is None
            gap["exterior"] = bool(exterior)

            if exterior:
                if width_m < MIN_DOOR_M * 0.6:
                    gap["kind"] = "drawing_gap"
                    continue
                door_evidence = arc_support >= 0.5 or (
                    leaf_support >= 0.7 and arc_support >= 0.25
                )
                if door_evidence and width_m <= MAX_DOOR_M * 1.5:
                    kind = "door"
                else:
                    kind = "window"
                opening = {
                    "id": len(openings),
                    "kind": kind,
                    "orientation": orientation,
                    "center_px": center,
                    "span_px": [int(start), int(end)],
                    "width_px": int(gap["width_px"]),
                    "width_m": width_m,
                    "wall_thickness_px": int(thickness),
                    "room": room_a if room_a is not None else room_b,
                    "arc_support": arc_support,
                    "leaf_support": leaf_support,
                    "exterior": True,
                    "inferred": kind == "window",
                }
                openings.append(opening)
                if kind == "door":
                    doorway = {
                        "id": len(doorways),
                        "orientation": orientation,
                        "center_px": center,
                        "width_px": int(gap["width_px"]),
                        "width_m": width_m,
                        "wall_thickness_px": int(thickness),
                        "rooms": [opening["room"], None],
                        "exterior": True,
                        "kind": "door",
                        "arc_support": arc_support,
                        "leaf_support": leaf_support,
                    }
                    doorways.append(doorway)
                    connected_rooms[opening["room"]] = True
                else:
                    windows.append(
                        {
                            "id": len(windows),
                            "orientation": orientation,
                            "center_px": center,
                            "width_px": int(gap["width_px"]),
                            "width_m": width_m,
                            "wall_thickness_px": int(thickness),
                            "room": opening["room"],
                            "sill_m": 0.9,
                            "head_m": 2.1,
                            "arc_support": arc_support,
                            "leaf_support": leaf_support,
                            "inferred_sill_height": True,
                        }
                    )
                gap["kind"] = kind
                continue

            # Interior opening between two rooms.
            if width_m < MIN_DOOR_M:
                gap["kind"] = "drawing_gap"
                continue
            kind = "door" if (arc_support >= DOOR_ARC_THRESHOLD or leaf_support >= 0.7) else "opening"
            if width_m > MAX_DOOR_M * 1.3:
                warnings.append(
                    f"A {width_m:.1f} m opening between rooms was detected; it is treated "
                    "as a large cased opening and clamped for passability."
                )
            opening = {
                "id": len(openings),
                "kind": kind,
                "orientation": orientation,
                "center_px": center,
                "span_px": [int(start), int(end)],
                "width_px": int(gap["width_px"]),
                "width_m": width_m,
                "wall_thickness_px": int(thickness),
                "rooms": [room_a, room_b],
                "arc_support": arc_support,
                "leaf_support": leaf_support,
                "exterior": False,
                "inferred": kind == "opening",
            }
            openings.append(opening)
            doorway = {
                "id": len(doorways),
                "orientation": orientation,
                "center_px": center,
                "width_px": int(gap["width_px"]),
                "width_m": width_m,
                "wall_thickness_px": int(thickness),
                "rooms": [room_a, room_b],
                "exterior": False,
                "kind": kind,
                "arc_support": arc_support,
                "leaf_support": leaf_support,
            }
            doorways.append(doorway)
            connected_rooms[room_a] = True
            connected_rooms[room_b] = True
            gap["kind"] = kind

        for room in rooms:
            room["connected"] = bool(connected_rooms.get(room["id"], False))
        return openings, doorways, windows

    # ------------------------------------------------------------------
    # Scale and footprint
    # ------------------------------------------------------------------
    def _provisional_scale(
        self, walls_bbox: Dict, known_width: Optional[float], known_length: Optional[float]
    ) -> float:
        if known_width and known_width > 0:
            return walls_bbox["width"] / known_width
        if known_length and known_length > 0:
            return walls_bbox["height"] / known_length
        return walls_bbox["width"] / DEFAULT_HOUSE_WIDTH_M

    def _resolve_scale(
        self,
        walls_bbox: Dict,
        rooms: List[Dict],
        known_width: Optional[float],
        known_length: Optional[float],
        warnings: List[str],
    ) -> Tuple[float, str, bool]:
        candidates = []
        if known_width and known_width > 0:
            candidates.append(walls_bbox["width"] / known_width)
        if known_length and known_length > 0:
            candidates.append(walls_bbox["height"] / known_length)
        if candidates:
            scale = float(np.mean(candidates))
            verified = bool(known_width and known_length)
            if not verified:
                warnings.append(
                    "Only one dimension was provided; the other axis inherits the same scale."
                )
            footprint_width_m = walls_bbox["width"] / scale
            if not 2.5 <= footprint_width_m <= 80:
                warnings.append(
                    "The provided dimensions imply an implausible building size; falling back "
                    "to room-based scale estimation."
                )
            else:
                return scale, "provided_dimensions", verified

        room_sides = [
            min(room["bounds_px"][2], room["bounds_px"][3])
            for room in rooms
            if room["area_px"] > 0
        ]
        if room_sides:
            median_side = float(np.median(room_sides))
            scale = median_side / DEFAULT_ROOM_MIN_SIDE_M
            footprint_width_m = walls_bbox["width"] / scale
            if 2.5 <= footprint_width_m <= 80:
                return scale, "estimated_from_rooms", False

        scale = walls_bbox["width"] / DEFAULT_HOUSE_WIDTH_M
        warnings.append(
            "Scale could not be estimated from the plan; assuming a "
            f"{DEFAULT_HOUSE_WIDTH_M:.0f} m wide building. Measurements are approximate."
        )
        return scale, "estimated_from_footprint", False

    # ------------------------------------------------------------------
    # Diagnostics and visualisation
    # ------------------------------------------------------------------
    def _write_normalized(
        self, diagnostics_dir: Path, image: np.ndarray, width: int, height: int
    ):
        cv2.imwrite(str(diagnostics_dir / "floor_plan_normalized.png"), image)

    async def parse_manual_outline(
        self,
        job_id: str,
        floor_plan_path: str,
        known_width: Optional[float],
        known_length: Optional[float],
        rooms: List,
        doorways: Optional[List] = None,
        wall_thickness_px: Optional[float] = None,
        progress_callback=None,
    ) -> Dict:
        return await asyncio.to_thread(
            self._parse_manual_outline_sync,
            job_id,
            floor_plan_path,
            known_width,
            known_length,
            rooms,
            doorways,
            wall_thickness_px,
            progress_callback,
        )

    def _parse_manual_outline_sync(
        self,
        job_id: str,
        floor_plan_path: str,
        known_width: Optional[float],
        known_length: Optional[float],
        rooms: List,
        doorways: Optional[List] = None,
        wall_thickness_px: Optional[float] = None,
        progress_callback=None,
    ) -> Dict:
        def emit(stage: str):
            if progress_callback is None:
                return
            try:
                progress_callback(stage)
            except Exception:
                pass

        warnings: List[str] = []
        try:
            image = self._read_plan_image(Path(floor_plan_path), job_id)
        except Exception as exc:
            return {
                "status": "failed",
                "error": f"Could not read floor plan: {exc}",
                "error_code": "unreadable_plan",
            }
        height, width = image.shape[:2]
        diagnostics_dir = Path(__file__).parent / "geometry" / job_id
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        self._write_normalized(diagnostics_dir, image, width, height)
        emit("preprocessing")

        def fail(error: str, code: str) -> Dict:
            (diagnostics_dir / "parse_failure.json").write_text(
                json.dumps(
                    {
                        "job_id": job_id,
                        "error": error,
                        "error_code": code,
                        "warnings": warnings,
                        "source": "manual_outline",
                    },
                    indent=2,
                )
            )
            return {
                "status": "failed",
                "error": error,
                "error_code": code,
                "warnings": list(dict.fromkeys(warnings)),
                "diagnostics_dir": f"geometry/{job_id}",
                "correction_supported": True,
            }

        if not rooms:
            return fail("At least one manual room outline is required.", "manual_no_rooms")
        if len(rooms) > 40:
            return fail("At most 40 manual room outlines are allowed.", "manual_too_many_rooms")

        room_defs: List[Dict] = []
        for index, raw in enumerate(rooms):
            points = raw.get("polygon_px") if isinstance(raw, dict) else raw
            if not points or len(points) < 4:
                return fail(
                    f"Room {index + 1} needs at least four corner points.",
                    "manual_invalid_room",
                )
            try:
                xs = [float(point[0]) for point in points]
                ys = [float(point[1]) for point in points]
            except (TypeError, ValueError, IndexError):
                return fail(
                    f"Room {index + 1} contains invalid coordinates.",
                    "manual_invalid_room",
                )
            x0, x1 = min(xs), max(xs)
            y0, y1 = min(ys), max(ys)
            if x0 < 0 or y0 < 0 or x1 > width or y1 > height:
                return fail(
                    f"Room {index + 1} extends outside the floor plan image.",
                    "manual_room_outside_image",
                )
            if x1 - x0 < 20 or y1 - y0 < 20:
                return fail(
                    f"Room {index + 1} is too small to be a room.",
                    "manual_room_too_small",
                )
            if not all(
                abs(x - x0) <= 2 or abs(x - x1) <= 2 or abs(y - y0) <= 2 or abs(y - y1) <= 2
                for x, y in zip(xs, ys)
            ):
                return fail(
                    f"Room {index + 1} is not an axis-aligned rectangle. Manual "
                    "correction currently supports rectangular room outlines only.",
                    "manual_room_not_axis_aligned",
                )
            room_defs.append(
                {
                    "id": index,
                    "x0": int(round(x0)),
                    "y0": int(round(y0)),
                    "x1": int(round(x1)),
                    "y1": int(round(y1)),
                }
            )

        for first in range(len(room_defs)):
            for second in range(first + 1, len(room_defs)):
                a, b = room_defs[first], room_defs[second]
                overlap_w = max(0, min(a["x1"], b["x1"]) - max(a["x0"], b["x0"]))
                overlap_h = max(0, min(a["y1"], b["y1"]) - max(a["y0"], b["y0"]))
                overlap = overlap_w * overlap_h
                smaller = min(
                    (a["x1"] - a["x0"]) * (a["y1"] - a["y0"]),
                    (b["x1"] - b["x0"]) * (b["y1"] - b["y0"]),
                )
                if smaller > 0 and overlap / smaller > 0.1:
                    return fail(
                        f"Room {first + 1} and room {second + 1} overlap. Move the "
                        "outlines so rooms share edges instead of covering each other.",
                        "manual_rooms_overlap",
                    )

        thickness = int(
            round(
                min(
                    60,
                    max(4, float(wall_thickness_px or max(4, min(width, height) * 0.015))),
                )
            )
        )
        labels = np.zeros((height, width), dtype=np.int32)
        for room_def in room_defs:
            labels[
                room_def["y0"] : room_def["y1"] + 1,
                room_def["x0"] : room_def["x1"] + 1,
            ] = room_def["id"] + 1

        rooms_data: List[Dict] = []
        for room_def in room_defs:
            x0, y0, x1, y1 = (
                room_def["x0"],
                room_def["y0"],
                room_def["x1"],
                room_def["y1"],
            )
            mask = np.zeros((height, width), dtype=np.uint8)
            mask[y0 : y1 + 1, x0 : x1 + 1] = 255
            distance = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
            spawn_y, spawn_x = np.unravel_index(int(np.argmax(distance)), distance.shape)
            polygon_px = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
            rooms_data.append(
                {
                    "id": room_def["id"],
                    "bounds_px": [x0, y0, x1 - x0, y1 - y0],
                    "area_px": int((x1 - x0) * (y1 - y0)),
                    "center_px": [(x0 + x1) / 2.0, (y0 + y1) / 2.0],
                    "spawn_px": [float(spawn_x), float(spawn_y)],
                    "polygon_px": polygon_px,
                    "fill_ratio": 1.0,
                    "component_label": room_def["id"] + 1,
                    "manual": True,
                }
            )

        walls_bbox = {
            "x": min(room["x0"] for room in room_defs),
            "y": min(room["y0"] for room in room_defs),
            "width": max(room["x1"] for room in room_defs)
            - min(room["x0"] for room in room_defs),
            "height": max(room["y1"] for room in room_defs)
            - min(room["y0"] for room in room_defs),
        }

        candidates: List[Dict] = []
        for room_def in room_defs:
            x0, y0, x1, y1 = (
                room_def["x0"],
                room_def["y0"],
                room_def["x1"],
                room_def["y1"],
            )
            candidates.extend(
                [
                    {"orientation": "horizontal", "line": y0, "start": x0, "end": x1},
                    {"orientation": "horizontal", "line": y1, "start": x0, "end": x1},
                    {"orientation": "vertical", "line": x0, "start": y0, "end": y1},
                    {"orientation": "vertical", "line": x1, "start": y0, "end": y1},
                ]
            )

        tolerance = max(3, thickness * 0.75)
        groups: List[Dict] = []
        for candidate in sorted(candidates, key=lambda item: item["line"]):
            for group in groups:
                if (
                    group["orientation"] == candidate["orientation"]
                    and abs(group["line"] - candidate["line"]) <= tolerance
                ):
                    group["lines"].append(candidate["line"])
                    group["intervals"].append((candidate["start"], candidate["end"]))
                    break
            else:
                groups.append(
                    {
                        "orientation": candidate["orientation"],
                        "line": candidate["line"],
                        "lines": [candidate["line"]],
                        "intervals": [(candidate["start"], candidate["end"])],
                    }
                )

        def merge_intervals(intervals: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
            merged: List[List[float]] = []
            for start, end in sorted(intervals):
                if merged and start <= merged[-1][1] + 0.5:
                    merged[-1][1] = max(merged[-1][1], end)
                else:
                    merged.append([start, end])
            return [(start, end) for start, end in merged]

        def room_at(px: float, py: float) -> int:
            ix, iy = int(round(px)), int(round(py))
            if 0 <= iy < height and 0 <= ix < width:
                return int(labels[iy, ix])
            return 0

        offset = thickness / 2.0 + max(5, thickness * 0.6)
        wall_segments: List[Dict] = []
        for group in groups:
            line = float(np.mean(group["lines"]))
            for start, end in merge_intervals(group["intervals"]):
                if end - start < max(12, thickness * 2):
                    continue
                midpoint = (start + end) / 2.0
                if group["orientation"] == "horizontal":
                    side_a = room_at(midpoint, line - offset)
                    side_b = room_at(midpoint, line + offset)
                    start_px = [float(start), line]
                    end_px = [float(end), line]
                else:
                    side_a = room_at(line - offset, midpoint)
                    side_b = room_at(line + offset, midpoint)
                    start_px = [line, float(start)]
                    end_px = [line, float(end)]
                exterior = not (side_a and side_b and side_a != side_b)
                wall_segments.append(
                    {
                        "orientation": group["orientation"],
                        "start_px": start_px,
                        "end_px": end_px,
                        "thickness_px": thickness,
                        "exterior": bool(exterior),
                        "manual": True,
                    }
                )

        if not wall_segments:
            return fail("No walls could be derived from the manual rooms.", "manual_no_walls")

        scale, scale_source, scale_verified = self._resolve_scale(
            walls_bbox, rooms_data, known_width, known_length, warnings
        )
        if not scale_verified:
            warnings.append(
                "Scale was estimated from the manual room sizes; measurements are approximate."
            )

        origin_x, origin_y = walls_bbox["x"], walls_bbox["y"]
        for room in rooms_data:
            x, y, w, h = room["bounds_px"]
            room["bounds_m"] = [
                round((x - origin_x) / scale, 3),
                round((y - origin_y) / scale, 3),
                round(w / scale, 3),
                round(h / scale, 3),
            ]
            room["center_m"] = [
                round((room["center_px"][0] - origin_x) / scale, 3),
                round((room["center_px"][1] - origin_y) / scale, 3),
            ]
            room["spawn_m"] = [
                round((room["spawn_px"][0] - origin_x) / scale, 3),
                round((room["spawn_px"][1] - origin_y) / scale, 3),
            ]
            room["area_m2"] = round(room["area_px"] / (scale * scale), 2)
            room["polygon_m"] = [
                [round((px - origin_x) / scale, 3), round((py - origin_y) / scale, 3)]
                for px, py in room["polygon_px"]
            ]

        for wall in wall_segments:
            wall["start_m"] = [
                round((wall["start_px"][0] - origin_x) / scale, 3),
                round((wall["start_px"][1] - origin_y) / scale, 3),
            ]
            wall["end_m"] = [
                round((wall["end_px"][0] - origin_x) / scale, 3),
                round((wall["end_px"][1] - origin_y) / scale, 3),
            ]
            wall["thickness_m"] = round(min(0.45, max(0.08, thickness / scale)), 3)

        doorway_records: List[Dict] = []
        for index, raw in enumerate(doorways or []):
            if not isinstance(raw, dict):
                return fail(
                    f"Doorway {index + 1} must be an object with orientation, center_px "
                    "and width_px.",
                    "manual_invalid_doorway",
                )
            orientation = str(raw.get("orientation") or "").lower()
            if orientation not in {"horizontal", "vertical"}:
                return fail(
                    f"Doorway {index + 1} has invalid orientation '{orientation}'.",
                    "manual_invalid_doorway",
                )
            center = raw.get("center_px") or []
            if len(center) != 2:
                return fail(
                    f"Doorway {index + 1} center_px must contain two coordinates.",
                    "manual_invalid_doorway",
                )
            try:
                center_px = [float(center[0]), float(center[1])]
                width_px = float(raw.get("width_px") or 0.9 * scale)
            except (TypeError, ValueError):
                return fail(
                    f"Doorway {index + 1} contains invalid numbers.",
                    "manual_invalid_doorway",
                )
            width_m_value = width_px / scale
            if not (MIN_DOOR_M * 0.8 <= width_m_value <= MAX_DOOR_M * 1.6):
                return fail(
                    f"Doorway {index + 1} is {width_m_value:.2f} m wide, outside the "
                    "supported doorway range.",
                    "manual_doorway_width",
                )
            line_axis = 1 if orientation == "horizontal" else 0
            run_axis = 1 - line_axis
            matching = [
                wall
                for wall in wall_segments
                if wall["orientation"] == orientation
                and abs(wall["start_px"][line_axis] - center_px[line_axis])
                <= thickness + 12
            ]
            if not matching:
                return fail(
                    f"Doorway {index + 1} is not on a wall. Place it on a shared or "
                    "exterior wall line.",
                    "manual_doorway_not_on_wall",
                )
            run_matching = [
                wall
                for wall in matching
                if min(wall["start_px"][run_axis], wall["end_px"][run_axis]) - 8
                <= center_px[run_axis]
                <= max(wall["start_px"][run_axis], wall["end_px"][run_axis]) + 8
            ]
            if not run_matching:
                return fail(
                    f"Doorway {index + 1} lies beyond the end of the wall. Move it "
                    "inside the wall span.",
                    "manual_doorway_not_on_wall",
                )
            room_side_a = room_at(
                center_px[0] - (offset if orientation == "vertical" else 0),
                center_px[1] - (offset if orientation == "horizontal" else 0),
            )
            room_side_b = room_at(
                center_px[0] + (offset if orientation == "vertical" else 0),
                center_px[1] + (offset if orientation == "horizontal" else 0),
            )
            if room_side_a == room_side_b or (room_side_a == 0 and room_side_b == 0):
                return fail(
                    f"Doorway {index + 1} does not connect two rooms or an exterior "
                    "opening. Move it onto a wall between a room and another room or "
                    "the outside.",
                    "manual_doorway_not_connecting",
                )
            side_a = room_side_a - 1 if room_side_a else None
            side_b = room_side_b - 1 if room_side_b else None
            exterior = side_a is None or side_b is None
            doorway_records.append(
                {
                    "id": index,
                    "orientation": orientation,
                    "center_px": center_px,
                    "width_px": width_px,
                    "width_m": round(width_m_value, 3),
                    "wall_thickness_px": thickness,
                    "rooms": [side_a, side_b],
                    "exterior": exterior,
                    "kind": "manual_door",
                    "manual": True,
                    "arc_support": None,
                    "leaf_support": None,
                }
            )

        adjacency = {room["id"]: set() for room in rooms_data}
        for doorway in doorway_records:
            first, second = doorway["rooms"]
            if first is not None and second is not None:
                adjacency[first].add(second)
                adjacency[second].add(first)
        visited = {rooms_data[0]["id"]}
        frontier = [rooms_data[0]["id"]]
        while frontier:
            current = frontier.pop()
            for neighbour in adjacency[current]:
                if neighbour not in visited:
                    visited.add(neighbour)
                    frontier.append(neighbour)
        if len(visited) != len(rooms_data):
            missing = sorted(set(adjacency) - visited)
            return fail(
                "Manual room outlines are disconnected. Add doorways so that room(s) "
                + ", ".join(str(room_id + 1) for room_id in missing)
                + " can be reached.",
                "manual_outline_disconnected",
            )
        for room in rooms_data:
            room["connected"] = bool(adjacency[room["id"]]) or len(rooms_data) == 1

        for doorway in doorway_records:
            doorway["center_m"] = [
                round((doorway["center_px"][0] - origin_x) / scale, 3),
                round((doorway["center_px"][1] - origin_y) / scale, 3),
            ]

        emit("detecting_rooms")
        wall_mask = np.zeros((height, width), dtype=np.uint8)
        for wall in wall_segments:
            half = thickness // 2
            if wall["orientation"] == "horizontal":
                y = int(round(wall["start_px"][1]))
                cv2.rectangle(
                    wall_mask,
                    (int(round(wall["start_px"][0])), y - half),
                    (int(round(wall["end_px"][0])), y + half),
                    255,
                    -1,
                )
            else:
                x = int(round(wall["start_px"][0]))
                cv2.rectangle(
                    wall_mask,
                    (x - half, int(round(wall["start_px"][1]))),
                    (x + half, int(round(wall["end_px"][1]))),
                    255,
                    -1,
                )
        cv2.imwrite(str(diagnostics_dir / "walls_mask.png"), wall_mask)
        cv2.imwrite(str(diagnostics_dir / "barrier.png"), (labels > 0).astype(np.uint8) * 255)
        cv2.imwrite(
            str(diagnostics_dir / "rooms_detected.png"),
            self._visualize_rooms(image, rooms_data),
        )
        cv2.imwrite(
            str(diagnostics_dir / "openings_detected.png"),
            self._visualize_openings(image, doorway_records, []),
        )
        (diagnostics_dir / "manual_outline.json").write_text(
            json.dumps(
                {
                    "rooms": room_defs,
                    "doorways": doorway_records,
                    "wall_thickness_px": thickness,
                },
                indent=2,
            )
        )
        (diagnostics_dir / "segmentation.json").write_text(
            json.dumps(
                {
                    "source": "manual_outline",
                    "rooms": [
                        {
                            "id": room["id"],
                            "bounds_px": room["bounds_px"],
                            "area_px": room["area_px"],
                        }
                        for room in rooms_data
                    ],
                    "doorways": len(doorway_records),
                    "windows": 0,
                },
                indent=2,
            )
        )
        emit("validating_layout")

        warnings = list(dict.fromkeys(warnings))
        return {
            "status": "completed",
            "image_dimensions": [int(width), int(height)],
            "preprocessing": {"source": "manual_outline"},
            "inverted_drawing": False,
            "wall_pixel_ratio": round(
                float(np.count_nonzero(wall_mask)) / float(width * height), 4
            ),
            "scale_pixels_per_meter": round(scale, 3),
            "scale_source": scale_source,
            "scale_verified": bool(scale_verified),
            "footprint_px": walls_bbox,
            "footprint_m": {
                "width": round(walls_bbox["width"] / scale, 3),
                "height": round(walls_bbox["height"] / scale, 3),
            },
            "rooms": rooms_data,
            "wall_segments": wall_segments,
            "doorways": doorway_records,
            "windows": [],
            "openings": [],
            "segmentation": {
                "source": "manual_outline",
                "selected_attempt": "manual",
                "attempts": [],
                "doorways": len(doorway_records),
                "windows": 0,
                "diagnostics_dir": f"geometry/{job_id}",
            },
            "warnings": warnings,
        }

    def _save_success_diagnostics(
        self,
        diagnostics_dir: Path,
        image: np.ndarray,
        wall_mask: np.ndarray,
        barrier: np.ndarray,
        rooms: List[Dict],
        doorways: List[Dict],
        windows: List[Dict],
        attempts: List[Dict],
    ):
        cv2.imwrite(str(diagnostics_dir / "walls_mask.png"), wall_mask)
        cv2.imwrite(str(diagnostics_dir / "barrier.png"), barrier)
        cv2.imwrite(
            str(diagnostics_dir / "rooms_detected.png"),
            self._visualize_rooms(image, rooms),
        )
        cv2.imwrite(
            str(diagnostics_dir / "openings_detected.png"),
            self._visualize_openings(image, doorways, windows),
        )
        (diagnostics_dir / "segmentation.json").write_text(
            json.dumps(
                {
                    "attempts": attempts,
                    "rooms": [
                        {
                            "id": room["id"],
                            "bounds_px": room["bounds_px"],
                            "area_px": room["area_px"],
                            "component_label": room["component_label"],
                        }
                        for room in rooms
                    ],
                    "doorways": len(doorways),
                    "windows": len(windows),
                },
                indent=2,
            )
        )

    def _fail_with_diagnostics(
        self,
        job_id: str,
        diagnostics_dir: Path,
        image: np.ndarray,
        wall_mask: Optional[np.ndarray],
        barrier: Optional[np.ndarray],
        error: str,
        error_code: str,
        warnings: List[str],
        diagnostics: Dict,
        lines: Optional[List[Dict]] = None,
        gaps: Optional[List[Dict]] = None,
    ) -> Dict:
        if wall_mask is not None:
            cv2.imwrite(str(diagnostics_dir / "walls_mask.png"), wall_mask)
        if barrier is not None:
            cv2.imwrite(str(diagnostics_dir / "barrier.png"), barrier)
            cv2.imwrite(
                str(diagnostics_dir / "barrier_overlay.png"),
                self._visualize_barrier(image, barrier),
            )
        if lines:
            cv2.imwrite(
                str(diagnostics_dir / "walls_detected.png"),
                self._visualize_lines(image, lines, gaps or []),
            )
        (diagnostics_dir / "parse_failure.json").write_text(
            json.dumps(
                {
                    "job_id": job_id,
                    "error": error,
                    "error_code": error_code,
                    "warnings": warnings,
                    "diagnostics": diagnostics,
                    "lines": [
                        {
                            "orientation": line["orientation"],
                            "position_px": line["pos"],
                            "thickness_px": line["thickness_px"],
                            "intervals": line["intervals"],
                            "exterior": line.get("exterior"),
                        }
                        for line in (lines or [])
                    ],
                    "gaps": gaps or [],
                },
                indent=2,
                default=str,
            )
        )
        return {
            "status": "failed",
            "error": error,
            "error_code": error_code,
            "warnings": list(dict.fromkeys(warnings)),
            "attempts": diagnostics.get("attempts", []),
            "wall_pixel_ratio": diagnostics.get("wall_pixel_ratio"),
            "diagnostics_dir": f"geometry/{job_id}",
        }

    def _visualize_rooms(self, image: np.ndarray, rooms: List[Dict]) -> np.ndarray:
        canvas = image.copy()
        palette = [
            (66, 135, 245),
            (64, 192, 87),
            (250, 176, 5),
            (220, 78, 78),
            (156, 89, 209),
            (54, 194, 201),
            (233, 120, 60),
            (110, 168, 86),
        ]
        for room in rooms:
            color = palette[room["id"] % len(palette)]
            overlay = image.copy()
            cv2.fillPoly(overlay, [np.array(room["polygon_px"], dtype=np.int32)], color)
            cv2.addWeighted(overlay, 0.35, canvas, 0.65, 0, canvas)
            x, y, w, h = room["bounds_px"]
            cv2.putText(
                canvas,
                f"Room {room['id'] + 1}",
                (x + 6, y + 22),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (20, 20, 20),
                2,
            )
        return canvas

    def _visualize_openings(
        self, image: np.ndarray, doorways: List[Dict], windows: List[Dict]
    ) -> np.ndarray:
        canvas = image.copy()
        for door in doorways:
            cx, cy = map(int, door["center_px"])
            cv2.circle(canvas, (cx, cy), 10, (0, 0, 230), -1)
            cv2.putText(
                canvas,
                f"D {door['width_m']:.1f}m",
                (cx + 12, cy - 6),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (0, 0, 180),
                1,
            )
        for window in windows:
            cx, cy = map(int, window["center_px"])
            cv2.circle(canvas, (cx, cy), 8, (60, 170, 30), -1)
            cv2.putText(
                canvas,
                f"W {window['width_m']:.1f}m",
                (cx + 10, cy + 16),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.42,
                (30, 130, 10),
                1,
            )
        return canvas

    def _visualize_lines(
        self, image: np.ndarray, lines: List[Dict], gaps: List[Dict]
    ) -> np.ndarray:
        canvas = image.copy()
        for line in lines:
            position = int(line["pos"])
            thickness = max(1, int(line["thickness_px"]))
            color = (0, 0, 255) if line.get("exterior") else (255, 0, 0)
            for start, end in line["intervals"]:
                if line["orientation"] == "horizontal":
                    cv2.rectangle(
                        canvas,
                        (start, position - thickness // 2),
                        (end, position + thickness // 2),
                        color,
                        2,
                    )
                else:
                    cv2.rectangle(
                        canvas,
                        (position - thickness // 2, start),
                        (position + thickness // 2, end),
                        color,
                        2,
                    )
        for gap in gaps:
            start, end = gap["span_px"]
            position = gap["line_px"]
            thickness = max(1, int(gap["wall_thickness_px"]))
            color = (0, 180, 255) if gap["sealed"] else (0, 255, 0)
            if gap["orientation"] == "horizontal":
                cv2.rectangle(
                    canvas,
                    (start, position - thickness // 2 - 2),
                    (end, position + thickness // 2 + 2),
                    color,
                    2,
                )
            else:
                cv2.rectangle(
                    canvas,
                    (position - thickness // 2 - 2, start),
                    (position + thickness // 2 + 2, end),
                    color,
                    2,
                )
        return canvas

    def _visualize_barrier(self, image: np.ndarray, barrier: np.ndarray) -> np.ndarray:
        canvas = image.copy()
        overlay = canvas.copy()
        overlay[barrier > 0] = (40, 40, 220)
        cv2.addWeighted(overlay, 0.35, canvas, 0.65, 0, canvas)
        return canvas

    def _flag_diagonals(self, wall_mask: np.ndarray, warnings: List[str]):
        total_wall_pixels = float(np.count_nonzero(wall_mask))
        if total_wall_pixels == 0:
            return
        covered = np.zeros_like(wall_mask)
        height, width = wall_mask.shape
        min_diagonal_length = int(min(width, height) * 0.08)
        diagonal = cv2.HoughLinesP(
            wall_mask, 1, np.pi / 180, threshold=60,
            minLineLength=min_diagonal_length, maxLineGap=4,
        )
        if diagonal is None:
            return
        for x1, y1, x2, y2 in diagonal.reshape(-1, 4):
            x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
            if abs(x2 - x1) <= 6 or abs(y2 - y1) <= 6:
                continue
            cv2.line(covered, (x1, y1), (x2, y2), 255, 3)
        diagonal_fraction = float(np.count_nonzero(covered)) / total_wall_pixels
        if diagonal_fraction > 0.02:
            warnings.append(
                "The plan contains diagonal wall sections. Only axis-aligned walls are "
                "reconstructed; diagonal areas are approximated."
            )
