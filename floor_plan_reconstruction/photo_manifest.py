"""Persistent per-job photograph manifest.

Originals stay in ``input/<job>/`` untouched. Display-oriented optimized copies
and thumbnails are generated separately under ``geometry/<job>/photos/`` so the
review UI and reader never depend on EXIF-rotated originals. Every entry is
scoped to one job and property; no lookup can cross job boundaries.
"""
from __future__ import annotations

import json
import math
import mimetypes
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

BASE_DIR = Path(__file__).parent
INPUT_DIR = BASE_DIR / "input"
GEOMETRY_DIR = BASE_DIR / "geometry"
MANIFEST_SCHEMA_VERSION = "1.0"
OPTIMIZED_MAX_SIDE = 1600
THUMBNAIL_MAX_SIDE = 480
MAX_ASPECT_RATIO = 12.0

ROOM_TYPES = [
    "entrance",
    "living_room",
    "kitchen",
    "bedroom",
    "bathroom",
    "dining_room",
    "hallway",
    "balcony",
    "utility",
    "exterior",
    "staircase",
    "unknown",
]

ROOM_TYPE_LABELS = {
    "entrance": "Entrance / foyer",
    "living_room": "Living room",
    "kitchen": "Kitchen",
    "bedroom": "Bedroom",
    "bathroom": "Bathroom",
    "dining_room": "Dining room",
    "hallway": "Hallway / corridor",
    "balcony": "Balcony / terrace",
    "utility": "Utility room",
    "exterior": "Exterior / garden",
    "staircase": "Staircase",
    "unknown": "Unknown / unclassified",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def manifest_path(job_id: str) -> Path:
    return GEOMETRY_DIR / job_id / "photo_manifest.json"


def photos_root(job_id: str) -> Path:
    return GEOMETRY_DIR / job_id / "photos"


def load_photo_manifest(job_id: str) -> Optional[Dict]:
    path = manifest_path(job_id)
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def save_photo_manifest(manifest: Dict) -> Path:
    manifest["updated_at"] = _now()
    path = manifest_path(manifest["job_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2))
    tmp.replace(path)
    return path


def _mime_type(source: Image.Image, path: Path) -> str:
    if source.format and Image.MIME.get(source.format.upper()):
        return Image.MIME[source.format.upper()]
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _save_variant(image: Image.Image, target: Path, max_side: int) -> Dict:
    if image.mode not in ("RGB", "L"):
        background = Image.new("RGB", image.size, (255, 255, 255))
        if image.mode == "RGBA":
            background.paste(image, mask=image.getchannel("A"))
        else:
            background.paste(image.convert("RGB"))
        image = background
    else:
        image = image.convert("RGB")
    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="JPEG", quality=88, optimize=True)
    return {
        "filename": target.name,
        "width": int(image.width),
        "height": int(image.height),
        "size_bytes": target.stat().st_size,
    }


def _empty_photo(
    job_id: str,
    property_id: str,
    photo_id: str,
    *,
    original_filename: str,
    stored_filename: str,
    size_bytes: int,
    sha256: str,
) -> Dict:
    return {
        "photo_id": photo_id,
        "job_id": job_id,
        "property_id": property_id,
        "original": {
            "original_filename": original_filename,
            "stored_filename": stored_filename,
            "size_bytes": int(size_bytes),
            "sha256": sha256,
            "mime_type": None,
            "width": None,
            "height": None,
            "aspect_ratio": None,
            "orientation": None,
        },
        "optimized": None,
        "thumbnail": None,
        "classification": {
            "status": "pending",
            "model": None,
            "predicted_room_type": None,
            "predicted_label": None,
            "confidence": None,
            "ambiguous": None,
            "candidates": [],
            "error": None,
            "classified_at": None,
        },
        "room_assignment": {
            "room_id": None,
            "confirmed": False,
            "excluded": False,
            "confirmed_at": None,
        },
        "match": {
            "status": "unassigned",
            "room_id": None,
            "confidence": None,
            "reason": "Classification has not run yet.",
        },
        "quality": {
            "brightness": None,
            "contrast": None,
            "sharpness": None,
            "duplicate_of": None,
            "warnings": [],
        },
        "camera": {
            "status": "unknown",
            "center": None,
            "viewing_direction": None,
            "registered": False,
        },
        "visible": {
            "status": "not_detected",
            "features": [],
            "walls": [],
            "windows": [],
            "doors": [],
            "fixtures": [],
            "method": None,
        },
        "processing": {
            "status": "ready",
            "errors": [],
        },
        "textures": {
            "wall_texture": None,
            "floor_texture": None,
            "wall_rgb": None,
            "floor_rgb": None,
        },
    }


def photo_by_id(manifest: Dict, photo_id: str) -> Optional[Dict]:
    return next(
        (photo for photo in manifest.get("photos", []) if photo["photo_id"] == photo_id),
        None,
    )


def _decode_image(path: Path) -> Image.Image:
    with Image.open(path) as source:
        return ImageOps.exif_transpose(source).convert("RGB")


def _quality_metrics(image: Image.Image) -> Dict:
    gray = np.asarray(image.convert("L"), dtype=np.float32)
    if gray.size == 0:
        return {"brightness": None, "contrast": None, "sharpness": None}
    sharpness = float(np.var(np.diff(gray, axis=0)))
    return {
        "brightness": round(float(gray.mean()), 1),
        "contrast": round(float(gray.std()), 1),
        "sharpness": round(sharpness, 1) if math.isfinite(sharpness) else None,
    }


def _perceptual_hash(image: Image.Image) -> str:
    small = image.convert("L").resize((8, 8), Image.Resampling.BILINEAR)
    pixels = np.asarray(small, dtype=np.float32)
    bits = (pixels > pixels.mean()).flatten()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def refresh_duplicate_flags(manifest: Dict) -> Dict:
    """Mark ready photographs whose 64-bit average hash matches an earlier one.

    Derived from the stored hashes so the review screen can warn about
    duplicate uploads before any reconstruction work starts.
    """
    seen: List[tuple[int, Dict]] = []
    for photo in manifest.get("photos", []):
        quality = photo.setdefault("quality", {})
        if photo.get("processing", {}).get("status") != "ready":
            continue
        encoded = quality.get("perceptual_hash")
        try:
            image_hash = int(encoded, 16) if encoded else None
        except (TypeError, ValueError):
            image_hash = None
        duplicate_of = None
        if image_hash is not None:
            duplicate_of = next(
                (
                    other["original"].get("original_filename")
                    for other_hash, other in seen
                    if (image_hash ^ other_hash).bit_count() <= 2
                ),
                None,
            )
            if duplicate_of is None:
                seen.append((image_hash, photo))
        quality["duplicate_of"] = duplicate_of
    return manifest


def _populate_photo_original(job_id: str, photo: Dict, raw_path: Path):
    with Image.open(raw_path) as source:
        photo["original"]["mime_type"] = _mime_type(source, raw_path)
        oriented = ImageOps.exif_transpose(source)
        width, height = oriented.size
        if min(width, height) < 160:
            raise ValueError(f"image too small ({width}x{height}px)")
    photo["original"].update(
        {
            "width": int(width),
            "height": int(height),
            "aspect_ratio": round(width / max(1, height), 4),
            "orientation": (
                "portrait" if height > width else "landscape" if width > height else "square"
            ),
        }
    )
    if max(width, height) / max(1, min(width, height)) > MAX_ASPECT_RATIO:
        warning = (
            "The image has an unusual aspect ratio; previews preserve it without stretching."
        )
        if warning not in photo["quality"]["warnings"]:
            photo["quality"]["warnings"].append(warning)
    image = _decode_image(raw_path)
    photo["optimized"] = _save_variant(
        image,
        photos_root(job_id) / "optimized" / f"{photo['photo_id']}.jpg",
        OPTIMIZED_MAX_SIDE,
    )
    photo["thumbnail"] = _save_variant(
        image,
        photos_root(job_id) / "thumbs" / f"{photo['photo_id']}.jpg",
        THUMBNAIL_MAX_SIDE,
    )
    photo["quality"].update(_quality_metrics(image))
    photo["quality"]["perceptual_hash"] = _perceptual_hash(image)
    photo["processing"]["status"] = "ready"
    photo["processing"]["errors"] = []


def refresh_photo_classification(photo: Dict):
    photo["classification"] = {
        "status": "pending",
        "model": None,
        "predicted_room_type": None,
        "predicted_label": None,
        "confidence": None,
        "ambiguous": None,
        "candidates": [],
        "error": None,
        "classified_at": None,
    }
    photo["room_assignment"] = {
        "room_id": None,
        "confirmed": False,
        "excluded": False,
        "confirmed_at": None,
    }
    photo["match"] = {
        "status": "unassigned",
        "room_id": None,
        "confidence": None,
        "reason": "Awaiting classification and review.",
    }
    photo["textures"] = {
        "wall_texture": None,
        "floor_texture": None,
        "wall_rgb": None,
        "floor_rgb": None,
    }


def build_photo_manifest(
    job_id: str,
    property_id: str,
    photo_inputs: List[Dict],
    photo_paths: List[str],
) -> Dict:
    """Create manifest entries and derivatives for the initial upload."""
    if len(photo_inputs) != len(photo_paths):
        raise ValueError("photo_inputs and photo_paths must describe the same uploads")
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "job_id": job_id,
        "property_id": property_id,
        "created_at": _now(),
        "updated_at": _now(),
        "classifier": {
            "status": "pending",
            "model": None,
            "labels": list(ROOM_TYPES),
        },
        "review": {
            "status": "pending",
            "confirmed_at": None,
            "room_types": {},
        },
        "photos": [],
    }
    for index, (entry, raw_path) in enumerate(zip(photo_inputs, photo_paths)):
        photo_id = f"ph-{uuid.uuid4().hex[:12]}"
        photo = _empty_photo(
            job_id,
            property_id,
            photo_id,
            original_filename=entry.get("original_filename") or Path(raw_path).name,
            stored_filename=entry.get("stored_filename") or Path(raw_path).name,
            size_bytes=int(entry.get("size_bytes") or 0),
            sha256=entry.get("sha256") or "",
        )
        manifest["photos"].append(photo)
        try:
            _populate_photo_original(job_id, photo, Path(raw_path))
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            photo["processing"]["status"] = "rejected"
            photo["processing"]["errors"].append(str(exc))
    save_photo_manifest(manifest)
    return manifest


def add_photo_to_manifest(
    manifest: Dict, raw_path: str, original_filename: str, sha256: str
) -> Dict:
    """Register a replacement/additional upload without touching existing entries."""
    job_id = manifest["job_id"]
    path = Path(raw_path)
    photo_id = f"ph-{uuid.uuid4().hex[:12]}"
    photo = _empty_photo(
        job_id,
        manifest["property_id"],
        photo_id,
        original_filename=original_filename,
        stored_filename=path.name,
        size_bytes=path.stat().st_size if path.exists() else 0,
        sha256=sha256,
    )
    manifest["photos"].append(photo)
    try:
        _populate_photo_original(job_id, photo, path)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        photo["processing"]["status"] = "rejected"
        photo["processing"]["errors"].append(str(exc))
    save_photo_manifest(manifest)
    return photo


def replace_photo_in_manifest(
    manifest: Dict,
    photo_id: str,
    stored_filename: str,
    original_filename: str,
    sha256: str,
) -> Dict:
    """Replace a job-scoped photo file in place and reset its review state."""
    photo = photo_by_id(manifest, photo_id)
    if photo is None:
        raise KeyError(photo_id)
    photo["original"] = {
        "original_filename": original_filename,
        "stored_filename": stored_filename,
        "size_bytes": (INPUT_DIR / manifest["job_id"] / stored_filename).stat().st_size,
        "sha256": sha256,
        "mime_type": None,
        "width": None,
        "height": None,
        "aspect_ratio": None,
        "orientation": None,
    }
    photo["quality"]["warnings"] = []
    photo["processing"] = {"status": "ready", "errors": []}
    refresh_photo_classification(photo)
    try:
        _populate_photo_original(
            manifest["job_id"], photo, INPUT_DIR / manifest["job_id"] / stored_filename
        )
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        photo["processing"]["status"] = "rejected"
        photo["processing"]["errors"].append(str(exc))
    manifest["review"] = {"status": "pending", "confirmed_at": None, "room_types": {}}
    save_photo_manifest(manifest)
    return photo


def remove_photo_from_manifest(manifest: Dict, photo_id: str) -> bool:
    photos = manifest.get("photos", [])
    kept = [photo for photo in photos if photo.get("photo_id") != photo_id]
    if len(kept) == len(photos):
        return False
    manifest["photos"] = kept
    save_photo_manifest(manifest)
    return True


def exclude_photo(manifest: Dict, photo_id: str) -> Optional[Dict]:
    photo = photo_by_id(manifest, photo_id)
    if photo is None:
        return None
    photo["room_assignment"] = {
        "room_id": None,
        "confirmed": True,
        "excluded": True,
        "confirmed_at": _now(),
    }
    photo["match"] = {
        "status": "excluded",
        "room_id": None,
        "confidence": None,
        "reason": "Excluded from the walkthrough by the user.",
    }
    save_photo_manifest(manifest)
    return photo


def usable_photos(manifest: Optional[Dict]) -> List[Dict]:
    if not manifest:
        return []
    return [
        photo
        for photo in manifest.get("photos", [])
        if photo.get("processing", {}).get("status") == "ready"
        and not photo.get("room_assignment", {}).get("excluded")
    ]


def included_photo_paths(manifest: Dict) -> List[str]:
    job_id = manifest["job_id"]
    paths = []
    for photo in usable_photos(manifest):
        path = INPUT_DIR / job_id / photo["original"]["stored_filename"]
        if path.is_file():
            paths.append(str(path))
    return paths


def photo_variant_path(job_id: str, photo: Dict, variant: str) -> Path:
    if variant == "original":
        return INPUT_DIR / job_id / photo["original"]["stored_filename"]
    if variant not in {"thumbnail", "optimized"}:
        raise ValueError(f"unknown photo variant {variant}")
    entry = photo.get(variant)
    if not entry:
        raise FileNotFoundError(variant)
    folder = "thumbs" if variant == "thumbnail" else "optimized"
    return photos_root(job_id) / folder / entry["filename"]


def public_photo(job_id: str, photo: Dict) -> Dict:
    return {
        "photo_id": photo["photo_id"],
        "original_filename": photo["original"].get("original_filename"),
        "mime_type": photo["original"].get("mime_type"),
        "width": photo["original"].get("width"),
        "height": photo["original"].get("height"),
        "aspect_ratio": photo["original"].get("aspect_ratio"),
        "orientation": photo["original"].get("orientation"),
        "thumbnail_url": f"/reconstructions/{job_id}/photos/{photo['photo_id']}/thumbnail",
        "preview_url": f"/reconstructions/{job_id}/photos/{photo['photo_id']}/optimized",
        "original_url": f"/reconstructions/{job_id}/photos/{photo['photo_id']}/original",
        "classification": photo.get("classification"),
        "room_assignment": photo.get("room_assignment"),
        "match": photo.get("match"),
        "quality": photo.get("quality"),
        "camera": photo.get("camera"),
        "visible": photo.get("visible"),
        "processing": photo.get("processing"),
    }


def update_manifest_photo(
    manifest: Dict, photo_id: str, updates: Dict
) -> Optional[Dict]:
    for photo in manifest.get("photos", []):
        if photo.get("photo_id") != photo_id:
            continue
        for key, value in updates.items():
            if isinstance(value, dict) and isinstance(photo.get(key), dict):
                photo[key].update(value)
            else:
                photo[key] = value
        save_photo_manifest(manifest)
        return photo
    return None


def match_summary(manifest: Dict) -> Dict:
    photos = manifest.get("photos", [])
    ready = [p for p in photos if p.get("processing", {}).get("status") == "ready"]
    assigned = [
        p
        for p in ready
        if p.get("room_assignment", {}).get("confirmed")
        and p.get("room_assignment", {}).get("room_id") is not None
        and not p.get("room_assignment", {}).get("excluded")
    ]
    excluded = [p for p in ready if p.get("room_assignment", {}).get("excluded")]
    rejected = [p for p in photos if p.get("processing", {}).get("status") == "rejected"]
    return {
        "total": len(photos),
        "ready": len(ready),
        "assigned": len(assigned),
        "excluded": len(excluded),
        "rejected": len(rejected),
        "conflicts": sum(
            1
            for p in assigned
            if (p.get("match") or {}).get("conflict")
        ),
        "needs_review": len(ready) - len(assigned) - len(excluded),
        "classifier": manifest.get("classifier", {}),
    }


def assignments_by_room(manifest: Dict) -> Dict[int, List[Dict]]:
    grouped: Dict[int, List[Dict]] = {}
    for photo in usable_photos(manifest):
        assignment = photo.get("room_assignment") or {}
        room_id = assignment.get("room_id")
        if room_id is None:
            continue
        grouped.setdefault(int(room_id), []).append(photo)
    return grouped


def apply_photo_analysis(manifest: Dict, photo_result: Dict) -> Dict:
    """Attach texture crops and quality measurements to their photo entries."""
    by_name = {
        entry.get("file"): entry for entry in photo_result.get("textures", [])
    }
    measurements = {
        entry.get("file"): entry for entry in photo_result.get("photos", [])
    }
    for photo in manifest.get("photos", []):
        stored = photo["original"].get("stored_filename")
        texture = by_name.get(stored)
        if texture:
            photo["textures"] = {
                "wall_texture": texture.get("wall_texture"),
                "floor_texture": texture.get("floor_texture"),
                "wall_rgb": texture.get("wall_rgb"),
                "floor_rgb": texture.get("floor_rgb"),
            }
        measured = measurements.get(stored)
        if measured:
            photo["quality"].update(
                {
                    "brightness": measured.get("brightness"),
                    "contrast": measured.get("contrast"),
                    "sharpness": measured.get("sharpness"),
                }
            )
            if measured.get("duplicate_of") and not photo["quality"].get("duplicate_of"):
                photo["quality"]["duplicate_of"] = measured["duplicate_of"]
    save_photo_manifest(manifest)
    return manifest


def apply_camera_poses(manifest: Dict, photogrammetry: Dict) -> Dict:
    """Map COLMAP camera poses back to the manifest's stable photo IDs."""
    prepared_by_name = {
        entry.get("file"): entry for entry in photogrammetry.get("images", [])
    }
    cameras_by_sfm = {
        entry.get("file"): entry for entry in photogrammetry.get("cameras", [])
    }
    for photo in manifest.get("photos", []):
        prepared = prepared_by_name.get(photo["original"].get("stored_filename"))
        if not prepared:
            photo["camera"] = {
                "status": "not_used",
                "center": None,
                "viewing_direction": None,
                "registered": False,
            }
            continue
        camera = cameras_by_sfm.get(prepared.get("sfm_file"))
        if camera:
            photo["camera"] = {
                "status": "registered",
                "center": camera.get("center"),
                "viewing_direction": camera.get("viewing_direction"),
                "registered": True,
                "num_points3d": camera.get("num_points3d"),
            }
        else:
            photo["camera"] = {
                "status": "unregistered",
                "center": None,
                "viewing_direction": None,
                "registered": False,
            }
    save_photo_manifest(manifest)
    return manifest


def iter_photo_files(job_id: str) -> Iterable[Path]:
    root = photos_root(job_id)
    if not root.exists():
        return []
    return [path for path in root.rglob("*") if path.is_file()]
