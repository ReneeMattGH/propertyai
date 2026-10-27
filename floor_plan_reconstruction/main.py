"""
RealEstAI property reconstruction service.

Upload a floor plan and receive an interactive, furnished, walkable
three-dimensional scene. The floor plan is the only required input: room
types are inferred from plan geometry, confirmed by the user, and furniture
is placed with deterministic rules from an original procedural asset library.

Endpoints:
  POST /reconstructions                        submit a new reconstruction
  GET  /reconstructions                        list jobs (reopen past tours)
  GET  /reconstructions/{id}                   status + progress + report data
  GET  /reconstructions/{id}/room-types        inferred room types awaiting review
  POST /reconstructions/{id}/room-types        confirm/correct room types
  POST /reconstructions/{id}/furnishings       change style or regenerate layout
  PATCH /reconstructions/{id}/rooms/{room}     change one room's type
  GET  /reconstructions/{id}/scene             collision volumes, room graph, manifest
  GET  /reconstructions/{id}/manifest          asset manifest
  GET  /reconstructions/{id}/assets            generated asset list
  GET  /reconstructions/{id}/assets/{f}        download an asset (path-safe)
  GET  /reconstructions/{id}/logs              human-readable job log
  POST /reconstructions/{id}/cancel            request cancellation
  POST /reconstructions/{id}/retry             retry a failed/cancelled job

The pipeline runs in background tasks and CPU-bound stages are offloaded to
worker threads so the HTTP API stays responsive. The legacy photo-review
helpers remain for jobs created by older releases, but new jobs never upload
or require photographs.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

BASE_DIR = Path(__file__).parent
# On a hosted deployment the application directory is replaced on every
# release. Point REALESTAI_DATA_DIR at a persistent volume so uploaded plans,
# generated scenes and job state survive restarts.
DATA_DIR = Path(os.environ.get("REALESTAI_DATA_DIR", str(BASE_DIR))).expanduser().resolve()
INPUT_DIR = DATA_DIR / "input"
FLOOR_PLANS_DIR = DATA_DIR / "floor_plans"
PHOTOS_DIR = DATA_DIR / "photos"
GEOMETRY_DIR = DATA_DIR / "geometry"
OUTPUT_DIR = DATA_DIR / "output"
LOGS_DIR = DATA_DIR / "logs"
JOBS_FILE = DATA_DIR / "jobs.json"

for directory in (INPUT_DIR, FLOOR_PLANS_DIR, PHOTOS_DIR, GEOMETRY_DIR, OUTPUT_DIR, LOGS_DIR):
    directory.mkdir(parents=True, exist_ok=True)

MAX_FLOOR_PLAN_BYTES = 15 * 1024 * 1024
MAX_PHOTO_BYTES = 15 * 1024 * 1024
MAX_PHOTOS = 40
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class JobStatus:
    QUEUED = "queued"
    PARSING_FLOOR_PLAN = "parsing_floor_plan"
    DETECTING_ROOMS_OPENINGS = "detecting_rooms_openings"
    VALIDATING_ROOM_LAYOUT = "validating_room_layout"
    AWAITING_ROOM_TYPES = "awaiting_room_types"
    OPTIMIZING_OPENINGS = "optimizing_openings"
    GENERATING_GEOMETRY = "generating_geometry"
    PLACING_FURNITURE = "placing_furniture"
    GENERATING_LIGHTING_MATERIALS = "generating_lighting_materials"
    VALIDATING_NAVIGATION_COLLISIONS = "validating_navigation_collisions"
    LOADING_WALKTHROUGH = "loading_walkthrough"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    # Legacy photo-workflow states: kept so pre-existing jobs stay readable.
    PREPROCESSING = "preprocessing"
    DETECTING_ROOMS = "detecting_rooms"
    VALIDATING_LAYOUT = "validating_layout"
    CLASSIFYING_PHOTOS = "classifying_photos"
    MATCHING_PHOTOS = "matching_photos"
    AWAITING_REVIEW = "awaiting_review"
    ANALYZING_PHOTOS = "analyzing_photos"
    RECONSTRUCTING = "reconstructing_images"
    BUILDING_GEOMETRY = "building_geometry"
    TEXTURING = "texturing"
    EXPORTING = "exporting"
    VALIDATING_ASSETS = "validating_assets"


STAGE_LABELS = {
    JobStatus.QUEUED: "Queued",
    JobStatus.PARSING_FLOOR_PLAN: "Parsing floor plan",
    JobStatus.DETECTING_ROOMS_OPENINGS: "Detecting rooms and openings",
    JobStatus.VALIDATING_ROOM_LAYOUT: "Checking room layout",
    JobStatus.AWAITING_ROOM_TYPES: "Confirm room types",
    JobStatus.OPTIMIZING_OPENINGS: "Optimizing doors and walkways",
    JobStatus.GENERATING_GEOMETRY: "Building the 3D house",
    JobStatus.PLACING_FURNITURE: "Arranging furniture",
    JobStatus.GENERATING_LIGHTING_MATERIALS: "Finishing materials and lighting",
    JobStatus.VALIDATING_NAVIGATION_COLLISIONS: "Testing navigation",
    JobStatus.LOADING_WALKTHROUGH: "Preparing walkthrough",
    JobStatus.COMPLETED: "Ready to explore",
    JobStatus.FAILED: "Failed",
    JobStatus.CANCELLED: "Cancelled",
    # Legacy
    JobStatus.PREPROCESSING: "Preprocessing inputs",
    JobStatus.DETECTING_ROOMS: "Detecting walls and rooms",
    JobStatus.VALIDATING_LAYOUT: "Validating layout",
    JobStatus.CLASSIFYING_PHOTOS: "Classifying room photographs",
    JobStatus.MATCHING_PHOTOS: "Matching photos to rooms",
    JobStatus.AWAITING_REVIEW: "Review photo assignments",
    JobStatus.ANALYZING_PHOTOS: "Analysing photographs",
    JobStatus.RECONSTRUCTING: "Reconstructing from images",
    JobStatus.BUILDING_GEOMETRY: "Building geometry",
    JobStatus.TEXTURING: "Texturing surfaces",
    JobStatus.EXPORTING: "Exporting scene assets",
    JobStatus.VALIDATING_ASSETS: "Validating scene assets",
}

STAGE_PROGRESS = {
    JobStatus.QUEUED: 0.0,
    JobStatus.PARSING_FLOOR_PLAN: 6.0,
    JobStatus.DETECTING_ROOMS_OPENINGS: 18.0,
    JobStatus.VALIDATING_ROOM_LAYOUT: 28.0,
    JobStatus.AWAITING_ROOM_TYPES: 34.0,
    JobStatus.OPTIMIZING_OPENINGS: 40.0,
    JobStatus.GENERATING_GEOMETRY: 48.0,
    JobStatus.PLACING_FURNITURE: 62.0,
    JobStatus.GENERATING_LIGHTING_MATERIALS: 76.0,
    JobStatus.VALIDATING_NAVIGATION_COLLISIONS: 90.0,
    JobStatus.LOADING_WALKTHROUGH: 97.0,
    JobStatus.COMPLETED: 100.0,
    # Legacy photo workflow
    JobStatus.PREPROCESSING: 5.0,
    JobStatus.DETECTING_ROOMS: 15.0,
    JobStatus.VALIDATING_LAYOUT: 28.0,
    JobStatus.CLASSIFYING_PHOTOS: 34.0,
    JobStatus.MATCHING_PHOTOS: 42.0,
    JobStatus.AWAITING_REVIEW: 46.0,
    JobStatus.ANALYZING_PHOTOS: 50.0,
    JobStatus.RECONSTRUCTING: 56.0,
    JobStatus.BUILDING_GEOMETRY: 70.0,
    JobStatus.TEXTURING: 80.0,
    JobStatus.EXPORTING: 89.0,
    JobStatus.VALIDATING_ASSETS: 95.0,
}

PARSER_STAGE_MAP = {
    "preprocessing": JobStatus.PARSING_FLOOR_PLAN,
    "detecting_rooms": JobStatus.DETECTING_ROOMS_OPENINGS,
    "validating_layout": JobStatus.VALIDATING_ROOM_LAYOUT,
}

GEOMETRY_STAGE_MAP = {
    "generating_geometry": JobStatus.GENERATING_GEOMETRY,
    "texturing": JobStatus.GENERATING_GEOMETRY,
    "placing_furniture": JobStatus.PLACING_FURNITURE,
    "generating_lighting_materials": JobStatus.GENERATING_LIGHTING_MATERIALS,
    "validating_navigation_collisions": JobStatus.VALIDATING_NAVIGATION_COLLISIONS,
    "exporting": JobStatus.LOADING_WALKTHROUGH,
    "loading_walkthrough": JobStatus.LOADING_WALKTHROUGH,
}

ROOM_TYPE_REVIEW_FILE = "room_types.json"
MAX_FURNISHING_VARIANT = 50


class RetryRequest(BaseModel):
    preprocessing: Optional[Dict[str, str]] = None


class ManualRoomInput(BaseModel):
    polygon_px: List[List[float]]


class ManualDoorwayInput(BaseModel):
    orientation: str
    center_px: List[float]
    width_px: Optional[float] = None


class ManualPlanRequest(BaseModel):
    rooms: List[ManualRoomInput]
    doorways: List[ManualDoorwayInput] = []
    wall_thickness_px: Optional[float] = None


class PhotoAssignmentInput(BaseModel):
    photo_id: str
    room_id: Optional[int] = None
    excluded: bool = False


class ConfirmAssignmentsRequest(BaseModel):
    assignments: List[PhotoAssignmentInput]
    room_types: Optional[Dict[str, str]] = None


class RoomTypeInput(BaseModel):
    room_id: int
    room_type: str
    name: Optional[str] = None


class ConfirmRoomTypesRequest(BaseModel):
    rooms: List[RoomTypeInput]
    style: Optional[str] = None
    variant: Optional[int] = None


class FurnishingRequest(BaseModel):
    style: Optional[str] = None
    variant: Optional[int] = None
    room_types: Optional[List[RoomTypeInput]] = None
    regenerate: bool = False


class RoomUpdateRequest(BaseModel):
    room_type: str
    name: Optional[str] = None


class JobResponse(BaseModel):
    job_id: str
    property_id: Optional[str] = None
    status: str
    current_stage: str
    stage_label: Optional[str] = None
    progress: float
    created_at: str
    updated_at: str
    error: Optional[str] = None
    error_code: Optional[str] = None
    failed_stage: Optional[str] = None
    project_name: Optional[str] = None
    floor_plan_filename: Optional[str] = None
    photo_count: Optional[int] = None
    num_floors: Optional[int] = None
    known_dimensions: Optional[Dict[str, float]] = None
    inputs: Optional[Dict[str, Any]] = None
    stage_records: Optional[List[Dict[str, Any]]] = None
    diagnostics: Optional[Dict[str, Any]] = None
    assets: Optional[List[Dict[str, Any]]] = None
    warnings: Optional[List[str]] = None
    limitations: Optional[List[str]] = None
    geometry_valid: Optional[bool] = None
    navigation_available: Optional[bool] = None
    scale_verified: Optional[bool] = None
    manifest_url: Optional[str] = None
    scene_url: Optional[str] = None
    review_url: Optional[str] = None
    floor_plan_url: Optional[str] = None
    diagnostics_url: Optional[str] = None
    retry_count: Optional[int] = None
    manual_outline: Optional[Dict[str, Any]] = None
    cancel_requested: Optional[bool] = None
    review_required: Optional[bool] = None
    assignments_confirmed: Optional[bool] = None
    photo_review_summary: Optional[Dict[str, Any]] = None
    room_count: Optional[int] = None
    classifier: Optional[Dict[str, Any]] = None
    room_types_url: Optional[str] = None
    room_types_confirmed: Optional[bool] = None
    room_type_review: Optional[Dict[str, Any]] = None
    room_types: Optional[List[Dict[str, Any]]] = None
    furnishing_style: Optional[str] = None
    furnishing_variant: Optional[int] = None
    furniture_count: Optional[int] = None
    furniture_url: Optional[str] = None


app = FastAPI(
    title="RealEstAI Reconstruction Service",
    version="5.0.0",
    description="Floor plan to an interactive, automatically furnished 3D walkthrough",
)

frontend_urls = os.getenv(
    "FRONTEND_URLS", "http://localhost:3000,http://127.0.0.1:3000"
).split(",")
frontend_url_regex = os.getenv("FRONTEND_URL_REGEX", "").strip() or None
app.add_middleware(
    CORSMiddleware,
    allow_origins=[url.strip() for url in frontend_urls if url.strip()],
    allow_origin_regex=frontend_url_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_JOBS_LOCK = threading.RLock()


# ----------------------------------------------------------------------
# Job persistence (atomic, lock-protected)
# ----------------------------------------------------------------------
def _read_jobs_unlocked() -> Dict[str, Dict]:
    if not JOBS_FILE.exists():
        return {}
    try:
        return json.loads(JOBS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def load_jobs() -> Dict[str, Dict]:
    with _JOBS_LOCK:
        return _read_jobs_unlocked()


def _write_jobs_unlocked(jobs: Dict[str, Dict]):
    tmp = JOBS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(jobs, indent=2, default=str))
    tmp.replace(JOBS_FILE)


def update_job(job_id: str, **updates):
    with _JOBS_LOCK:
        jobs = _read_jobs_unlocked()
        if job_id not in jobs:
            return
        jobs[job_id].update(updates)
        jobs[job_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write_jobs_unlocked(jobs)


def get_job(job_id: str) -> Optional[Dict]:
    with _JOBS_LOCK:
        return _read_jobs_unlocked().get(job_id)


def log_event(job_id: str, message: str):
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = f"[{stamp}] {message}"
    print(f"[{job_id[:8]}] {message}")
    try:
        with open(LOGS_DIR / f"{job_id}.log", "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def _room_type_review_path(job_id: str) -> Path:
    return GEOMETRY_DIR / job_id / ROOM_TYPE_REVIEW_FILE


def load_room_type_review(job_id: str) -> Optional[Dict]:
    path = _room_type_review_path(job_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def save_room_type_review(job_id: str, payload: Dict):
    path = _room_type_review_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)


def _room_type_summary(review: Optional[Dict]) -> Optional[Dict]:
    if not review:
        return None
    rooms = review.get("rooms") or []
    return {
        "status": review.get("status"),
        "source": review.get("source"),
        "requires_confirmation": bool(review.get("requires_confirmation")),
        "rooms": len(rooms),
        "uncertain_rooms": [
            int(room["room_id"])
            for room in rooms
            if room.get("needs_confirmation") or room.get("room_type") == "unknown"
        ],
        "confirmed_at": review.get("confirmed_at"),
    }


def _confirmed_room_types(review: Optional[Dict]) -> List[Dict]:
    if not review:
        return []
    assignments = review.get("assignments") or []
    if assignments:
        return [
            {
                "room_id": int(entry["room_id"]),
                "name": entry.get("name"),
                "room_type": entry.get("room_type"),
                "source": "user",
            }
            for entry in assignments
        ]
    return [
        {
            "room_id": int(room["room_id"]),
            "name": room.get("name"),
            "room_type": room.get("room_type"),
            "source": room.get("source", "inferred"),
        }
        for room in review.get("rooms") or []
    ]


def _validated_style(style: Optional[str], current: Optional[str] = None) -> str:
    from furniture_assets import STYLES

    value = (style or current or "modern").strip().lower()
    if value not in STYLES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported furnishing style {style!r}. "
            f"Choose one of: {', '.join(STYLES)}.",
        )
    return value


def _validated_variant(variant: Optional[int], current: Optional[int] = None) -> int:
    value = current if variant is None else variant
    try:
        value = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="variant must be an integer") from exc
    if not 0 <= value <= MAX_FURNISHING_VARIANT:
        raise HTTPException(
            status_code=400,
            detail=f"variant must be between 0 and {MAX_FURNISHING_VARIANT}",
        )
    return value


def _apply_room_type_confirmation(
    review: Dict,
    floor_plan_result: Dict,
    selections: List[Dict],
    style: str,
    variant: int,
):
    from room_types import apply_names, normalize_confirmed_types

    rooms = floor_plan_result.get("rooms") or []
    metadata = normalize_confirmed_types({"rooms": selections}, rooms)
    apply_names(metadata, rooms)
    existing = {
        int(entry["room_id"]): entry for entry in review.get("rooms") or []
    }
    updated_rooms = []
    for room_id in sorted(metadata):
        meta = metadata[room_id]
        updated_rooms.append(
            {
                **existing.get(room_id, {"room_id": room_id}),
                "room_id": room_id,
                "room_type": meta["room_type"],
                "name": meta["name"],
                "confidence": 1.0,
                "needs_confirmation": False,
                "source": "user",
                "alternatives": [],
                "reasons": ["Room type confirmed by the user."],
            }
        )
    assignments = [
        {
            "room_id": room_id,
            "room_type": meta["room_type"],
            "name": meta["name"],
        }
        for room_id, meta in sorted(metadata.items())
    ]
    return (
        {
            **review,
            "rooms": updated_rooms,
            "assignments": assignments,
            "status": "confirmed",
            "source": "user",
            "requires_confirmation": False,
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
            "style": style,
            "variant": variant,
        },
        metadata,
    )


def _public_job(job: Dict) -> Dict:
    payload = dict(job)
    payload.pop("floor_plan_path", None)
    payload.pop("photo_paths", None)
    payload["job_id"] = payload.pop("id")
    payload["stage_label"] = STAGE_LABELS.get(payload.get("current_stage", ""), payload.get("current_stage"))
    payload["manifest_url"] = (
        f"/reconstructions/{payload['job_id']}/manifest"
        if payload.get("status") == JobStatus.COMPLETED
        else None
    )
    payload["scene_url"] = (
        f"/reconstructions/{payload['job_id']}/scene"
        if payload.get("status") == JobStatus.COMPLETED
        else None
    )
    payload["floor_plan_url"] = f"/reconstructions/{payload['job_id']}/floor-plan"
    payload["diagnostics_url"] = f"/reconstructions/{payload['job_id']}/diagnostics"
    has_room_types = _room_type_review_path(payload["job_id"]).is_file()
    payload["room_types_url"] = (
        f"/reconstructions/{payload['job_id']}/room-types" if has_room_types else None
    )
    has_manifest = (
        GEOMETRY_DIR / payload["job_id"] / "photo_manifest.json"
    ).is_file()
    payload["review_url"] = (
        f"/reconstructions/{payload['job_id']}/review" if has_manifest else None
    )
    if "room_type_review" not in payload or payload.get("room_type_review") is None:
        review = load_room_type_review(payload["job_id"]) if has_room_types else None
        payload["room_type_review"] = _room_type_summary(review)
    if payload.get("room_types") is None:
        payload["room_types"] = []
    payload["furniture_url"] = (
        f"/reconstructions/{payload['job_id']}/assets/furniture.json"
        if payload.get("status") == JobStatus.COMPLETED
        and (OUTPUT_DIR / payload["job_id"] / "furniture.json").is_file()
        else None
    )
    return payload


class PipelineCancelled(Exception):
    pass


class ReconstructionPipeline:
    """Orchestrates validation → parse → photos → geometry → validation."""

    def __init__(self):
        from geometry_builder import GeometryBuilder, write_manifest
        from improved_floor_plan_parser import ImprovedFloorPlanParser
        from photo_analyzer import PhotoAnalyzer
        from photogrammetry import PhotogrammetryEngine
        from scene_validator import SceneValidator

        self.parser = ImprovedFloorPlanParser()
        self.photo_analyzer = PhotoAnalyzer()
        self.photogrammetry = PhotogrammetryEngine()
        self.geometry_builder = GeometryBuilder()
        self.validator = SceneValidator()
        self.write_manifest = write_manifest

    def _check_cancelled(self, job_id: str):
        job = get_job(job_id) or {}
        if job.get("cancel_requested"):
            raise PipelineCancelled("Cancelled by user")

    def _set_stage(self, job_id: str, status: str):
        self._check_cancelled(job_id)
        job = get_job(job_id) or {}
        if job.get("status") == status:
            return
        progress = max(
            float(job.get("progress") or 0.0), STAGE_PROGRESS.get(status, 0.0)
        )
        update_job(
            job_id,
            status=status,
            current_stage=status,
            progress=progress,
            failed_stage=None,
            error_code=None,
        )
        log_event(job_id, f"{STAGE_LABELS.get(status, status)}")

    def _parser_progress(self, job_id: str):
        def callback(stage: str):
            mapped = PARSER_STAGE_MAP.get(stage)
            if mapped:
                self._set_stage(job_id, mapped)

        return callback

    def _geometry_progress(self, job_id: str):
        def callback(stage: str):
            mapped = GEOMETRY_STAGE_MAP.get(stage)
            if mapped:
                self._set_stage(job_id, mapped)

        return callback

    def _fail_job(
        self,
        job_id: str,
        failed_stage: str,
        error: str,
        error_code: Optional[str] = None,
        diagnostics: Optional[Dict] = None,
    ):
        job = get_job(job_id) or {}
        progress = float(job.get("progress") or 0.0)
        if failed_stage in STAGE_PROGRESS:
            progress = min(progress, STAGE_PROGRESS[failed_stage])
        update_job(
            job_id,
            status=JobStatus.FAILED,
            current_stage="failed",
            progress=progress,
            failed_stage=failed_stage,
            error_code=error_code,
            error=error,
            diagnostics=diagnostics if diagnostics is not None else job.get("diagnostics") or {},
        )
        log_event(job_id, f"Failed during {STAGE_LABELS.get(failed_stage, failed_stage)}: {error}")

    def _record_stage(
        self,
        job_id: str,
        stage_records: List[Dict],
        stage: str,
        status: str,
        detail: Optional[Dict] = None,
    ):
        stage_records.append(
            {
                "stage": stage,
                "status": status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **({"detail": detail} if detail else {}),
            }
        )
        update_job(job_id, stage_records=stage_records)

    @staticmethod
    def _repair_diagnostics(repair_result: Dict) -> Dict[str, Any]:
        repair_log = repair_result.get("repair_log") or {}
        normalization = repair_log.get("normalization") or {}
        attempts = repair_log.get("attempts") or []
        repairs = repair_log.get("repairs") or []
        return {
            "status": repair_log.get("status"),
            "error_code": repair_result.get("error_code"),
            "attempt_count": len(attempts),
            "repair_count": len(repairs),
            "conservative_fallback": bool(repair_log.get("conservative_fallback")),
            "normalization": {
                "valid": normalization.get("valid"),
                "repairs": (normalization.get("repairs") or [])[:40],
                "ambiguities": normalization.get("ambiguities") or [],
                "warnings": normalization.get("warnings") or [],
            },
            "repairs": repairs[-40:],
            "attempts_detail": [
                {
                    key: attempt.get(key)
                    for key in (
                        "attempt",
                        "phase",
                        "geometry_valid",
                        "navigation_available",
                        "errors",
                        "navigation",
                        "repairs",
                        "elapsed_s",
                    )
                }
                for attempt in attempts
            ],
            "unresolved": repair_log.get("unresolved") or [],
            "log_url": f"/reconstructions/{repair_log.get('job_id')}/assets/repair_log.json"
            if repair_log.get("job_id")
            else None,
        }

    def _persist_repair_artifacts(
        self,
        output_dir: Path,
        repair_result: Dict,
        validation: Optional[Dict],
    ) -> List[str]:
        """Write the developer artifacts even when the job cannot be published."""
        written: List[str] = []
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return written
        payloads = {
            "house_model.json": repair_result.get("house_model"),
            "repair_log.json": repair_result.get("repair_log"),
        }
        for name, payload in payloads.items():
            if not payload:
                continue
            try:
                (output_dir / name).write_text(json.dumps(payload, indent=2))
                written.append(name)
            except OSError:
                continue
        if validation is not None:
            try:
                (output_dir / "validation_report.json").write_text(
                    json.dumps(validation, indent=2)
                )
                written.append("validation_report.json")
            except OSError:
                pass
        return written

    async def run(
        self,
        job_id: str,
        floor_plan_path: str,
        photo_paths: List[str],
        num_floors: int,
        known_width: Optional[float],
        known_length: Optional[float],
        preprocessing: Optional[Dict] = None,
        manual_outline: Optional[Dict] = None,
    ):
        """Preparation stage: validate, parse, classify and stop for review."""
        await self.prepare(
            job_id,
            floor_plan_path,
            photo_paths,
            num_floors,
            known_width,
            known_length,
            preprocessing,
            manual_outline,
        )

    def _photo_manifest_for_job(
        self,
        job: Dict,
        job_id: str,
        photo_paths: List[str],
    ) -> Dict:
        from photo_manifest import (
            build_photo_manifest,
            load_photo_manifest,
        )

        inputs = (job.get("inputs") or {}).get("photos") or []
        stored = {Path(path).name for path in photo_paths}
        manifest = load_photo_manifest(job_id)
        if manifest and {
            photo["original"].get("stored_filename")
            for photo in manifest.get("photos", [])
        } == stored:
            return manifest
        if not inputs:
            inputs = []
            for index, raw_path in enumerate(photo_paths):
                path = Path(raw_path)
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                inputs.append(
                    {
                        "original_filename": path.name,
                        "stored_filename": path.name,
                        "size_bytes": path.stat().st_size,
                        "sha256": digest,
                    }
                )
        return build_photo_manifest(
            job_id, job.get("property_id") or job_id, inputs, photo_paths
        )

    async def prepare(
        self,
        job_id: str,
        floor_plan_path: str,
        photo_paths: List[str],
        num_floors: int,
        known_width: Optional[float],
        known_length: Optional[float],
        preprocessing: Optional[Dict] = None,
        manual_outline: Optional[Dict] = None,
    ):
        import asyncio

        from photo_manifest import (
            ROOM_TYPES,
            included_photo_paths,
            save_photo_manifest,
            usable_photos,
        )
        from room_classifier import RoomClassifier
        from room_matcher import room_shells, suggest_assignments

        stage_records: List[Dict] = []
        try:
            job = get_job(job_id) or {}
            inputs = job.get("inputs") or {}

            self._set_stage(job_id, JobStatus.PREPROCESSING)
            validation = await self.parser.validate_inputs(floor_plan_path, photo_paths)
            diagnostics: Dict[str, Any] = {"input_validation": validation}
            update_job(job_id, diagnostics=diagnostics)
            if validation["errors"]:
                self._fail_job(
                    job_id,
                    JobStatus.PREPROCESSING,
                    "; ".join(validation["errors"]),
                    "invalid_inputs",
                    diagnostics,
                )
                return
            self._record_stage(
                job_id,
                stage_records,
                "input_validation",
                "completed",
                {
                    "valid_photos": validation["photos"]["valid_count"],
                    "input_photos": len(photo_paths),
                },
            )

            self._set_stage(job_id, JobStatus.DETECTING_ROOMS)
            if manual_outline:
                log_event(job_id, "Parsing user-supplied manual room outlines")
                floor_plan_result = await self.parser.parse_manual_outline(
                    job_id,
                    floor_plan_path,
                    known_width,
                    known_length,
                    manual_outline.get("rooms") or [],
                    manual_outline.get("doorways") or [],
                    manual_outline.get("wall_thickness_px"),
                    progress_callback=lambda stage: self._set_stage(job_id, stage),
                )
            else:
                floor_plan_result = await self.parser.parse_floor_plan(
                    job_id,
                    floor_plan_path,
                    known_width,
                    known_length,
                    progress_callback=lambda stage: self._set_stage(job_id, stage),
                    preprocessing=preprocessing,
                )
            self._check_cancelled(job_id)
            diagnostics["floor_plan"] = floor_plan_result
            update_job(job_id, diagnostics=diagnostics)
            if floor_plan_result.get("status") != "completed":
                self._fail_job(
                    job_id,
                    JobStatus.DETECTING_ROOMS,
                    floor_plan_result.get("error", "Floor plan parsing failed"),
                    floor_plan_result.get("error_code"),
                    diagnostics,
                )
                return
            prepared_plan_path = GEOMETRY_DIR / job_id / "floor_plan.json"
            prepared_plan_path.parent.mkdir(parents=True, exist_ok=True)
            prepared_plan_path.write_text(json.dumps(floor_plan_result, indent=2))
            self._record_stage(
                job_id,
                stage_records,
                "floor_plan_parse",
                "completed",
                {
                    "rooms": len(floor_plan_result["rooms"]),
                    "walls": len(floor_plan_result["wall_segments"]),
                    "doorways": len(floor_plan_result["doorways"]),
                    "scale_pixels_per_meter": floor_plan_result["scale_pixels_per_meter"],
                    "scale_source": floor_plan_result["scale_source"],
                    "scale_verified": bool(floor_plan_result.get("scale_verified")),
                },
            )
            log_event(
                job_id,
                f"Floor plan parsed: {len(floor_plan_result['rooms'])} rooms, "
                f"{len(floor_plan_result['wall_segments'])} walls, "
                f"{len(floor_plan_result['doorways'])} doorway(s)",
            )

            manifest = self._photo_manifest_for_job(job, job_id, photo_paths)
            ready = usable_photos(manifest)
            if not ready:
                self._fail_job(
                    job_id,
                    JobStatus.ANALYZING_PHOTOS,
                    "None of the uploaded photographs could be decoded",
                    "no_usable_photos",
                    diagnostics,
                )
                return

            self._set_stage(job_id, JobStatus.CLASSIFYING_PHOTOS)
            pending = [
                photo
                for photo in ready
                if (photo.get("classification") or {}).get("status") in {"pending", "unavailable"}
            ]
            if pending:
                classify_paths = [
                    str(INPUT_DIR / job_id / photo["original"]["stored_filename"])
                    for photo in pending
                ]
                results = await asyncio.to_thread(
                    RoomClassifier().classify_paths, classify_paths
                )
                for photo, result in zip(pending, results):
                    photo["classification"] = {
                        **result,
                        "classified_at": datetime.now(timezone.utc).isoformat(),
                    }
                log_event(
                    job_id,
                    f"Classified {len(pending)} photograph(s) with "
                    f"{results[0].get('model') if results else 'no model'}",
                )
            classifications = [
                photo.get("classification") or {} for photo in ready
            ]
            if any(item.get("status") in {"classified", "ambiguous"} for item in classifications):
                classifier_status = (
                    "completed"
                    if all(item.get("status") in {"classified", "ambiguous"} for item in classifications)
                    else "partial"
                )
            else:
                classifier_status = "unavailable"
            models = sorted(
                {item.get("model") for item in classifications if item.get("model")}
            )
            manifest["classifier"] = {
                "status": classifier_status,
                "model": models[0] if models else None,
                "labels": list(ROOM_TYPES),
                "classified_at": datetime.now(timezone.utc).isoformat(),
                "error": next(
                    (
                        item.get("error")
                        for item in classifications
                        if item.get("error")
                    ),
                    None,
                ),
            }

            self._set_stage(job_id, JobStatus.MATCHING_PHOTOS)
            rooms = room_shells(floor_plan_result)
            for photo in ready:
                photo["room_assignment"] = {
                    "room_id": None,
                    "confirmed": False,
                    "excluded": False,
                    "confirmed_at": None,
                }
            manifest["review"] = {
                "status": "pending",
                "confirmed_at": None,
                "room_types": {},
            }
            suggestions = {
                item["photo_id"]: item for item in suggest_assignments(rooms, manifest)
            }
            for photo in ready:
                suggestion = suggestions.get(photo["photo_id"]) or {}
                photo["match"] = {
                    "status": suggestion.get("status", "needs_review"),
                    "room_id": suggestion.get("room_id"),
                    "confidence": suggestion.get("confidence"),
                    "reason": suggestion.get("reason"),
                }
            save_photo_manifest(manifest)
            self._record_stage(
                job_id,
                stage_records,
                "photo_classification",
                classifier_status,
                {
                    "photos": len(ready),
                    "model": manifest["classifier"]["model"],
                    "ambiguous": sum(
                        1 for item in classifications if item.get("ambiguous")
                    ),
                },
            )
            self._record_stage(
                job_id,
                stage_records,
                "room_matching",
                "review_required",
                {
                    "rooms": len(rooms),
                    "photos": len(ready),
                    "auto_assigned": 0,
                },
            )

            from photo_manifest import match_summary

            summary = match_summary(manifest)
            diagnostics["photo_review"] = summary
            diagnostics["photos"] = {
                "total_photos": len(manifest.get("photos", [])),
                "analyzed_photos": len(ready),
                "classifier": manifest["classifier"],
                "errors": [
                    {
                        "file": photo["original"].get("original_filename"),
                        "error": "; ".join(photo["processing"].get("errors") or []),
                    }
                    for photo in manifest.get("photos", [])
                    if photo["processing"].get("status") == "rejected"
                ],
            }
            update_job(
                job_id,
                status=JobStatus.AWAITING_REVIEW,
                current_stage=JobStatus.AWAITING_REVIEW,
                progress=STAGE_PROGRESS[JobStatus.AWAITING_REVIEW],
                failed_stage=None,
                error=None,
                error_code=None,
                diagnostics=diagnostics,
                stage_records=stage_records,
                review_required=True,
                assignments_confirmed=False,
                photo_review_summary=summary,
                room_count=len(rooms),
                classifier=manifest["classifier"],
                warnings=list(floor_plan_result.get("warnings", [])),
                limitations=[],
                assets=[],
            )
            log_event(
                job_id,
                f"Awaiting photo review: {len(ready)} photo(s), {len(rooms)} room(s); "
                "no assignment is applied without user confirmation",
            )
        except PipelineCancelled:
            self._mark_cancelled(job_id)
        except Exception as exc:  # noqa: BLE001 - surface the real failure to the user
            self._mark_pipeline_error(job_id, exc)

    async def prepare_floor_plan(
        self,
        job_id: str,
        floor_plan_path: str,
        num_floors: int,
        known_width: Optional[float],
        known_length: Optional[float],
        preprocessing: Optional[Dict] = None,
        manual_outline: Optional[Dict] = None,
    ):
        """Parse the floor plan, infer room types and pause for confirmation."""
        import asyncio

        from room_types import infer_room_types

        stage_records = list((get_job(job_id) or {}).get("stage_records") or [])
        try:
            job = get_job(job_id) or {}
            self._set_stage(job_id, JobStatus.PARSING_FLOOR_PLAN)
            validation = await self.parser.validate_inputs(floor_plan_path)
            diagnostics: Dict[str, Any] = {"input_validation": validation}
            update_job(job_id, diagnostics=diagnostics)
            if validation["errors"]:
                self._fail_job(
                    job_id,
                    JobStatus.PARSING_FLOOR_PLAN,
                    "; ".join(validation["errors"]),
                    "invalid_inputs",
                    diagnostics,
                )
                return
            self._record_stage(
                job_id,
                stage_records,
                "input_validation",
                "completed",
                {
                    "floor_plan_valid": validation["floor_plan_valid"],
                    "photos_required": False,
                },
            )

            self._set_stage(job_id, JobStatus.DETECTING_ROOMS_OPENINGS)
            if manual_outline:
                log_event(job_id, "Parsing user-supplied manual room outlines")
                floor_plan_result = await self.parser.parse_manual_outline(
                    job_id,
                    floor_plan_path,
                    known_width,
                    known_length,
                    manual_outline.get("rooms") or [],
                    manual_outline.get("doorways") or [],
                    manual_outline.get("wall_thickness_px"),
                    progress_callback=self._parser_progress(job_id),
                )
            else:
                floor_plan_result = await self.parser.parse_floor_plan(
                    job_id,
                    floor_plan_path,
                    known_width,
                    known_length,
                    progress_callback=self._parser_progress(job_id),
                    preprocessing=preprocessing,
                )
            self._check_cancelled(job_id)
            diagnostics["floor_plan"] = floor_plan_result
            update_job(job_id, diagnostics=diagnostics)
            if floor_plan_result.get("status") != "completed":
                self._fail_job(
                    job_id,
                    JobStatus.DETECTING_ROOMS_OPENINGS,
                    floor_plan_result.get("error", "Floor plan parsing failed"),
                    floor_plan_result.get("error_code"),
                    diagnostics,
                )
                return
            prepared_plan_path = GEOMETRY_DIR / job_id / "floor_plan.json"
            prepared_plan_path.parent.mkdir(parents=True, exist_ok=True)
            prepared_plan_path.write_text(json.dumps(floor_plan_result, indent=2))
            self._record_stage(
                job_id,
                stage_records,
                "floor_plan_parse",
                "completed",
                {
                    "rooms": len(floor_plan_result["rooms"]),
                    "walls": len(floor_plan_result["wall_segments"]),
                    "doorways": len(floor_plan_result["doorways"]),
                    "windows": len(floor_plan_result.get("windows") or []),
                    "scale_pixels_per_meter": floor_plan_result["scale_pixels_per_meter"],
                    "scale_source": floor_plan_result["scale_source"],
                    "scale_verified": bool(floor_plan_result.get("scale_verified")),
                },
            )
            log_event(
                job_id,
                f"Floor plan parsed: {len(floor_plan_result['rooms'])} rooms, "
                f"{len(floor_plan_result['wall_segments'])} walls, "
                f"{len(floor_plan_result['doorways'])} doorway(s), "
                f"{len(floor_plan_result.get('windows') or [])} window(s)",
            )

            self._set_stage(job_id, JobStatus.VALIDATING_ROOM_LAYOUT)
            rooms = floor_plan_result["rooms"]
            inference = await asyncio.to_thread(
                infer_room_types,
                rooms,
                floor_plan_result.get("doorways") or [],
                floor_plan_result.get("windows") or [],
                None,
                floor_plan_result.get("footprint_m"),
            )
            review = {
                "schema_version": "1.0",
                "job_id": job_id,
                **inference,
                # The inferred types are a proposal: the user always confirms
                # them before furniture is placed.
                "requires_confirmation": True,
                "status": "pending",
                "confirmed_at": None,
                "assignments": [],
                "style": job.get("furnishing_style") or "modern",
                "variant": int(job.get("furnishing_variant") or 0),
            }
            save_room_type_review(job_id, review)
            self._record_stage(
                job_id,
                stage_records,
                "room_type_inference",
                "review_required",
                {
                    "rooms": len(rooms),
                    "uncertain_rooms": _room_type_summary(review)["uncertain_rooms"],
                    "source": inference.get("source"),
                },
            )
            diagnostics["room_types"] = {
                "source": inference.get("source"),
                "requires_confirmation": True,
            }
            update_job(
                job_id,
                status=JobStatus.AWAITING_ROOM_TYPES,
                current_stage=JobStatus.AWAITING_ROOM_TYPES,
                progress=STAGE_PROGRESS[JobStatus.AWAITING_ROOM_TYPES],
                failed_stage=None,
                error=None,
                error_code=None,
                diagnostics=diagnostics,
                stage_records=stage_records,
                review_required=True,
                assignments_confirmed=False,
                room_types_confirmed=False,
                room_types=[],
                room_type_review=_room_type_summary(review),
                room_count=len(rooms),
                warnings=list(floor_plan_result.get("warnings", [])),
                limitations=[],
                assets=[],
                furniture_count=0,
            )
            log_event(
                job_id,
                f"Awaiting room-type confirmation: {len(rooms)} room(s) detected; "
                "no furniture is placed before the user confirms the proposal",
            )
        except PipelineCancelled:
            self._mark_cancelled(job_id)
        except Exception as exc:  # noqa: BLE001 - surface the real failure to the user
            self._mark_pipeline_error(job_id, exc)

    async def reconstruct_furnished(
        self,
        job_id: str,
        style: Optional[str] = None,
        variant: Optional[int] = None,
    ):
        """Build and validate the furnished scene from confirmed room types."""
        import asyncio

        from furnishing import furnish_floor_plan
        from furniture_assets import STYLE_LABELS, STYLES
        from room_types import ROOM_TYPE_LABELS, apply_names, normalize_confirmed_types

        stage_records = list((get_job(job_id) or {}).get("stage_records") or [])
        try:
            job = get_job(job_id) or {}
            property_id = job.get("property_id") or job_id
            plan_path = GEOMETRY_DIR / job_id / "floor_plan.json"
            if not plan_path.is_file():
                self._fail_job(
                    job_id,
                    JobStatus.PARSING_FLOOR_PLAN,
                    "The prepared floor plan is missing; retry the job to parse it again",
                    "prepared_plan_missing",
                )
                return
            review = load_room_type_review(job_id)
            if not review:
                self._fail_job(
                    job_id,
                    JobStatus.AWAITING_ROOM_TYPES,
                    "Room types have not been reviewed for this job",
                    "room_types_required",
                )
                return
            if review.get("status") != "confirmed":
                self._fail_job(
                    job_id,
                    JobStatus.AWAITING_ROOM_TYPES,
                    "Room types have not been confirmed",
                    "room_types_required",
                )
                return
            floor_plan_result = json.loads(plan_path.read_text())
            rooms = floor_plan_result.get("rooms") or []
            try:
                room_metadata = normalize_confirmed_types(
                    {"rooms": review.get("assignments") or []},
                    rooms,
                    fallback=review.get("rooms"),
                )
            except ValueError as exc:
                self._fail_job(
                    job_id,
                    JobStatus.AWAITING_ROOM_TYPES,
                    str(exc),
                    "room_types_invalid",
                )
                return
            apply_names(room_metadata, rooms)
            for metadata in room_metadata.values():
                metadata["source"] = "user"

            style = (style or job.get("furnishing_style") or review.get("style") or "modern")
            if style not in STYLES:
                style = "modern"
            variant = int(
                variant
                if variant is not None
                else job.get("furnishing_variant") or review.get("variant") or 0
            )
            variant = max(0, min(variant, MAX_FURNISHING_VARIANT))
            update_job(
                job_id,
                room_types=_confirmed_room_types(review),
                room_types_confirmed=True,
                furnishing_style=style,
                furnishing_variant=variant,
            )
            self._record_stage(
                job_id,
                stage_records,
                "room_types",
                "confirmed",
                {"rooms": len(room_metadata), "source": "user"},
            )

            self._set_stage(job_id, JobStatus.OPTIMIZING_OPENINGS)

            from repair_engine import SceneRepairEngine

            def _plan_furnishing(
                plan, metadata, furnishing_style, furnishing_variant, allow_partial
            ):
                return furnish_floor_plan(
                    plan.get("rooms") or [],
                    metadata,
                    plan.get("doorways") or [],
                    plan.get("windows") or [],
                    furnishing_style,
                    furnishing_variant,
                    allow_partial=allow_partial,
                )

            async def _furnish(
                plan, metadata, furnishing_style, furnishing_variant, allow_partial
            ):
                return await asyncio.to_thread(
                    _plan_furnishing,
                    plan,
                    metadata,
                    furnishing_style,
                    furnishing_variant,
                    allow_partial,
                )

            async def _build_scene(plan, planned_furnishing, furnishing_style):
                return await self.geometry_builder.build_geometry(
                    job_id,
                    plan,
                    {},
                    int(job.get("num_floors") or 1),
                    (job.get("known_dimensions") or {}).get("width_meters"),
                    (job.get("known_dimensions") or {}).get("length_meters"),
                    None,
                    progress_callback=self._geometry_progress(job_id),
                    photo_manifest=None,
                    room_metadata=room_metadata,
                    use_photo_floor_textures=False,
                    furnishing=planned_furnishing,
                    style=furnishing_style,
                )

            async def _validate_scene(geometry, plan, planned_furnishing):
                self._check_cancelled(job_id)
                return await self.validator.validate_scene(
                    job_id,
                    geometry,
                    photo_manifest=None,
                    floor_plan_result=plan,
                    furnishing=planned_furnishing,
                )

            def _repair_progress(event, payload):
                if event == "repair_attempt":
                    self._set_stage(job_id, JobStatus.OPTIMIZING_OPENINGS)
                    actions = ", ".join(payload.get("actions") or [])
                    log_event(
                        job_id,
                        "Automatic repair attempt "
                        f"{payload.get('attempt')}: {actions}",
                    )
                elif event == "repair_regenerate" and payload.get("rebuild"):
                    self._set_stage(job_id, JobStatus.GENERATING_GEOMETRY)
                elif event in ("repair_validate", "repair_conservative"):
                    self._set_stage(
                        job_id, JobStatus.VALIDATING_NAVIGATION_COLLISIONS
                    )

            repair_result = await SceneRepairEngine().run(
                job_id=job_id,
                plan=floor_plan_result,
                room_metadata=room_metadata,
                style=style,
                variant=variant,
                furnish=_furnish,
                build=_build_scene,
                validate=_validate_scene,
                progress=_repair_progress,
                log=lambda message: log_event(job_id, message),
                abort=lambda: bool((get_job(job_id) or {}).get("cancel_requested")),
            )
            self._check_cancelled(job_id)

            floor_plan_result = repair_result.get("plan") or floor_plan_result
            rooms = floor_plan_result.get("rooms") or rooms
            furnishing = repair_result.get("furnishing") or {}
            house_model = repair_result.get("house_model") or {}
            repair_log = repair_result.get("repair_log") or {}
            geometry_result = repair_result.get("geometry")
            validation_result = repair_result.get("validation") or {}
            if furnishing.get("variant") is not None:
                variant = int(furnishing.get("variant"))
            validation_result["scale_verified"] = bool(
                floor_plan_result.get("scale_verified")
            )
            repair_diagnostics = self._repair_diagnostics(repair_result)

            self._record_stage(
                job_id,
                stage_records,
                "furnishing_plan",
                "completed" if furnishing.get("items") else "failed",
                {
                    "style": style,
                    "variant": variant,
                    "items": len(furnishing.get("items") or []),
                    "rooms_furnished": (furnishing.get("summary") or {}).get(
                        "rooms_furnished", 0
                    ),
                    "rooms_unspecified": (furnishing.get("summary") or {}).get(
                        "rooms_unspecified", 0
                    ),
                    "repair_status": repair_log.get("status"),
                    "repair_attempts": repair_diagnostics["attempt_count"],
                },
            )
            log_event(
                job_id,
                f"Furnishing planned: {len(furnishing.get('items') or [])} item(s), "
                f"style {STYLE_LABELS.get(style, style)}, variant {variant}",
            )

            diagnostics: Dict[str, Any] = dict(job.get("diagnostics") or {})
            diagnostics["furnishing"] = {
                "style": style,
                "variant": variant,
                "summary": furnishing.get("summary"),
                "warnings": furnishing.get("warnings"),
            }
            diagnostics["repair"] = repair_diagnostics
            diagnostics["house_model"] = {
                "version": house_model.get("version"),
                "spawn": house_model.get("spawn"),
                "rooms": len(house_model.get("rooms") or []),
                "doorways": len(house_model.get("doorways") or []),
                "walls": len(house_model.get("walls") or []),
                "validation": house_model.get("validation"),
                "ambiguities": house_model.get("ambiguities") or [],
                "warnings": house_model.get("warnings") or [],
            }
            if geometry_result:
                diagnostics["geometry"] = {
                    key: geometry_result.get(key)
                    for key in (
                        "vertices_count",
                        "faces_count",
                        "collision_walls",
                        "collision_furniture",
                        "rooms_count",
                        "room_graph_edges",
                        "furniture_items",
                        "warnings",
                        "limitations",
                    )
                }
                self._record_stage(
                    job_id,
                    stage_records,
                    "geometry_build",
                    "completed",
                    {
                        "vertices": geometry_result["vertices_count"],
                        "faces": geometry_result["faces_count"],
                        "collision_walls": geometry_result["collision_walls"],
                        "collision_furniture": geometry_result["collision_furniture"],
                        "furniture_items": geometry_result["furniture_items"],
                        "primary_asset": "scene.glb",
                        "repair_status": repair_log.get("status"),
                    },
                )
                log_event(
                    job_id,
                    f"Geometry built: {geometry_result['vertices_count']} vertices, "
                    f"{geometry_result['faces_count']} faces, "
                    f"{geometry_result['furniture_items']} furniture item(s)",
                )
            else:
                self._record_stage(
                    job_id,
                    stage_records,
                    "geometry_build",
                    "failed",
                    {"error": repair_result.get("message")},
                )
            if validation_result:
                geometry_valid = bool(validation_result.get("geometry_valid"))
                self._record_stage(
                    job_id,
                    stage_records,
                    "scene_validation",
                    "completed" if geometry_valid else "failed",
                    {
                        "geometry_valid": geometry_valid,
                        "navigation_available": bool(
                            validation_result.get("navigation_available")
                        ),
                        "checks": validation_result.get("checks", {}),
                        "issues": (validation_result.get("stats") or {}).get(
                            "issues", []
                        )[:20],
                        "repair_attempts": repair_diagnostics["attempt_count"],
                    },
                )
            else:
                self._record_stage(
                    job_id,
                    stage_records,
                    "scene_validation",
                    "failed",
                    {
                        "error": (
                            "Scene validation could not run because the scene was not "
                            "generated."
                        )
                    },
                )
            update_job(job_id, diagnostics=diagnostics)

            warning_items = (
                list(floor_plan_result.get("warnings", []))
                + list(furnishing.get("warnings", []))
                + list((geometry_result or {}).get("warnings", []))
                + list(validation_result.get("warnings", []))
            )
            if repair_diagnostics["repair_count"]:
                warning_items.append(
                    f"Automatic repair applied {repair_diagnostics['repair_count']} "
                    "correction(s) before the final validation."
                )
            for ambiguity in repair_diagnostics["normalization"]["ambiguities"][:5]:
                message = ambiguity.get("message") or ambiguity.get("detail")
                if message:
                    warning_items.append(f"Structural ambiguity: {message}")
            warnings = list(dict.fromkeys(warning_items))
            limitations = list(
                dict.fromkeys((geometry_result or {}).get("limitations", []))
            )
            room_payload = []
            label_map = dict(ROOM_TYPE_LABELS)
            review_rooms = {
                int(entry["room_id"]): entry for entry in review.get("rooms") or []
            }
            rooms_by_id = {int(room["id"]): room for room in rooms}
            furnishing_rooms = {
                int(entry["room_id"]): entry for entry in furnishing.get("rooms") or []
            }
            for room_id in sorted(room_metadata):
                metadata = room_metadata[room_id]
                review_entry = review_rooms.get(room_id, {})
                room = rooms_by_id.get(room_id, {})
                room_payload.append(
                    {
                        "room_id": room_id,
                        "name": metadata.get("name"),
                        "room_type": metadata.get("room_type"),
                        "room_type_label": label_map.get(metadata.get("room_type")),
                        "confidence": review_entry.get("confidence"),
                        "source": metadata.get("source", "user"),
                        "area_m2": room.get("area_m2"),
                        "bounds_m": room.get("bounds_m"),
                        "polygon": room.get("polygon"),
                        "furniture": [
                            item["id"]
                            for item in furnishing.get("items") or []
                            if int(item["room_id"]) == room_id
                        ],
                        "furnishing_status": (furnishing_rooms.get(room_id) or {}).get(
                            "status"
                        ),
                    }
                )
            concise_rooms = [
                {
                    "room_id": entry["room_id"],
                    "name": entry["name"],
                    "room_type": entry["room_type"],
                    "source": entry["source"],
                }
                for entry in room_payload
            ]

            if not repair_result.get("ok") or not validation_result.get("geometry_valid"):
                message = repair_result.get("message") or (
                    "Scene validation failed after automatic repair: "
                    + "; ".join(
                        validation_result.get("errors", ["unknown validation error"])
                    )
                )
                error_code = repair_result.get("error_code") or "scene_validation_failed"
                failed_stage = (
                    JobStatus.VALIDATING_NAVIGATION_COLLISIONS
                    if geometry_result and validation_result
                    else JobStatus.GENERATING_GEOMETRY
                )
                output_dir = Path(
                    (geometry_result or {}).get("output_dir") or (OUTPUT_DIR / job_id)
                )
                written = self._persist_repair_artifacts(
                    output_dir,
                    repair_result,
                    validation_result
                    or {
                        "status": "failed",
                        "geometry_valid": False,
                        "navigation_available": False,
                        "scale_verified": bool(floor_plan_result.get("scale_verified")),
                        "checks": {},
                        "errors": [message],
                        "warnings": warnings,
                        "stats": {},
                    },
                )
                repair_diagnostics["artifacts"] = written
                update_job(
                    job_id,
                    diagnostics={
                        **diagnostics,
                        "scene_validation": validation_result,
                        "repair": repair_diagnostics,
                        "furnishing": {
                            "style": style,
                            "variant": variant,
                            "summary": furnishing.get("summary"),
                        },
                    },
                    geometry_valid=False,
                    navigation_available=False,
                    assets=(geometry_result or {}).get("assets", []),
                    warnings=warnings,
                    limitations=limitations,
                    room_types=concise_rooms,
                    room_types_confirmed=True,
                    furniture_count=len(furnishing.get("items") or []),
                    furnishing_style=style,
                    furnishing_variant=variant,
                    error=message,
                    error_code=error_code,
                    failed_stage=failed_stage,
                    status=JobStatus.FAILED,
                    current_stage="failed",
                    progress=STAGE_PROGRESS.get(
                        failed_stage,
                        STAGE_PROGRESS[JobStatus.GENERATING_GEOMETRY],
                    ),
                )
                log_event(
                    job_id,
                    "Reconstruction failed after "
                    f"{repair_diagnostics['attempt_count']} automatic repair attempt(s): "
                    f"{message}",
                )
                return

            self._set_stage(job_id, JobStatus.LOADING_WALKTHROUGH)
            repair_diagnostics["artifacts"] = self._persist_repair_artifacts(
                Path(geometry_result["output_dir"]), repair_result, validation_result
            )
            collision = json.loads(
                (Path(geometry_result["output_dir"]) / "collision.json").read_text()
            )
            manifest_payload = {
                "schema_version": "3.0",
                "workflow": "floor_plan_only",
                "property_id": property_id,
                "job_id": job_id,
                "project_name": job.get("project_name"),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "format": "glb",
                "primary_asset": "scene.glb",
                "assets": geometry_result["assets"],
                "scene": {
                    "bounds": geometry_result.get("geometry", {}).get("bounds"),
                    "floor_height": geometry_result["metadata"]["floor_height"],
                    "rooms": geometry_result["rooms_count"],
                    "doors": len(floor_plan_result.get("doorways") or []),
                    "windows": len(floor_plan_result.get("windows") or []),
                    "spawn": collision["spawn"],
                    "scale": geometry_result["metadata"]["scale"],
                    "approximate": not bool(floor_plan_result.get("scale_verified")),
                    "furnishing_style": style,
                    "furniture_count": len(furnishing.get("items") or []),
                },
                "method": geometry_result["metadata"]["method"],
                "room_types": room_payload,
                "rooms": room_payload,
                "room_assignments": room_payload,
                "furnishing": {
                    "style": style,
                    "style_label": furnishing.get("style_label"),
                    "variant": variant,
                    "summary": furnishing.get("summary"),
                    "limits": furnishing.get("limits"),
                    "materials": furnishing.get("materials"),
                },
                "furniture": furnishing.get("items") or [],
                "stats": {
                    "rooms": len(room_payload),
                    "furniture_items": len(furnishing.get("items") or []),
                    "collision_items": (furnishing.get("summary") or {}).get(
                        "collision_items"
                    ),
                },
                "inferred": geometry_result["metadata"].get("inferred", []),
                "provenance": {
                    "property_id": property_id,
                    "job_id": job_id,
                    "engine": {
                        "service": "RealEstAI Reconstruction Service",
                        "version": app.version,
                        "photographs_used": False,
                        "furniture": "procedural asset library + deterministic constraints",
                    },
                    "inputs": {
                        "floor_plan": (job.get("inputs") or {}).get("floor_plan"),
                        "num_floors": job.get("num_floors"),
                        "known_dimensions": job.get("known_dimensions"),
                        "room_types_confirmed": True,
                    },
                    "stages": stage_records,
                    "outputs": geometry_result["assets"],
                    "warnings": warnings,
                    "limitations": limitations,
                },
                "validation": {
                    "geometry_valid": validation_result["geometry_valid"],
                    "navigation_available": validation_result["navigation_available"],
                    "checks": validation_result.get("checks", {}),
                    "stats": validation_result.get("stats", {}),
                },
                "repair": {
                    "status": repair_diagnostics["status"],
                    "attempts": repair_diagnostics["attempt_count"],
                    "repair_count": repair_diagnostics["repair_count"],
                    "conservative_fallback": repair_diagnostics[
                        "conservative_fallback"
                    ],
                    "normalization": repair_diagnostics["normalization"],
                    "repairs": repair_diagnostics["repairs"],
                    "unresolved": repair_diagnostics["unresolved"],
                },
                "warnings": warnings,
                "limitations": limitations,
            }
            self.write_manifest(Path(geometry_result["output_dir"]), manifest_payload)
            self._record_stage(
                job_id,
                stage_records,
                "manifest",
                "written",
                {"assets": [asset["name"] for asset in geometry_result["assets"]]},
            )
            update_job(
                job_id,
                diagnostics={
                    **diagnostics,
                    "scene_validation": validation_result,
                    "repair": repair_diagnostics,
                    "furnishing": {
                        "style": style,
                        "variant": variant,
                        "summary": furnishing.get("summary"),
                        "warnings": furnishing.get("warnings"),
                    },
                },
                geometry_valid=True,
                navigation_available=bool(validation_result["navigation_available"]),
                scale_verified=bool(floor_plan_result.get("scale_verified")),
                warnings=warnings,
                limitations=limitations,
                assets=geometry_result["assets"],
                status=JobStatus.COMPLETED,
                current_stage=JobStatus.COMPLETED,
                progress=100,
                failed_stage=None,
                error_code=None,
                error=None,
                review_required=False,
                assignments_confirmed=False,
                room_count=len(rooms),
                room_types=concise_rooms,
                room_types_confirmed=True,
                room_type_review=_room_type_summary(review),
                furnishing_style=style,
                furnishing_variant=variant,
                furniture_count=len(furnishing.get("items") or []),
                stage_records=stage_records,
            )
            log_event(
                job_id,
                "Reconstruction completed: scene.glb validated "
                f"({geometry_result['faces_count']} faces, "
                f"{geometry_result['furniture_items']} furniture item(s), navigation "
                f"{'available' if validation_result['navigation_available'] else 'partial'})",
            )

        except PipelineCancelled:
            self._mark_cancelled(job_id)
        except Exception as exc:  # noqa: BLE001 - surface the real failure to the user
            self._mark_pipeline_error(job_id, exc)

    async def reconstruct(self, job_id: str):
        """Reconstruction stage after the user has confirmed every assignment."""
        from photo_manifest import (
            apply_camera_poses,
            apply_photo_analysis,
            assignments_by_room,
            included_photo_paths,
            load_photo_manifest,
            match_summary,
            public_photo,
            save_photo_manifest,
            usable_photos,
        )
        from room_matcher import derive_room_metadata, room_shells

        stage_records = list((get_job(job_id) or {}).get("stage_records") or [])
        try:
            job = get_job(job_id) or {}
            property_id = job.get("property_id") or job_id
            inputs = job.get("inputs") or {}
            plan_path = GEOMETRY_DIR / job_id / "floor_plan.json"
            manifest = load_photo_manifest(job_id)
            if not plan_path.is_file():
                self._fail_job(
                    job_id,
                    JobStatus.PREPROCESSING,
                    "The prepared floor plan is missing; re-run photo preparation",
                    "prepared_plan_missing",
                )
                return
            if not manifest:
                self._fail_job(
                    job_id,
                    JobStatus.AWAITING_REVIEW,
                    "No photo manifest exists for this job; review photo assignments first",
                    "review_required",
                )
                return
            if (manifest.get("review") or {}).get("status") != "confirmed":
                self._fail_job(
                    job_id,
                    JobStatus.AWAITING_REVIEW,
                    "Photo assignments have not been confirmed",
                    "review_required",
                )
                return
            floor_plan_result = json.loads(plan_path.read_text())
            photo_paths = included_photo_paths(manifest)
            if not photo_paths:
                self._fail_job(
                    job_id,
                    JobStatus.ANALYZING_PHOTOS,
                    "No confirmed photographs are available for reconstruction",
                    "no_usable_photos",
                )
                return
            diagnostics: Dict[str, Any] = job.get("diagnostics") or {}

            self._set_stage(job_id, JobStatus.ANALYZING_PHOTOS)
            photo_result = await self.photo_analyzer.analyze_photos(
                job_id, photo_paths, floor_plan_result
            )
            apply_photo_analysis(manifest, photo_result)
            photo_result.pop("photos", None)  # keep job diagnostics small
            diagnostics["photos"] = photo_result
            update_job(job_id, diagnostics=diagnostics)
            self._record_stage(
                job_id,
                stage_records,
                "photo_analysis",
                "completed",
                {
                    "analysed_photos": photo_result["analyzed_photos"],
                    "texture_tiles": len(photo_result.get("textures", [])),
                    "overlap_assessment": photo_result["photogrammetry"]["assessment"],
                },
            )
            log_event(
                job_id,
                f"Photos analysed: {photo_result['analyzed_photos']} confirmed photo(s), "
                f"overlap assessment {photo_result['photogrammetry']['assessment']}",
            )

            self._set_stage(job_id, JobStatus.RECONSTRUCTING)
            photogrammetry_result = await self.photogrammetry.reconstruct(
                job_id, photo_paths, floor_plan_result
            )
            apply_camera_poses(manifest, photogrammetry_result)
            diagnostics["photogrammetry"] = {
                key: photogrammetry_result.get(key)
                for key in (
                    "status",
                    "engine",
                    "input_images",
                    "registered_images",
                    "registration_ratio",
                    "sparse_points",
                    "mean_reprojection_error_px",
                    "matched_pairs",
                    "inlier_matches",
                    "dense",
                    "warnings",
                    "errors",
                )
            }
            update_job(job_id, diagnostics=diagnostics)
            self._record_stage(
                job_id,
                stage_records,
                "photogrammetry",
                photogrammetry_result.get("status", "not_run"),
                {
                    "engine": photogrammetry_result.get("engine"),
                    "registered_images": photogrammetry_result.get("registered_images", 0),
                    "input_images": photogrammetry_result.get("input_images", 0),
                    "sparse_points": photogrammetry_result.get("sparse_points", 0),
                    "mean_reprojection_error_px": photogrammetry_result.get(
                        "mean_reprojection_error_px"
                    ),
                    "dense_status": (photogrammetry_result.get("dense") or {}).get("status"),
                    "duration_seconds": photogrammetry_result.get("duration_seconds"),
                },
            )
            log_event(
                job_id,
                "Photogrammetry: "
                f"{photogrammetry_result.get('status')} "
                f"({photogrammetry_result.get('registered_images', 0)}/"
                f"{photogrammetry_result.get('input_images', 0)} images registered, "
                f"dense {(photogrammetry_result.get('dense') or {}).get('status')})",
            )
            self._check_cancelled(job_id)

            self._set_stage(job_id, JobStatus.BUILDING_GEOMETRY)
            rooms = room_shells(floor_plan_result)
            room_metadata = derive_room_metadata(rooms, manifest)
            geometry_result = await self.geometry_builder.build_geometry(
                job_id,
                floor_plan_result,
                photo_result,
                int(job.get("num_floors") or 1),
                (job.get("known_dimensions") or {}).get("width_meters"),
                (job.get("known_dimensions") or {}).get("length_meters"),
                photogrammetry_result,
                progress_callback=lambda stage: self._set_stage(job_id, stage),
                photo_manifest=manifest,
                room_metadata=room_metadata,
                use_photo_floor_textures=bool(job.get("use_photo_floor_textures")),
            )
            diagnostics["geometry"] = {
                key: geometry_result.get(key)
                for key in (
                    "vertices_count",
                    "faces_count",
                    "collision_walls",
                    "rooms_count",
                    "room_graph_edges",
                    "textures",
                    "photogrammetry",
                    "warnings",
                    "limitations",
                )
            }
            update_job(job_id, diagnostics=diagnostics)
            self._record_stage(
                job_id,
                stage_records,
                "geometry_build",
                "completed",
                {
                    "vertices": geometry_result["vertices_count"],
                    "faces": geometry_result["faces_count"],
                    "collision_walls": geometry_result["collision_walls"],
                    "texture_sets": (geometry_result.get("textures") or {}).get("count", 0),
                    "primary_asset": "scene.glb",
                },
            )
            log_event(
                job_id,
                f"Geometry built: {geometry_result['vertices_count']} vertices, "
                f"{geometry_result['faces_count']} faces, "
                f"{(geometry_result.get('textures') or {}).get('count', 0)} room-scoped photo textures",
            )

            self._set_stage(job_id, JobStatus.VALIDATING_ASSETS)
            validation_result = await self.validator.validate_scene(
                job_id,
                geometry_result,
                photo_manifest=manifest,
                floor_plan_result=floor_plan_result,
            )
            validation_result["scale_verified"] = bool(floor_plan_result.get("scale_verified"))
            self._check_cancelled(job_id)

            warnings = list(
                dict.fromkeys(
                    floor_plan_result.get("warnings", [])
                    + photo_result.get("warnings", [])
                    + geometry_result.get("warnings", [])
                    + validation_result.get("warnings", [])
                    + [
                        f"{photo['original'].get('original_filename')}: "
                        f"{(photo.get('match') or {})['conflict']['message']}"
                        for photo in usable_photos(manifest)
                        if (photo.get("match") or {}).get("conflict")
                    ]
                )
            )
            limitations = list(dict.fromkeys(geometry_result.get("limitations", [])))
            self._record_stage(
                job_id,
                stage_records,
                "scene_validation",
                "completed" if validation_result["geometry_valid"] else "failed",
                {
                    "geometry_valid": validation_result["geometry_valid"],
                    "navigation_available": validation_result["navigation_available"],
                    "checks": validation_result.get("checks", {}),
                },
            )
            if not validation_result["geometry_valid"]:
                message = "Scene validation failed: " + "; ".join(
                    validation_result.get("errors", ["unknown validation error"])
                )
                update_job(
                    job_id,
                    diagnostics={**diagnostics, "scene_validation": validation_result},
                    geometry_valid=False,
                    navigation_available=False,
                    warnings=warnings,
                    limitations=limitations,
                    error=message,
                    error_code="scene_validation_failed",
                    failed_stage=JobStatus.VALIDATING_ASSETS,
                    status=JobStatus.FAILED,
                    current_stage="failed",
                    progress=STAGE_PROGRESS[JobStatus.VALIDATING_ASSETS],
                )
                log_event(job_id, message)
                return

            public_photos = [
                public_photo(job_id, photo) for photo in usable_photos(manifest)
            ]
            assignments = {
                str(room_id): [photo["photo_id"] for photo in photos]
                for room_id, photos in assignments_by_room(manifest).items()
            }
            manifest_payload = {
                "schema_version": "2.1",
                "property_id": property_id,
                "job_id": job_id,
                "project_name": job.get("project_name"),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "format": "glb",
                "primary_asset": "scene.glb",
                "assets": geometry_result["assets"],
                "scene": {
                    "bounds": geometry_result.get("geometry", {}).get("bounds"),
                    "floor_height": geometry_result["metadata"]["floor_height"],
                    "rooms": geometry_result["rooms_count"],
                    "doors": len(floor_plan_result.get("doorways", [])),
                    "spawn": json.loads(
                        (Path(geometry_result["output_dir"]) / "collision.json").read_text()
                    )["spawn"],
                    "scale": geometry_result["metadata"]["scale"],
                    "approximate": not bool(floor_plan_result.get("scale_verified")),
                },
                "method": geometry_result["metadata"]["method"],
                "photogrammetry": geometry_result.get("photogrammetry"),
                "textures": geometry_result.get("textures"),
                "room_assignments": [
                    {
                        **metadata,
                        "photos": assignments.get(str(metadata["room_id"]), []),
                    }
                    for metadata in room_metadata.values()
                ],
                "photo_manifest": {
                    "schema_version": manifest.get("schema_version"),
                    "classifier": manifest.get("classifier"),
                    "review": manifest.get("review"),
                    "summary": match_summary(manifest),
                    "photos": public_photos,
                },
                "inferred": geometry_result["metadata"].get("inferred", []),
                "provenance": {
                    "property_id": property_id,
                    "job_id": job_id,
                    "engine": {
                        "service": "RealEstAI Reconstruction Service",
                        "version": app.version,
                        "photogrammetry": photogrammetry_result.get("engine"),
                        "room_classifier": manifest.get("classifier", {}).get("model"),
                    },
                    "inputs": inputs,
                    "stages": stage_records,
                    "outputs": geometry_result["assets"],
                    "warnings": warnings,
                    "limitations": limitations,
                },
                "validation": {
                    "geometry_valid": validation_result["geometry_valid"],
                    "navigation_available": validation_result["navigation_available"],
                    "checks": validation_result.get("checks", {}),
                    "stats": validation_result.get("stats", {}),
                },
                "warnings": warnings,
                "limitations": limitations,
            }
            self.write_manifest(Path(geometry_result["output_dir"]), manifest_payload)
            self._record_stage(
                job_id,
                stage_records,
                "manifest",
                "written",
                {"assets": [asset["name"] for asset in geometry_result["assets"]]},
            )

            update_job(
                job_id,
                diagnostics={**diagnostics, "scene_validation": validation_result},
                geometry_valid=True,
                navigation_available=bool(validation_result["navigation_available"]),
                scale_verified=bool(floor_plan_result.get("scale_verified")),
                warnings=warnings,
                limitations=limitations,
                assets=geometry_result["assets"],
                status=JobStatus.COMPLETED,
                current_stage=JobStatus.COMPLETED,
                progress=100,
                failed_stage=None,
                error_code=None,
                error=None,
                review_required=False,
                assignments_confirmed=True,
                photo_review_summary=match_summary(manifest),
                room_count=len(rooms),
                stage_records=stage_records,
            )
            log_event(
                job_id,
                "Reconstruction completed: scene.glb validated "
                f"({geometry_result['faces_count']} faces, navigation "
                f"{'available' if validation_result['navigation_available'] else 'partial'})",
            )

        except PipelineCancelled:
            self._mark_cancelled(job_id)
        except Exception as exc:  # noqa: BLE001 - surface the real failure to the user
            self._mark_pipeline_error(job_id, exc)

    def _mark_cancelled(self, job_id: str):
        job = get_job(job_id) or {}
        update_job(
            job_id,
            status=JobStatus.CANCELLED,
            current_stage="cancelled",
            progress=float(job.get("progress") or 0.0),
            error="Reconstruction cancelled by user",
            error_code="cancelled",
            failed_stage=None,
        )
        log_event(job_id, "Reconstruction cancelled")

    def _mark_pipeline_error(self, job_id: str, exc: Exception):
        import traceback

        detail = f"{exc}"
        log_event(job_id, f"Reconstruction failed: {detail}")
        log_event(job_id, traceback.format_exc())
        job = get_job(job_id) or {}
        failed_stage = job.get("current_stage")
        if failed_stage in (None, "failed", "cancelled", JobStatus.COMPLETED):
            failed_stage = JobStatus.PARSING_FLOOR_PLAN
        progress = float(job.get("progress") or 0.0)
        if failed_stage in STAGE_PROGRESS:
            progress = min(progress, STAGE_PROGRESS[failed_stage])
        update_job(
            job_id,
            status=JobStatus.FAILED,
            current_stage="failed",
            failed_stage=failed_stage,
            error_code="pipeline_error",
            error=detail,
            progress=progress,
            diagnostics={**(job.get("diagnostics") or {}), "error": detail},
        )


_pipeline: Optional[ReconstructionPipeline] = None


def get_pipeline() -> ReconstructionPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = ReconstructionPipeline()
    return _pipeline


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------
@app.get("/")
async def root():
    return {
        "service": "RealEstAI Reconstruction Service",
        "version": app.version,
        "status": "operational",
        "jobs": len(load_jobs()),
    }


@app.get("/health")
async def health_check():
    import cv2
    import numpy
    import trimesh

    try:
        import pdf2image

        pdf2image_version = getattr(pdf2image, "__version__", "installed")
    except ImportError:
        pdf2image_version = None
    try:
        import mapbox_earcut

        earcut_version = getattr(mapbox_earcut, "__version__", "installed")
    except ImportError:
        earcut_version = None
    try:
        from photogrammetry import probe_pycolmap

        colmap = probe_pycolmap()
        pycolmap_version = colmap.get("version")
        colmap_cuda = bool(colmap.get("cuda"))
    except ImportError:
        pycolmap_version = None
        colmap_cuda = False
    try:
        import torch
        import transformers

        classifier_available = True
        classifier_versions = {
            "torch": torch.__version__,
            "transformers": transformers.__version__,
        }
    except ImportError:
        classifier_available = False
        classifier_versions = {}
    try:
        from furniture_assets import ASSETS, STYLES

        furnishing = {
            "asset_library_size": len(ASSETS),
            "styles": list(STYLES),
            "requires_external_vision_api": False,
        }
    except ImportError:
        furnishing = {}

    poppler = shutil.which("pdftoppm") is not None
    return {
        "status": "healthy",
        "dependencies": {
            "opencv": cv2.__version__,
            "numpy": numpy.__version__,
            "trimesh": trimesh.__version__,
            "mapbox_earcut": earcut_version,
            "pdf2image": pdf2image_version,
            "poppler": poppler,
            "pdf_floor_plans": bool(pdf2image_version and poppler),
            "pycolmap": pycolmap_version,
            "photogrammetry_available": pycolmap_version is not None,
            "photogrammetry_cuda": colmap_cuda,
            "dense_mvs_available": colmap_cuda,
            "room_classifier_available": classifier_available,
            "room_classifier": classifier_versions,
            "furnishing": furnishing,
        },
        "workspace": {
            "input_dir": str(INPUT_DIR),
            "geometry_dir": str(GEOMETRY_DIR),
            "output_dir": str(OUTPUT_DIR),
            "logs_dir": str(LOGS_DIR),
        },
    }


def _safe_stem(name: str) -> str:
    stem = Path(name or "photo").stem
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")[:60]
    return cleaned or "photo"


def _review_manifest(job_id: str):
    from photo_manifest import load_photo_manifest

    manifest = load_photo_manifest(job_id)
    if manifest is None:
        raise HTTPException(
            status_code=409,
            detail="Photo manifest is not available for this job yet",
        )
    return manifest


def _review_floor_plan(job_id: str) -> Dict:
    path = GEOMETRY_DIR / job_id / "floor_plan.json"
    if not path.is_file():
        raise HTTPException(
            status_code=409,
            detail="The floor plan has not been prepared for this job yet",
        )
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=500, detail=f"Prepared floor plan is malformed: {exc}"
        ) from exc


def _review_payload(job: Dict, manifest: Dict, floor_plan_result: Dict) -> Dict:
    from room_matcher import build_review_payload

    return build_review_payload(job, floor_plan_result, manifest)


async def _refresh_manifest_classification(job_id: str, manifest: Dict, photos: List[Dict]):
    import asyncio

    from photo_manifest import ROOM_TYPES, save_photo_manifest
    from room_classifier import RoomClassifier

    if not photos:
        return
    paths = [
        str(INPUT_DIR / job_id / photo["original"]["stored_filename"])
        for photo in photos
    ]
    results = await asyncio.to_thread(RoomClassifier().classify_paths, paths)
    classified_at = datetime.now(timezone.utc).isoformat()
    for photo, result in zip(photos, results):
        photo["classification"] = {**result, "classified_at": classified_at}
    ready = [
        photo
        for photo in manifest.get("photos", [])
        if photo.get("processing", {}).get("status") == "ready"
    ]
    statuses = [(photo.get("classification") or {}).get("status") for photo in ready]
    if any(status in {"classified", "ambiguous"} for status in statuses):
        classifier_status = (
            "completed"
            if all(status in {"classified", "ambiguous"} for status in statuses)
            else "partial"
        )
    else:
        classifier_status = "unavailable"
    models = sorted(
        {
            (photo.get("classification") or {}).get("model")
            for photo in ready
            if (photo.get("classification") or {}).get("model")
        }
    )
    manifest["classifier"] = {
        "status": classifier_status,
        "model": models[0] if models else None,
        "labels": list(ROOM_TYPES),
        "classified_at": classified_at,
        "error": next(
            (
                (photo.get("classification") or {}).get("error")
                for photo in ready
                if (photo.get("classification") or {}).get("error")
            ),
            None,
        ),
    }
    save_photo_manifest(manifest)


def _write_review_upload(job_id: str, upload: UploadFile, prefix: str) -> Dict:
    ext = Path(upload.filename or "photo").suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg"}:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported photo format '{ext}'. Allowed: PNG, JPG, JPEG",
        )
    original_name = upload.filename or f"{prefix}{ext}"
    target = INPUT_DIR / job_id / f"{prefix}_{_safe_stem(original_name)}{ext}"
    size = 0
    digest = hashlib.sha256()
    with open(target, "wb") as handle:
        for chunk in iter(lambda: upload.file.read(1024 * 1024), b""):
            size += len(chunk)
            if size > MAX_PHOTO_BYTES:
                target.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=400,
                    detail=f"Photo {original_name} is larger than "
                    f"{MAX_PHOTO_BYTES // (1024 * 1024)} MB",
                )
            digest.update(chunk)
            handle.write(chunk)
    if size == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Photo {original_name} is empty")
    return {
        "path": str(target),
        "stored_filename": target.name,
        "original_filename": original_name,
        "size_bytes": size,
        "sha256": digest.hexdigest(),
    }


@app.post("/reconstructions", response_model=JobResponse, status_code=201)
async def create_reconstruction(
    background_tasks: BackgroundTasks,
    project_name: str = Form(...),
    floor_plan: UploadFile = File(...),
    num_floors: int = Form(1),
    known_width: Optional[float] = Form(None),
    known_length: Optional[float] = Form(None),
    style: str = Form("modern"),
    property_id: Optional[str] = Form(None),
    photos: Optional[List[UploadFile]] = File(None),
):
    project_name = project_name.strip()
    if not project_name:
        raise HTTPException(status_code=400, detail="Project name is required")
    property_id = (property_id or "").strip()
    if not property_id:
        property_id = f"prop-{uuid.uuid4().hex[:12]}"
    if len(property_id) > 120:
        raise HTTPException(status_code=400, detail="Property ID must be 120 characters or fewer")
    if num_floors < 1 or num_floors > 5:
        raise HTTPException(status_code=400, detail="Number of floors must be between 1 and 5")
    if known_width is not None and not (2 <= known_width <= 100):
        raise HTTPException(status_code=400, detail="Known width must be between 2 and 100 metres")
    if known_length is not None and not (2 <= known_length <= 100):
        raise HTTPException(status_code=400, detail="Known length must be between 2 and 100 metres")
    style = _validated_style(style)
    ignored_photos = len(photos or [])

    allowed_plan = {".png", ".jpg", ".jpeg", ".pdf"}
    plan_ext = Path(floor_plan.filename or "floor_plan").suffix.lower()
    if plan_ext not in allowed_plan:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported floor plan format '{plan_ext}'. Allowed: PNG, JPG, JPEG, PDF",
        )

    job_id = str(uuid.uuid4())
    job_dir = INPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    floor_plan_path = job_dir / f"floor_plan{plan_ext}"
    try:
        saved = 0
        plan_digest = hashlib.sha256()
        with open(floor_plan_path, "wb") as handle:
            for chunk in iter(lambda: floor_plan.file.read(1024 * 1024), b""):
                saved += len(chunk)
                if saved > MAX_FLOOR_PLAN_BYTES:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Floor plan too large. Maximum {MAX_FLOOR_PLAN_BYTES // (1024 * 1024)} MB",
                    )
                plan_digest.update(chunk)
                handle.write(chunk)
        if floor_plan_path.stat().st_size == 0:
            raise HTTPException(status_code=400, detail="Floor plan file is empty")
    except HTTPException:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Failed to store uploads: {exc}") from exc

    known_dimensions = {}
    if known_width:
        known_dimensions["width_meters"] = known_width
    if known_length:
        known_dimensions["length_meters"] = known_length
    inputs = {
        "property_id": property_id,
        "floor_plan": {
            "original_filename": floor_plan.filename,
            "stored_filename": floor_plan_path.name,
            "size_bytes": floor_plan_path.stat().st_size,
            "sha256": plan_digest.hexdigest(),
        },
        "photos": [],
        "photos_ignored": ignored_photos,
        "num_floors": num_floors,
        "known_dimensions": known_dimensions,
    }

    now = datetime.now(timezone.utc).isoformat()
    job = {
        "id": job_id,
        "property_id": property_id,
        "status": JobStatus.QUEUED,
        "current_stage": JobStatus.QUEUED,
        "progress": 0.0,
        "created_at": now,
        "updated_at": now,
        "error": None,
        "project_name": project_name,
        "floor_plan_filename": floor_plan.filename,
        "photo_count": 0,
        "num_floors": num_floors,
        "known_dimensions": known_dimensions,
        "inputs": inputs,
        "stage_records": [],
        "diagnostics": {},
        "assets": [],
        "warnings": [],
        "limitations": [],
        "retry_count": 0,
        "cancel_requested": False,
        "floor_plan_path": str(floor_plan_path),
        "photo_paths": [],
        "review_required": True,
        "assignments_confirmed": False,
        "photo_review_summary": None,
        "classifier": None,
        "use_photo_floor_textures": False,
        "room_types": [],
        "room_types_confirmed": False,
        "room_type_review": None,
        "furnishing_style": style,
        "furnishing_variant": 0,
        "furniture_count": 0,
    }
    with _JOBS_LOCK:
        jobs = _read_jobs_unlocked()
        jobs[job_id] = job
        _write_jobs_unlocked(jobs)
    log_event(
        job_id,
        f"Job created for '{project_name}' (floor-plan-only, style {style})"
        + (
            f"; {ignored_photos} photo(s) were supplied but are not required and "
            "were not stored"
            if ignored_photos
            else ""
        ),
    )

    background_tasks.add_task(
        get_pipeline().prepare_floor_plan,
        job_id,
        str(floor_plan_path),
        num_floors,
        known_width,
        known_length,
    )
    return _public_job(job)


@app.get("/reconstructions", response_model=List[JobResponse])
async def list_reconstructions(status: Optional[str] = None, limit: int = 50):
    jobs = sorted(load_jobs().values(), key=lambda item: item.get("created_at", ""), reverse=True)
    if status:
        jobs = [job for job in jobs if job.get("status") == status]
    summaries = []
    for job in jobs[: max(1, min(limit, 200))]:
        payload = _public_job(job)
        payload["diagnostics"] = None
        payload["assets"] = None
        summaries.append(payload)
    return summaries


@app.get("/reconstructions/{job_id}", response_model=JobResponse)
async def get_reconstruction_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _public_job(job)


@app.post("/reconstructions/{job_id}/cancel", response_model=JobResponse)
async def cancel_reconstruction(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409, detail=f"Job already {job.get('status')} and cannot be cancelled"
        )
    update_job(job_id, cancel_requested=True)
    if job.get("status") in {
        JobStatus.AWAITING_ROOM_TYPES,
        JobStatus.AWAITING_REVIEW,
    }:
        update_job(
            job_id,
            status=JobStatus.CANCELLED,
            current_stage="cancelled",
            error="Reconstruction cancelled by user",
            error_code="cancelled",
        )
    log_event(job_id, "Cancellation requested")
    return _public_job(get_job(job_id) or job)


@app.post("/reconstructions/{job_id}/retry", response_model=JobResponse)
async def retry_reconstruction(
    job_id: str,
    background_tasks: BackgroundTasks,
    request: Optional[RetryRequest] = None,
):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    retryable = job.get("status") in {"failed", "cancelled"} or (
        job.get("status") == JobStatus.COMPLETED
        and job.get("navigation_available") is False
    )
    if not retryable:
        raise HTTPException(
            status_code=409,
            detail="Only failed, cancelled or partially navigable jobs can be retried",
        )
    preprocessing = request.preprocessing if request else None
    if preprocessing:
        unknown = set(preprocessing) - {"wall_polarity", "sealing"}
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown preprocessing option(s): {', '.join(sorted(unknown))}",
            )
        if preprocessing.get("wall_polarity", "auto") not in {"auto", "dark", "light"}:
            raise HTTPException(
                status_code=400,
                detail="wall_polarity must be one of auto, dark, light",
            )
        if preprocessing.get("sealing", "auto") not in {"auto", "minimal", "aggressive"}:
            raise HTTPException(
                status_code=400,
                detail="sealing must be one of auto, minimal, aggressive",
            )
    floor_plan_path = job.get("floor_plan_path")
    if not floor_plan_path or not Path(floor_plan_path).exists():
        raise HTTPException(
            status_code=409,
            detail="The original floor plan is no longer available; create a new reconstruction",
        )
    from photo_manifest import load_photo_manifest

    photo_manifest = load_photo_manifest(job_id)
    prepared_plan = (GEOMETRY_DIR / job_id / "floor_plan.json").is_file()
    review = load_room_type_review(job_id)
    resume_furnished = bool(
        review
        and review.get("status") == "confirmed"
        and prepared_plan
        and not preprocessing
    )
    legacy_resume = bool(
        not review
        and job.get("assignments_confirmed")
        and photo_manifest
        and (photo_manifest.get("review") or {}).get("status") == "confirmed"
        and prepared_plan
        and not preprocessing
        and all(Path(path).exists() for path in (job.get("photo_paths") or []))
    )
    is_legacy = bool(photo_manifest) or bool(job.get("photo_paths"))
    update_job(
        job_id,
        status=JobStatus.QUEUED,
        current_stage=JobStatus.QUEUED,
        progress=0.0,
        error=None,
        error_code=None,
        failed_stage=None,
        diagnostics={},
        assets=[],
        warnings=[],
        limitations=[],
        cancel_requested=False,
        retry_preprocessing=preprocessing or {},
        retry_count=int(job.get("retry_count") or 0) + 1,
        review_required=not (resume_furnished or legacy_resume),
        assignments_confirmed=legacy_resume,
        room_types_confirmed=bool(resume_furnished),
        furniture_count=0 if not resume_furnished else job.get("furniture_count", 0),
    )
    log_event(
        job_id,
        "Retry requested (furnished reconstruction)"
        if resume_furnished
        else "Retry requested (legacy reconstruction)"
        if legacy_resume
        else "Retry requested (re-parse floor plan)",
    )
    if resume_furnished:
        background_tasks.add_task(
            get_pipeline().reconstruct_furnished,
            job_id,
            job.get("furnishing_style"),
            job.get("furnishing_variant"),
        )
    elif legacy_resume:
        background_tasks.add_task(get_pipeline().reconstruct, job_id)
    elif is_legacy:
        background_tasks.add_task(
            get_pipeline().run,
            job_id,
            floor_plan_path,
            job.get("photo_paths") or [],
            int(job.get("num_floors") or 1),
            (job.get("known_dimensions") or {}).get("width_meters"),
            (job.get("known_dimensions") or {}).get("length_meters"),
            preprocessing,
            job.get("manual_outline"),
        )
    else:
        background_tasks.add_task(
            get_pipeline().prepare_floor_plan,
            job_id,
            floor_plan_path,
            int(job.get("num_floors") or 1),
            (job.get("known_dimensions") or {}).get("width_meters"),
            (job.get("known_dimensions") or {}).get("length_meters"),
            preprocessing,
            job.get("manual_outline"),
        )
    return _public_job(get_job(job_id) or job)


@app.post("/reconstructions/{job_id}/manual-plan", response_model=JobResponse)
async def submit_manual_plan(
    job_id: str,
    request: ManualPlanRequest,
    background_tasks: BackgroundTasks,
):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    correctable = job.get("status") in {"failed", "cancelled"} or (
        job.get("status") == JobStatus.COMPLETED
        and job.get("navigation_available") is False
    )
    if not correctable:
        raise HTTPException(
            status_code=409,
            detail="Manual correction is only available for failed, cancelled or "
            "partially navigable jobs",
        )
    floor_plan_path = job.get("floor_plan_path")
    if not floor_plan_path or not Path(floor_plan_path).exists():
        raise HTTPException(
            status_code=409,
            detail="The original floor plan is no longer available; create a new reconstruction",
        )
    if not request.rooms:
        raise HTTPException(status_code=400, detail="At least one room outline is required")
    if len(request.rooms) > 40:
        raise HTTPException(status_code=400, detail="At most 40 room outlines are allowed")
    doorway_payload = []
    for doorway in request.doorways:
        if doorway.orientation not in {"horizontal", "vertical"}:
            raise HTTPException(
                status_code=400,
                detail="Doorway orientation must be horizontal or vertical",
            )
        if len(doorway.center_px) != 2:
            raise HTTPException(
                status_code=400,
                detail="Doorway center_px must contain two coordinates",
            )
        doorway_payload.append(
            {
                "orientation": doorway.orientation,
                "center_px": [float(value) for value in doorway.center_px],
                "width_px": doorway.width_px,
            }
        )
    manual_outline = {
        "rooms": [
            {"polygon_px": [[float(x), float(y)] for x, y in room.polygon_px]}
            for room in request.rooms
        ],
        "doorways": doorway_payload,
        "wall_thickness_px": request.wall_thickness_px,
    }
    update_job(
        job_id,
        status=JobStatus.QUEUED,
        current_stage=JobStatus.QUEUED,
        progress=0.0,
        error=None,
        error_code=None,
        failed_stage=None,
        diagnostics={},
        assets=[],
        warnings=[],
        limitations=[],
        cancel_requested=False,
        manual_outline=manual_outline,
        retry_preprocessing={},
        retry_count=int(job.get("retry_count") or 0) + 1,
        review_required=True,
        assignments_confirmed=False,
        photo_review_summary=None,
        room_types=[],
        room_types_confirmed=False,
        room_type_review=None,
        furniture_count=0,
    )
    log_event(job_id, "Manual room-outline correction submitted")
    background_tasks.add_task(
        get_pipeline().prepare_floor_plan,
        job_id,
        floor_plan_path,
        int(job.get("num_floors") or 1),
        (job.get("known_dimensions") or {}).get("width_meters"),
        (job.get("known_dimensions") or {}).get("length_meters"),
        None,
        manual_outline,
    )
    return _public_job(get_job(job_id) or job)


@app.get("/reconstructions/{job_id}/room-types")
async def get_room_types(job_id: str):
    from room_types import ROOM_TYPE_LABELS, ROOM_TYPES

    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    review = load_room_type_review(job_id)
    if not review:
        raise HTTPException(
            status_code=409,
            detail="Room types are not available for this job yet",
        )
    floor_plan_result = _review_floor_plan(job_id)
    rooms_by_id = {
        int(room["id"]): room for room in floor_plan_result.get("rooms") or []
    }
    rooms = []
    for entry in review.get("rooms") or []:
        room_id = int(entry["room_id"])
        room = rooms_by_id.get(room_id) or {}
        rooms.append(
            {
                **entry,
                "area_m2": room.get("area_m2"),
                "bounds_m": room.get("bounds_m"),
                "polygon_m": room.get("polygon_m") or room.get("polygon"),
                "bounds_px": room.get("bounds_px"),
                "center_px": room.get("center_px"),
                "polygon_px": room.get("polygon_px"),
            }
        )
    labels = review.get("room_type_labels") or ROOM_TYPE_LABELS
    return {
        "job_id": job_id,
        "status": review.get("status"),
        "job_status": job.get("status"),
        "confirmed_at": review.get("confirmed_at"),
        "requires_confirmation": bool(review.get("requires_confirmation")),
        "source": review.get("source"),
        "notes": review.get("notes"),
        "footprint_m": floor_plan_result.get("footprint_m") or review.get("footprint_m"),
        "footprint_px": floor_plan_result.get("footprint_px"),
        "image_dimensions": floor_plan_result.get("image_dimensions"),
        "scale_pixels_per_meter": floor_plan_result.get("scale_pixels_per_meter"),
        "rooms": rooms,
        "assignments": review.get("assignments") or [],
        "labels": [
            {"value": room_type, "label": labels.get(room_type, room_type)}
            for room_type in ROOM_TYPES
        ],
        "style": review.get("style") or job.get("furnishing_style"),
        "variant": review.get("variant") or job.get("furnishing_variant") or 0,
        "floor_plan_url": f"/reconstructions/{job_id}/floor-plan",
        "normalized_floor_plan_url": f"/reconstructions/{job_id}/diagnostics/floor_plan_normalized.png",
    }


def _queue_furnished_rebuild(
    job_id: str,
    background_tasks: BackgroundTasks,
    style: str,
    variant: int,
):
    update_job(
        job_id,
        status=JobStatus.QUEUED,
        current_stage=JobStatus.QUEUED,
        progress=0.0,
        error=None,
        error_code=None,
        failed_stage=None,
        assets=[],
        warnings=[],
        limitations=[],
        cancel_requested=False,
        review_required=False,
        room_types_confirmed=True,
        furnishing_style=style,
        furnishing_variant=variant,
        geometry_valid=None,
        navigation_available=None,
    )
    background_tasks.add_task(
        get_pipeline().reconstruct_furnished, job_id, style, variant
    )


def _review_is_editable(job: Dict) -> bool:
    return job.get("status") in {
        JobStatus.AWAITING_ROOM_TYPES,
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    }


@app.post("/reconstructions/{job_id}/room-types", response_model=JobResponse)
async def confirm_room_types(
    job_id: str,
    request: ConfirmRoomTypesRequest,
    background_tasks: BackgroundTasks,
):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not _review_is_editable(job):
        raise HTTPException(
            status_code=409,
            detail=f"Room types cannot be changed while the job is {job.get('status')}",
        )
    review = load_room_type_review(job_id)
    if not review:
        raise HTTPException(
            status_code=409,
            detail="No room-type review exists for this job; parse the floor plan first",
        )
    floor_plan_result = _review_floor_plan(job_id)
    style = _validated_style(
        request.style, job.get("furnishing_style") or review.get("style")
    )
    variant = _validated_variant(
        request.variant,
        job.get("furnishing_variant")
        if job.get("furnishing_variant") is not None
        else review.get("variant"),
    )
    try:
        confirmed, _ = _apply_room_type_confirmation(
            review,
            floor_plan_result,
            [entry.model_dump() for entry in request.rooms],
            style,
            variant,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_room_type_review(job_id, confirmed)
    job = get_job(job_id) or job
    update_job(
        job_id,
        room_types=_confirmed_room_types(confirmed),
        room_types_confirmed=True,
        room_type_review=_room_type_summary(confirmed),
        room_count=len(confirmed.get("rooms") or []),
    )
    log_event(
        job_id,
        f"Room types confirmed for {len(confirmed.get('rooms') or [])} room(s); "
        f"style {style}, variant {variant}",
    )
    _queue_furnished_rebuild(job_id, background_tasks, style, variant)
    return _public_job(get_job(job_id) or job)


@app.post("/reconstructions/{job_id}/furnishings", response_model=JobResponse)
async def regenerate_furnishings(
    job_id: str,
    request: FurnishingRequest,
    background_tasks: BackgroundTasks,
):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not _review_is_editable(job):
        raise HTTPException(
            status_code=409,
            detail=f"Furnishings cannot be changed while the job is {job.get('status')}",
        )
    review = load_room_type_review(job_id)
    if not review:
        raise HTTPException(
            status_code=409,
            detail="No room-type review exists for this job; parse the floor plan first",
        )
    floor_plan_result = _review_floor_plan(job_id)
    style = _validated_style(
        request.style, job.get("furnishing_style") or review.get("style")
    )
    current_variant = (
        job.get("furnishing_variant")
        if job.get("furnishing_variant") is not None
        else review.get("variant") or 0
    )
    if request.regenerate and request.variant is None:
        variant = (int(current_variant) + 1) % (MAX_FURNISHING_VARIANT + 1)
    else:
        variant = _validated_variant(request.variant, current_variant)
    if request.room_types:
        try:
            review, _ = _apply_room_type_confirmation(
                review,
                floor_plan_result,
                [entry.model_dump() for entry in request.room_types],
                style,
                variant,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    elif review.get("status") != "confirmed":
        raise HTTPException(
            status_code=409,
            detail="Confirm room types before regenerating furnishing",
        )
    else:
        review = {**review, "style": style, "variant": variant}
    save_room_type_review(job_id, review)
    update_job(
        job_id,
        room_types=_confirmed_room_types(review),
        room_types_confirmed=review.get("status") == "confirmed",
        room_type_review=_room_type_summary(review),
    )
    log_event(
        job_id,
        f"Furnishing regeneration requested (style {style}, variant {variant})",
    )
    _queue_furnished_rebuild(job_id, background_tasks, style, variant)
    return _public_job(get_job(job_id) or job)


@app.patch("/reconstructions/{job_id}/rooms/{room_id}", response_model=JobResponse)
async def update_room_type(
    job_id: str,
    room_id: int,
    request: RoomUpdateRequest,
    background_tasks: BackgroundTasks,
):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not _review_is_editable(job):
        raise HTTPException(
            status_code=409,
            detail=f"Room types cannot be changed while the job is {job.get('status')}",
        )
    review = load_room_type_review(job_id)
    if not review:
        raise HTTPException(
            status_code=409,
            detail="No room-type review exists for this job; parse the floor plan first",
        )
    floor_plan_result = _review_floor_plan(job_id)
    room_ids = {int(room["id"]) for room in floor_plan_result.get("rooms") or []}
    if room_id not in room_ids:
        raise HTTPException(status_code=404, detail="Room not found in this floor plan")
    selections = review.get("assignments") or [
        {
            "room_id": int(entry["room_id"]),
            "room_type": entry.get("room_type"),
            "name": entry.get("name"),
        }
        for entry in review.get("rooms") or []
    ]
    if not selections:
        raise HTTPException(
            status_code=409, detail="No room-type selection exists for this job"
        )
    selections = [
        {
            "room_id": int(entry["room_id"]),
            "room_type": entry.get("room_type"),
            "name": entry.get("name"),
        }
        for entry in selections
    ]
    matched = False
    for entry in selections:
        if int(entry["room_id"]) == room_id:
            entry["room_type"] = request.room_type
            if request.name is not None:
                entry["name"] = request.name
            matched = True
            break
    if not matched:
        raise HTTPException(status_code=404, detail="Room not found in this review")
    style = _validated_style(
        None, job.get("furnishing_style") or review.get("style")
    )
    variant = _validated_variant(
        None,
        job.get("furnishing_variant")
        if job.get("furnishing_variant") is not None
        else review.get("variant"),
    )
    try:
        confirmed, _ = _apply_room_type_confirmation(
            review, floor_plan_result, selections, style, variant
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_room_type_review(job_id, confirmed)
    update_job(
        job_id,
        room_types=_confirmed_room_types(confirmed),
        room_types_confirmed=True,
        room_type_review=_room_type_summary(confirmed),
    )
    log_event(
        job_id,
        f"Room {room_id} changed to {request.room_type}; rebuild requested",
    )
    _queue_furnished_rebuild(job_id, background_tasks, style, variant)
    return _public_job(get_job(job_id) or job)


@app.get("/reconstructions/{job_id}/manifest")
async def get_manifest(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail=f"Manifest not available. Job status: {job.get('status')}",
        )
    manifest_path = OUTPUT_DIR / job_id / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(
            status_code=409,
            detail=f"No manifest available. Job status: {job.get('status')}",
        )
    return json.loads(manifest_path.read_text())


@app.get("/reconstructions/{job_id}/scene")
async def get_scene_metadata(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail=f"Scene not available. Job status: {job.get('status')} "
            f"({job.get('progress', 0):.0f}%)",
        )
    output_dir = OUTPUT_DIR / job_id
    furniture = None
    furniture_path = output_dir / "furniture.json"
    if furniture_path.is_file():
        try:
            furniture = json.loads(furniture_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Furniture layout is malformed: {exc}",
            ) from exc
    try:
        collision = json.loads((output_dir / "collision.json").read_text())
        room_graph = json.loads((output_dir / "room_graph.json").read_text())
        metadata = json.loads((output_dir / "metadata.json").read_text())
        manifest = json.loads((output_dir / "manifest.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Scene assets are missing or malformed: {exc}",
        ) from exc
    return {
        "job_id": job_id,
        "property_id": job.get("property_id"),
        "workflow": manifest.get("workflow") or "photo_assisted",
        "model_url": f"/reconstructions/{job_id}/assets/scene.glb",
        "collision": collision,
        "room_graph": room_graph,
        "metadata": metadata,
        "manifest": manifest,
        "room_types": manifest.get("room_types") or metadata.get("room_types") or [],
        "room_assignments": manifest.get("room_assignments", []),
        "furniture": (furniture or {}).get("items") or [],
        "furnishing": manifest.get("furnishing"),
        "photo_manifest": manifest.get("photo_manifest"),
    }


@app.get("/reconstructions/{job_id}/review")
async def get_photo_review(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    manifest = _review_manifest(job_id)
    floor_plan_result = _review_floor_plan(job_id)
    return _review_payload(job, manifest, floor_plan_result)


@app.post("/reconstructions/{job_id}/confirm-assignments", response_model=JobResponse)
async def confirm_photo_assignments(
    job_id: str,
    request: ConfirmAssignmentsRequest,
    background_tasks: BackgroundTasks,
):
    from photo_manifest import match_summary, save_photo_manifest
    from room_matcher import AssignmentError, apply_assignments, room_shells

    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") != JobStatus.AWAITING_REVIEW:
        raise HTTPException(
            status_code=409,
            detail=f"Photo assignments cannot be confirmed while the job is "
            f"{job.get('status')}",
        )
    manifest = _review_manifest(job_id)
    floor_plan_result = _review_floor_plan(job_id)
    rooms = room_shells(floor_plan_result)
    try:
        apply_assignments(
            manifest,
            rooms,
            [assignment.model_dump() for assignment in request.assignments],
            request.room_types,
        )
    except AssignmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_photo_manifest(manifest)
    summary = match_summary(manifest)
    diagnostics = dict(job.get("diagnostics") or {})
    diagnostics["photo_review"] = summary
    update_job(
        job_id,
        status=JobStatus.QUEUED,
        current_stage=JobStatus.QUEUED,
        progress=STAGE_PROGRESS[JobStatus.MATCHING_PHOTOS],
        error=None,
        error_code=None,
        failed_stage=None,
        review_required=False,
        assignments_confirmed=True,
        photo_review_summary=summary,
        classifier=manifest.get("classifier"),
        diagnostics=diagnostics,
        cancel_requested=False,
    )
    log_event(
        job_id,
        f"Photo assignments confirmed by user: {summary['assigned']} assigned, "
        f"{summary['excluded']} excluded",
    )
    background_tasks.add_task(get_pipeline().reconstruct, job_id)
    return _public_job(get_job(job_id) or job)


@app.get("/reconstructions/{job_id}/photos/{photo_id}/{variant}")
async def get_reconstruction_photo(job_id: str, photo_id: str, variant: str):
    from photo_manifest import photo_by_id, photo_variant_path

    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    manifest = _review_manifest(job_id)
    photo = photo_by_id(manifest, photo_id)
    if photo is None or photo.get("job_id") != job_id:
        raise HTTPException(status_code=404, detail="Photograph not found for this job")
    try:
        path = photo_variant_path(job_id, photo, variant)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="Photo variant not found") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Photo file is missing")
    media_types = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
    return FileResponse(path, media_type=media_types.get(path.suffix.lower()))


@app.delete("/reconstructions/{job_id}/photos/{photo_id}")
async def remove_review_photo(job_id: str, photo_id: str):
    from photo_manifest import exclude_photo, match_summary

    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") != JobStatus.AWAITING_REVIEW:
        raise HTTPException(
            status_code=409,
            detail="Photographs can only be removed during assignment review",
        )
    manifest = _review_manifest(job_id)
    if exclude_photo(manifest, photo_id) is None:
        raise HTTPException(status_code=404, detail="Photograph not found for this job")
    floor_plan_result = _review_floor_plan(job_id)
    update_job(
        job_id,
        photo_review_summary=match_summary(manifest),
        assignments_confirmed=False,
        review_required=True,
    )
    log_event(job_id, f"Photograph {photo_id} excluded from the walkthrough")
    return _review_payload(get_job(job_id) or job, manifest, floor_plan_result)


@app.post("/reconstructions/{job_id}/photos")
async def add_review_photo(job_id: str, photo: UploadFile = File(...)):
    from photo_manifest import add_photo_to_manifest, match_summary

    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") != JobStatus.AWAITING_REVIEW:
        raise HTTPException(
            status_code=409,
            detail="Photographs can only be added during assignment review",
        )
    manifest = _review_manifest(job_id)
    ready = sum(
        1
        for item in manifest.get("photos", [])
        if item.get("processing", {}).get("status") == "ready"
    )
    if ready >= MAX_PHOTOS:
        raise HTTPException(
            status_code=400, detail=f"At most {MAX_PHOTOS} photographs are allowed"
        )
    upload = _write_review_upload(job_id, photo, f"review_{uuid.uuid4().hex[:8]}")
    entry = add_photo_to_manifest(
        manifest,
        upload["path"],
        upload["original_filename"],
        upload["sha256"],
    )
    if entry.get("processing", {}).get("status") != "ready":
        raise HTTPException(
            status_code=400,
            detail="The added photograph could not be decoded: "
            + "; ".join(entry.get("processing", {}).get("errors") or []),
        )
    await _refresh_manifest_classification(job_id, manifest, [entry])
    floor_plan_result = _review_floor_plan(job_id)
    update_job(
        job_id,
        photo_review_summary=match_summary(manifest),
        classifier=manifest.get("classifier"),
        assignments_confirmed=False,
        review_required=True,
    )
    return _review_payload(get_job(job_id) or job, manifest, floor_plan_result)


@app.post("/reconstructions/{job_id}/photos/{photo_id}/replace")
async def replace_review_photo(
    job_id: str, photo_id: str, photo: UploadFile = File(...)
):
    from photo_manifest import (
        match_summary,
        photo_by_id,
        replace_photo_in_manifest,
    )

    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") != JobStatus.AWAITING_REVIEW:
        raise HTTPException(
            status_code=409,
            detail="Photographs can only be replaced during assignment review",
        )
    manifest = _review_manifest(job_id)
    existing = photo_by_id(manifest, photo_id)
    if existing is None or existing.get("job_id") != job_id:
        raise HTTPException(status_code=404, detail="Photograph not found for this job")
    upload = _write_review_upload(
        job_id, photo, f"photo_{photo_id}_{uuid.uuid4().hex[:8]}"
    )
    entry = replace_photo_in_manifest(
        manifest,
        photo_id,
        upload["stored_filename"],
        upload["original_filename"],
        upload["sha256"],
    )
    if entry.get("processing", {}).get("status") != "ready":
        raise HTTPException(
            status_code=400,
            detail="The replacement photograph could not be decoded: "
            + "; ".join(entry.get("processing", {}).get("errors") or []),
        )
    await _refresh_manifest_classification(job_id, manifest, [entry])
    floor_plan_result = _review_floor_plan(job_id)
    update_job(
        job_id,
        photo_review_summary=match_summary(manifest),
        classifier=manifest.get("classifier"),
        assignments_confirmed=False,
        review_required=True,
    )
    return _review_payload(get_job(job_id) or job, manifest, floor_plan_result)


@app.get("/reconstructions/{job_id}/floor-plan")
async def get_floor_plan_preview(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    plan_path = job.get("floor_plan_path")
    if not plan_path or not Path(plan_path).is_file():
        raise HTTPException(
            status_code=409,
            detail="The original floor plan is no longer available for this job",
        )
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".pdf": "application/pdf",
    }
    suffix = Path(plan_path).suffix.lower()
    return FileResponse(plan_path, media_type=media_types.get(suffix))


@app.get("/reconstructions/{job_id}/diagnostics")
async def get_reconstruction_diagnostics(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    diagnostics_dir = GEOMETRY_DIR / job_id
    artifacts = []
    for name in (
        "floor_plan_normalized.png",
        "walls_mask.png",
        "barrier.png",
        "barrier_overlay.png",
        "walls_detected.png",
        "rooms_detected.png",
        "openings_detected.png",
        "segmentation_attempts.png",
        "segmentation.json",
        "parse_failure.json",
    ):
        path = diagnostics_dir / name
        if path.is_file():
            artifacts.append(
                {
                    "name": name,
                    "url": f"/reconstructions/{job_id}/diagnostics/{name}",
                    "type": path.suffix.lstrip("."),
                    "size_bytes": path.stat().st_size,
                }
            )
    failure_report = None
    parse_failure_path = diagnostics_dir / "parse_failure.json"
    if parse_failure_path.is_file():
        try:
            failure_report = json.loads(parse_failure_path.read_text())
        except (OSError, json.JSONDecodeError):
            failure_report = None
    output_dir = OUTPUT_DIR / job_id
    for name in ("house_model.json", "repair_log.json", "validation_report.json"):
        path = output_dir / name
        if not path.is_file():
            continue
        artifacts.append(
            {
                "name": name,
                "url": f"/reconstructions/{job_id}/assets/{name}",
                "type": path.suffix.lstrip("."),
                "size_bytes": path.stat().st_size,
            }
        )
    repair_log = None
    repair_log_path = output_dir / "repair_log.json"
    if repair_log_path.is_file():
        try:
            repair_log = json.loads(repair_log_path.read_text())
        except (OSError, json.JSONDecodeError):
            repair_log = None
    floor_plan = (job.get("diagnostics") or {}).get("floor_plan") or {}
    correctable = job.get("status") in {"failed", "cancelled"} or (
        job.get("status") == JobStatus.COMPLETED
        and job.get("navigation_available") is False
    )
    return {
        "job_id": job_id,
        "status": job.get("status"),
        "error": job.get("error"),
        "error_code": job.get("error_code"),
        "failed_stage": job.get("failed_stage"),
        "floor_plan_url": f"/reconstructions/{job_id}/floor-plan",
        "preprocessing": {
            "used": floor_plan.get("preprocessing") or job.get("retry_preprocessing") or {},
            "options": [
                {
                    "wall_polarity": "auto",
                    "sealing": "auto",
                    "label": "Automatic (re-run with default analysis)",
                },
                {
                    "wall_polarity": "dark",
                    "sealing": "auto",
                    "label": "Dark wall lines on a light background",
                },
                {
                    "wall_polarity": "light",
                    "sealing": "auto",
                    "label": "Light wall lines on a dark background",
                },
                {
                    "wall_polarity": "auto",
                    "sealing": "aggressive",
                    "label": "Stronger gap closing (more enclosed)",
                },
                {
                    "wall_polarity": "auto",
                    "sealing": "minimal",
                    "label": "Minimal gap closing (preserve wide openings)",
                },
            ],
        },
        "parser": {
            key: floor_plan.get(key)
            for key in (
                "status",
                "error",
                "error_code",
                "warnings",
                "attempts",
                "wall_pixel_ratio",
                "segmentation",
                "mask",
            )
            if key in floor_plan
        },
        "failure_report": failure_report,
        "room_type_review": job.get("room_type_review")
        or _room_type_summary(load_room_type_review(job_id)),
        "room_types_confirmed": bool(job.get("room_types_confirmed")),
        "furnishing_style": job.get("furnishing_style"),
        "furniture_count": job.get("furniture_count"),
        "photo_review": (job.get("diagnostics") or {}).get("photo_review")
        or job.get("photo_review_summary"),
        "repair": (job.get("diagnostics") or {}).get("repair") or repair_log,
        "house_model": (job.get("diagnostics") or {}).get("house_model"),
        "artifacts": artifacts,
        "correction_supported": True,
        "manual_plan_supported": correctable,
        "manual_outline": job.get("manual_outline"),
        "retry_supported": correctable,
    }


@app.get("/reconstructions/{job_id}/diagnostics/{artifact_name}")
async def get_reconstruction_diagnostic_artifact(job_id: str, artifact_name: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    diagnostics_dir = (GEOMETRY_DIR / job_id).resolve()
    candidate = (diagnostics_dir / artifact_name).resolve()
    if not candidate.is_relative_to(diagnostics_dir) or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Diagnostic artifact not found")
    media_types = {
        ".png": "image/png",
        ".json": "application/json",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }
    return FileResponse(candidate, media_type=media_types.get(candidate.suffix.lower()))


@app.get("/reconstructions/{job_id}/assets")
async def list_reconstruction_assets(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job_output_dir = OUTPUT_DIR / job_id
    if not job_output_dir.exists():
        return {"job_id": job_id, "assets": []}
    assets = []
    for file_path in sorted(job_output_dir.rglob("*")):
        if file_path.is_file() and file_path.name != "manifest.json.tmp":
            assets.append(
                {
                    "name": file_path.name,
                    "size": file_path.stat().st_size,
                    "type": file_path.suffix,
                }
            )
    return {"job_id": job_id, "assets": assets}


@app.get("/reconstructions/{job_id}/assets/{asset_name}")
async def get_reconstruction_asset(job_id: str, asset_name: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job_output_dir = (OUTPUT_DIR / job_id).resolve()
    candidate = (job_output_dir / asset_name).resolve()
    if not candidate.is_relative_to(job_output_dir) or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
    media_types = {
        ".glb": "model/gltf-binary",
        ".json": "application/json",
        ".ply": "model/ply",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }
    return FileResponse(candidate, media_type=media_types.get(candidate.suffix.lower()))


@app.get("/reconstructions/{job_id}/report")
async def get_reconstruction_report(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    report_path = OUTPUT_DIR / job_id / "validation_report.json"
    validation = None
    if report_path.exists():
        try:
            validation = json.loads(report_path.read_text())
        except json.JSONDecodeError:
            validation = None
    return {
        "job_id": job_id,
        "property_id": job.get("property_id"),
        "project_name": job.get("project_name"),
        "status": job.get("status"),
        "progress": job.get("progress"),
        "floor_plan_filename": job.get("floor_plan_filename"),
        "photo_count": job.get("photo_count"),
        "inputs": job.get("inputs"),
        "stage_records": job.get("stage_records"),
        "diagnostics": job.get("diagnostics", {}),
        "warnings": job.get("warnings", []),
        "limitations": job.get("limitations", []),
        "geometry_valid": job.get("geometry_valid"),
        "navigation_available": job.get("navigation_available"),
        "scale_verified": job.get("scale_verified"),
        "room_types": job.get("room_types") or [],
        "room_types_confirmed": bool(job.get("room_types_confirmed")),
        "furnishing_style": job.get("furnishing_style"),
        "furnishing_variant": job.get("furnishing_variant"),
        "furniture_count": job.get("furniture_count"),
        "validation": validation,
        "error": job.get("error"),
        "error_code": job.get("error_code"),
        "failed_stage": job.get("failed_stage"),
        "diagnostics_url": f"/reconstructions/{job_id}/diagnostics",
    }


@app.get("/reconstructions/{job_id}/logs")
async def get_job_logs(job_id: str, tail: int = 60):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    log_path = LOGS_DIR / f"{job_id}.log"
    if not log_path.exists():
        return {"job_id": job_id, "lines": []}
    lines = log_path.read_text(encoding="utf-8").splitlines()
    return {"job_id": job_id, "lines": lines[-max(1, min(tail, 500)):]}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8002)
