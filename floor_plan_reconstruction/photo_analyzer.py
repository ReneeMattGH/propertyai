"""
Photo analyser.

Responsibilities that are real and verified here:
  * reject unreadable or degenerate photographs;
  * detect duplicate and low-sharpness photographs;
  * extract a dominant-colour palette per photo and aggregate a material palette;
  * crop real wall and floor texture patches from each photograph and save them
    for the GLB builder, so uploaded images drive the scene appearance;
  * run an ORB keypoint-overlap diagnostic between photo pairs.

Camera pose estimation and sparse geometry live in photogrammetry.py and run as
a separate stage. This module never claims photogrammetry when it did not run.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

DEFAULT_WALL_RGB = [228, 224, 216]
DEFAULT_FLOOR_RGB = [188, 169, 145]
DEFAULT_ACCENT_RGB = [140, 128, 112]
SAMPLE_SIZE = 240
MAX_OVERLAP_PAIRS = 12

ROOM_HINTS = {
    "living": "living_room",
    "lounge": "living_room",
    "kitchen": "kitchen",
    "bed": "bedroom",
    "bath": "bathroom",
    "toilet": "bathroom",
    "dining": "dining_room",
    "office": "office",
    "hall": "hallway",
    "corridor": "hallway",
    "stair": "stairs",
    "garage": "garage",
    "garden": "exterior",
    "exterior": "exterior",
}


def _luminance(rgb: Tuple[int, int, int]) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def _saturation(rgb: Tuple[int, int, int]) -> float:
    high = max(rgb)
    low = min(rgb)
    return 0.0 if high == 0 else (high - low) / high


def _median_rgb(values: List[Tuple[int, int, int]], fallback: List[int]) -> List[int]:
    if not values:
        return list(fallback)
    array = np.array(values, dtype=np.float64)
    median = np.median(array, axis=0)
    return [int(max(0, min(255, round(channel)))) for channel in median]


class PhotoAnalyzer:
    """Analyse photographs for material colours and overlap diagnostics."""

    async def analyze_photos(
        self, job_id: str, photo_paths: List[str], floor_plan_result: Dict
    ) -> Dict:
        return await asyncio.to_thread(
            self._analyze_photos_sync, job_id, photo_paths, floor_plan_result
        )

    def _analyze_photos_sync(
        self, job_id: str, photo_paths: List[str], floor_plan_result: Dict
    ) -> Dict:
        results: Dict = {
            "total_photos": len(photo_paths),
            "analyzed_photos": 0,
            "photos": [],
            "textures": [],
            "errors": [],
            "warnings": [],
            "palette": {},
            "filename_room_hints": {},
            "photogrammetry": {
                "used_for_geometry": False,
                "evaluated": False,
                "assessment": "not_evaluated",
                "pairs": [],
                "note": (
                    "Camera estimation is performed by the separate photogrammetry "
                    "stage. This diagnostic only measures ORB keypoint overlap."
                ),
            },
            "notes": [
                "Wall and floor texture patches are cropped from the actual uploaded "
                "photographs. Room assignment is decided by the classifier plus the "
                "user's explicit confirmation, never by upload order."
            ],
        }
        wall_candidates: List[Tuple[int, int, int]] = []
        floor_candidates: List[Tuple[int, int, int]] = []
        accent_candidates: List[Tuple[int, int, int]] = []
        gray_images: List[Tuple[str, np.ndarray]] = []
        texture_dir = Path(__file__).parent / "geometry" / job_id / "textures"
        texture_dir.mkdir(parents=True, exist_ok=True)
        seen_hashes: List[Tuple[int, str]] = []

        for index, photo_path in enumerate(photo_paths):
            path = Path(photo_path)
            try:
                with Image.open(path) as source:
                    source = ImageOps.exif_transpose(source).convert("RGB")
                    width, height = source.size
                    image = cv2.cvtColor(
                        np.asarray(source), cv2.COLOR_RGB2BGR
                    )
            except (UnidentifiedImageError, OSError, ValueError) as exc:
                results["errors"].append({"file": path.name, "error": str(exc)})
                continue
            if min(width, height) < 160:
                results["errors"].append(
                    {"file": path.name, "error": f"image too small ({width}x{height}px)"}
                )
                continue

            palette = self._dominant_colors(image, k=4)
            wall_rgb = self._estimate_wall_color(palette)
            floor_rgb = self._estimate_floor_color(image)
            accent_rgb = self._estimate_accent_color(palette)
            wall_candidates.append(tuple(wall_rgb))
            floor_candidates.append(tuple(floor_rgb))
            accent_candidates.append(tuple(accent_rgb))

            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            image_hash = self._average_hash(gray)
            duplicate_of = next(
                (
                    other_name
                    for other_hash, other_name in seen_hashes
                    if int(image_hash ^ other_hash).bit_count() <= 2
                ),
                None,
            )
            if duplicate_of is None:
                seen_hashes.append((image_hash, path.name))
            else:
                warning = (
                    f"{path.name} is a duplicate photograph of {duplicate_of}; both "
                    "remain in the walkthrough but the duplicate may not add camera coverage."
                )
                results["notes"].append(warning)
                results["warnings"].append(warning)

            wall_texture = self._extract_texture(
                image, wall_rgb, texture_dir / f"photo_{index:02d}_wall.jpg", "wall"
            )
            floor_texture = self._extract_texture(
                image, floor_rgb, texture_dir / f"photo_{index:02d}_floor.jpg", "floor"
            )
            hint = self._room_hint_from_filename(path.name)
            if hint:
                results["filename_room_hints"][path.name] = hint

            relative_wall = f"textures/{wall_texture}" if wall_texture else None
            relative_floor = f"textures/{floor_texture}" if floor_texture else None
            photo_entry = {
                "file": path.name,
                "width": int(width),
                "height": int(height),
                "brightness": round(float(np.mean(gray)), 1),
                "contrast": round(float(np.std(gray)), 1),
                "sharpness": round(sharpness, 1),
                "duplicate_of": duplicate_of,
                "room_hint": hint,
                "dominant_rgb": [list(color["rgb"]) for color in palette],
                "estimated_wall_rgb": wall_rgb,
                "estimated_floor_rgb": floor_rgb,
                "wall_texture": relative_wall,
                "floor_texture": relative_floor,
            }
            results["photos"].append(photo_entry)
            if wall_texture or floor_texture:
                results["textures"].append(
                    {
                        "file": path.name,
                        "room_hint": hint,
                        "wall_texture": relative_wall,
                        "floor_texture": relative_floor,
                        "wall_rgb": wall_rgb,
                        "floor_rgb": floor_rgb,
                        "sharpness": round(sharpness, 1),
                        "duplicate_of": duplicate_of,
                    }
                )
            if sharpness < 35.0:
                warning = (
                    f"{path.name} has a low sharpness score ({sharpness:.1f}); "
                    "it is still used but may produce blurred surfaces or unstable "
                    "camera matching."
                )
                results["notes"].append(warning)
                results["warnings"].append(warning)

            preview_width = 320
            scale = preview_width / max(1, width)
            gray_small = cv2.resize(
                gray,
                (preview_width, max(1, int(height * scale))),
                interpolation=cv2.INTER_AREA,
            )
            gray_images.append((path.name, gray_small))
            results["analyzed_photos"] += 1

        results["palette"] = {
            "wall_rgb": _median_rgb(wall_candidates, DEFAULT_WALL_RGB),
            "floor_rgb": _median_rgb(floor_candidates, DEFAULT_FLOOR_RGB),
            "accent_rgb": _median_rgb(accent_candidates, DEFAULT_ACCENT_RGB),
            "sample_count": results["analyzed_photos"],
        }
        if results["filename_room_hints"]:
            results["notes"].append(
                "Room names in photo filenames were recorded for diagnostics only; "
                "they never determine room assignment."
            )
        results["notes"].append(
            f"{len(results['textures'])} photograph(s) produced real wall/floor "
            "texture tiles for the GLB materials."
        )

        results["photogrammetry"] = self._overlap_diagnostic(gray_images, results["photogrammetry"])

        self._save_photo_analysis(job_id, results)
        return results

    # ------------------------------------------------------------------
    # Colour estimation
    # ------------------------------------------------------------------
    def _dominant_colors(self, image: np.ndarray, k: int = 4) -> List[Dict]:
        height, width = image.shape[:2]
        scale = SAMPLE_SIZE / max(1, max(height, width))
        sample = cv2.resize(
            image,
            (max(1, int(width * scale)), max(1, int(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
        pixels = sample.reshape(-1, 3).astype(np.float32)
        if len(pixels) < k:
            return [{"rgb": [128, 128, 128], "weight": 1.0}]
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
        _, labels, centers = cv2.kmeans(
            pixels, k, None, criteria, 3, cv2.KMEANS_PP_CENTERS
        )
        labels = labels.flatten()
        total = len(labels)
        palette = []
        for index, center in enumerate(centers):
            weight = float(np.count_nonzero(labels == index)) / total
            b, g, r = center
            palette.append(
                {"rgb": [int(r), int(g), int(b)], "weight": round(weight, 3)}
            )
        palette.sort(key=lambda item: item["weight"], reverse=True)
        return palette

    def _estimate_wall_color(self, palette: List[Dict]) -> List[int]:
        candidates = [
            item for item in palette if item["weight"] >= 0.08 and _luminance(item["rgb"]) < 246
        ]
        if not candidates:
            candidates = palette
        if not candidates:
            return list(DEFAULT_WALL_RGB)
        brightest = max(candidates, key=lambda item: _luminance(item["rgb"]))
        rgb = brightest["rgb"]
        # Desaturate slightly: photos carry colour casts that should not be painted on walls.
        average = sum(rgb) / 3
        return [int(round(average + (channel - average) * 0.65)) for channel in rgb]

    def _estimate_floor_color(self, image: np.ndarray) -> List[int]:
        height, width = image.shape[:2]
        band = image[int(height * 0.82):height, int(width * 0.15):int(width * 0.85)]
        if band.size == 0:
            return list(DEFAULT_FLOOR_RGB)
        pixels = band.reshape(-1, 3).astype(np.float32)
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 15, 1.0)
        _, labels, centers = cv2.kmeans(
            pixels, 2, None, criteria, 2, cv2.KMEANS_PP_CENTERS
        )
        labels = labels.flatten()
        best = max(range(len(centers)), key=lambda i: np.count_nonzero(labels == i))
        b, g, r = centers[best]
        # Darken and warm the sample slightly so floors read as a floor, not a wall.
        return [
            int(max(0, min(255, r * 0.92))),
            int(max(0, min(255, g * 0.86))),
            int(max(0, min(255, b * 0.78))),
        ]

    def _estimate_accent_color(self, palette: List[Dict]) -> List[int]:
        candidates = [item for item in palette if item["weight"] >= 0.05]
        if not candidates:
            return list(DEFAULT_ACCENT_RGB)
        return max(candidates, key=lambda item: _saturation(item["rgb"]))["rgb"]

    def _room_hint_from_filename(self, name: str) -> Optional[str]:
        lowered = name.lower()
        for token, room_type in ROOM_HINTS.items():
            if token in lowered:
                return room_type
        return None

    def _average_hash(self, gray: np.ndarray) -> int:
        small = cv2.resize(gray, (8, 8), interpolation=cv2.INTER_AREA)
        bits = (small > float(small.mean())).flatten()
        return int.from_bytes(np.packbits(bits).tobytes(), "big")

    def _extract_texture(
        self, image: np.ndarray, rgb: List[int], target: Path, kind: str
    ) -> Optional[str]:
        try:
            height, width = image.shape[:2]
            if kind == "wall":
                search = image[: max(1, int(height * 0.8))]
                crop = self._wall_patch(search, rgb)
            else:
                y0, y1 = int(height * 0.76), max(int(height * 0.76) + 1, int(height * 0.98))
                x0, x1 = int(width * 0.15), max(int(width * 0.15) + 1, int(width * 0.85))
                band = image[y0:y1, x0:x1]
                # Center-crop a square so the photo's aspect ratio is preserved
                # instead of stretching the floor band into a square tile.
                side = min(band.shape[:2]) if band.size else 0
                if side > 0:
                    top = (band.shape[0] - side) // 2
                    left = (band.shape[1] - side) // 2
                    crop = band[top:top + side, left:left + side]
                else:
                    crop = None
            if crop is None or crop.size == 0 or min(crop.shape[:2]) < 24:
                return None
            crop = cv2.resize(crop, (256, 256), interpolation=cv2.INTER_AREA)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(target), crop, [cv2.IMWRITE_JPEG_QUALITY, 90]):
                return None
            return target.name
        except Exception:
            return None

    def _wall_patch(self, region: np.ndarray, rgb: List[int]) -> Optional[np.ndarray]:
        height, width = region.shape[:2]
        lab = cv2.cvtColor(region, cv2.COLOR_BGR2LAB).astype(np.float32)
        target = cv2.cvtColor(
            np.uint8([[[rgb[2], rgb[1], rgb[0]]]]), cv2.COLOR_BGR2LAB
        )[0, 0].astype(np.float32)
        distance = np.linalg.norm(lab - target, axis=2)
        mask = (distance < 30).astype(np.uint8) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
        count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        if count > 1:
            index = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
            x, y, w, h, area = stats[index]
            if area >= 0.02 * width * height and min(w, h) >= 32:
                side = int(min(max(w, h), 360))
                left = int(x + (w - side) / 2)
                top = int(y + (h - side) / 2)
                left = max(0, min(width - side, left))
                top = max(0, min(height - side, top))
                patch = region[top:top + side, left:left + side]
                if patch.size > 0:
                    return patch
        y0, y1 = int(height * 0.15), int(height * 0.7)
        x0, x1 = int(width * 0.25), int(width * 0.75)
        fallback = region[max(0, y0):max(1, y1), max(0, x0):max(1, x1)]
        return fallback if fallback.size else None

    # ------------------------------------------------------------------
    # Overlap diagnostic
    # ------------------------------------------------------------------
    def _overlap_diagnostic(self, gray_images: List[Tuple[str, np.ndarray]], base: Dict) -> Dict:
        if len(gray_images) < 2:
            base["assessment"] = "insufficient_photos"
            base["note"] = (
                "At least two photographs are needed to evaluate overlap. Camera "
                "estimation itself is performed by the separate photogrammetry stage."
            )
            return base
        orb = cv2.ORB_create(nfeatures=600)
        matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        features = []
        for name, gray in gray_images:
            keypoints, descriptors = orb.detectAndCompute(gray, None)
            features.append((name, keypoints, descriptors))
        pairs = []
        for i in range(len(features)):
            for j in range(i + 1, len(features)):
                if len(pairs) >= MAX_OVERLAP_PAIRS:
                    break
                name_a, kp_a, desc_a = features[i]
                name_b, kp_b, desc_b = features[j]
                if desc_a is None or desc_b is None or len(desc_a) == 0 or len(desc_b) == 0:
                    score = 0.0
                else:
                    matches = matcher.match(desc_a, desc_b)
                    good = [m for m in matches if m.distance < 32]
                    score = len(good) / max(1, min(len(kp_a), len(kp_b)))
                pairs.append(
                    {
                        "a": name_a,
                        "b": name_b,
                        "overlap_score": round(float(score), 3),
                    }
                )
        max_score = max((pair["overlap_score"] for pair in pairs), default=0.0)
        if max_score >= 0.25:
            assessment = "overlap_detected"
        elif max_score >= 0.1:
            assessment = "weak_overlap"
        else:
            assessment = "insufficient_overlap"
        base["evaluated"] = True
        base["assessment"] = assessment
        base["pairs"] = pairs
        base["max_overlap_score"] = round(float(max_score), 3)
        return base

    def _save_photo_analysis(self, job_id: str, results: Dict):
        output_dir = Path(__file__).parent / "geometry" / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "photo_analysis.json").write_text(json.dumps(results, indent=2))
