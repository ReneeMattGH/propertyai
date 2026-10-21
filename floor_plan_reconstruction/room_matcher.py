"""Room-photo matching and user-confirmed assignment.

Floor-plan rooms carry geometry but no semantic labels. A classifier can say
"this photograph looks like a kitchen"; it cannot say *which* detected room is
the kitchen. This module therefore never auto-assigns photos to rooms for
properties with more than one room. It exposes safe suggestions and requires
an explicit confirmation for every usable photograph before reconstruction.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

from photo_manifest import (
    ROOM_TYPE_LABELS,
    ROOM_TYPES,
    assignments_by_room,
    match_summary,
    public_photo,
    refresh_duplicate_flags,
    save_photo_manifest,
    usable_photos,
)

CLASSIFIED_TYPES = [room_type for room_type in ROOM_TYPES if room_type != "unknown"]


class AssignmentError(ValueError):
    """Raised when a confirmation payload cannot be applied safely."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def room_shells(floor_plan_result: Dict) -> List[Dict]:
    rooms = sorted(
        floor_plan_result.get("rooms", []), key=lambda room: room.get("id", 0)
    )
    return [
        {
            "room_id": int(room["id"]),
            "default_name": f"Room {int(room['id']) + 1}",
            "area_m2": room.get("area_m2"),
            "center_m": room.get("center_m"),
            "bounds_m": room.get("bounds_m"),
            "polygon_m": room.get("polygon_m") or [],
        }
        for room in rooms
    ]


def _photo_type(photo: Dict) -> Optional[str]:
    classification = photo.get("classification") or {}
    predicted = classification.get("predicted_room_type")
    if predicted in CLASSIFIED_TYPES and classification.get("status") in {
        "classified",
        "ambiguous",
    }:
        return predicted
    return None


def derive_room_metadata(rooms: List[Dict], manifest: Dict) -> Dict[int, Dict]:
    """Derive room names from confirmed photograph classifications.

    A photo says the *category* of the room; numbering (Bedroom 1, Bedroom 2)
    comes from the floor-plan room id, so two bedrooms are never merged.
    """
    assigned = assignments_by_room(manifest)
    confirmed_types = (manifest.get("review") or {}).get("room_types") or {}
    room_types: Dict[int, Optional[str]] = {}
    for room in rooms:
        room_id = int(room["room_id"])
        override = confirmed_types.get(str(room_id))
        if override in ROOM_TYPES:
            room_types[room_id] = override
            continue
        types = [
            room_type
            for room_type in (_photo_type(photo) for photo in assigned.get(room_id, []))
            if room_type
        ]
        room_types[room_id] = Counter(types).most_common(1)[0][0] if types else None

    bedroom_numbers: Dict[int, int] = {}
    counter = 0
    for room in rooms:
        room_id = int(room["room_id"])
        if room_types.get(room_id) == "bedroom":
            counter += 1
            bedroom_numbers[room_id] = counter

    metadata: Dict[int, Dict] = {}
    for room in rooms:
        room_id = int(room["room_id"])
        room_type = room_types.get(room_id)
        confirmed_photos = assigned.get(room_id, [])
        if room_type == "bedroom":
            name = f"Bedroom {bedroom_numbers[room_id]}"
        elif room_type and room_type != "unknown":
            name = ROOM_TYPE_LABELS.get(room_type, room_type.replace("_", " ").title())
        else:
            name = room["default_name"]
        metadata[room_id] = {
            "room_id": room_id,
            "name": name,
            "room_type": room_type,
            "area_m2": room.get("area_m2"),
            "center_m": room.get("center_m"),
            "bounds_m": room.get("bounds_m"),
            "polygon_m": room.get("polygon_m") or [],
            "photo_ids": [photo["photo_id"] for photo in confirmed_photos],
            "photo_count": len(confirmed_photos),
        }
    return metadata


def suggest_assignments(rooms: List[Dict], manifest: Dict) -> List[Dict]:
    """Attach an honest suggestion to each ready photo.

    With unlabelled plan rooms the only safe automatic match is a single-room,
    single-photo property. Everything else is explicitly marked for review.
    """
    photos = usable_photos(manifest)
    suggestions = []
    single_room = len(rooms) == 1 and len(photos) == 1
    for photo in photos:
        photo_type = _photo_type(photo)
        classification = photo.get("classification") or {}
        assignment = photo.get("room_assignment") or {}
        if assignment.get("confirmed") and assignment.get("room_id") is not None:
            suggestion = {
                "status": "confirmed",
                "room_id": assignment["room_id"],
                "confidence": (photo.get("match") or {}).get("confidence"),
                "reason": "Assignment confirmed by the user.",
            }
        elif assignment.get("excluded"):
            suggestion = {
                "status": "excluded",
                "room_id": None,
                "confidence": None,
                "reason": "Excluded by the user.",
            }
        elif single_room:
            suggestion = {
                "status": "suggested",
                "room_id": rooms[0]["room_id"],
                "confidence": classification.get("confidence"),
                "reason": "This property has one room and one photograph; confirm this match.",
            }
        else:
            suggestion = {
                "status": "needs_review",
                "room_id": None,
                "confidence": None,
                "reason": (
                    f"The classifier predicts {classification.get('predicted_label') or 'an unknown type'}, "
                    "but plan rooms have no labels. Choose the matching room; this is not "
                    "applied automatically."
                ),
            }
        suggestions.append(
            {
                "photo_id": photo["photo_id"],
                "predicted_room_type": photo_type,
                "classification_status": classification.get("status"),
                "classification_confidence": classification.get("confidence"),
                **suggestion,
            }
        )
    return suggestions


def apply_assignments(
    manifest: Dict,
    rooms: List[Dict],
    assignments: Iterable[Dict],
    room_types: Optional[Dict[str, str]] = None,
) -> Dict:
    """Validate and persist a user review. Every ready photo is resolved."""
    room_ids = {int(room["room_id"]) for room in rooms}
    photo_index = {
        photo["photo_id"]: photo
        for photo in manifest.get("photos", [])
        if photo.get("processing", {}).get("status") == "ready"
    }
    payload = list(assignments)
    seen: Dict[str, Dict] = {}
    for item in payload:
        photo_id = str(item.get("photo_id") or "")
        if photo_id not in photo_index:
            raise AssignmentError(f"Unknown or unusable photograph: {photo_id or '(missing id)'}")
        if photo_id in seen:
            raise AssignmentError(f"Photograph {photo_id} was assigned more than once")
        seen[photo_id] = item
        excluded = bool(item.get("excluded"))
        room_id = item.get("room_id")
        if excluded and room_id is not None:
            raise AssignmentError(
                f"Photograph {photo_id} cannot be both excluded and assigned to a room"
            )
        if not excluded:
            if room_id is None:
                raise AssignmentError(
                    f"Photograph {photo_id} must be assigned to a room or explicitly excluded"
                )
            try:
                room_id = int(room_id)
            except (TypeError, ValueError) as exc:
                raise AssignmentError(f"Invalid room id for {photo_id}") from exc
            if room_id not in room_ids:
                raise AssignmentError(
                    f"Photograph {photo_id} references room {room_id}, which is not part of this job"
                )
    unresolved = set(photo_index) - set(seen)
    if unresolved:
        raise AssignmentError(
            f"{len(unresolved)} photograph(s) have not been reviewed. Assign every usable "
            "photograph to a room or exclude it."
        )
    if not any(not bool(item.get("excluded")) for item in seen.values()):
        raise AssignmentError(
            "At least one photograph must be assigned to a room for reconstruction"
        )

    if room_types:
        for raw_room_id, room_type in room_types.items():
            try:
                room_id = int(raw_room_id)
            except (TypeError, ValueError) as exc:
                raise AssignmentError(f"Invalid room id {raw_room_id!r}") from exc
            if room_id not in room_ids:
                raise AssignmentError(f"Room {room_id} is not part of this job")
            if room_type not in ROOM_TYPES:
                raise AssignmentError(f"Unknown room type {room_type!r}")

    now = _now()
    chosen_room_types = {
        int(raw_room_id): room_type
        for raw_room_id, room_type in (room_types or {}).items()
    }
    for photo_id, item in seen.items():
        photo = photo_index[photo_id]
        excluded = bool(item.get("excluded"))
        room_id = None if excluded else int(item.get("room_id"))
        predicted = _photo_type(photo)
        classification = photo.get("classification") or {}
        assigned_type = chosen_room_types.get(room_id) if room_id is not None else None
        conflict = None
        if (
            room_id is not None
            and predicted
            and assigned_type in CLASSIFIED_TYPES
            and assigned_type != predicted
            and not classification.get("ambiguous")
            and (classification.get("confidence") or 0) >= 0.5
        ):
            conflict = {
                "detected_room_type": predicted,
                "assigned_room_type": assigned_type,
                "confidence": classification.get("confidence"),
                "message": (
                    f"The classifier detected "
                    f"{ROOM_TYPE_LABELS.get(predicted, predicted)} but this photograph is "
                    f"assigned to a {ROOM_TYPE_LABELS.get(assigned_type, assigned_type)}. "
                    "The user's assignment takes precedence; the conflict is reported."
                ),
            }
        photo["room_assignment"] = {
            "room_id": room_id,
            "confirmed": True,
            "excluded": excluded,
            "confirmed_at": now,
        }
        corrected = bool(
            predicted and room_id is not None and room_types
            and room_types.get(str(room_id))
            and room_types[str(room_id)] != predicted
        )
        photo["match"] = {
            "status": "excluded" if excluded else "confirmed",
            "room_id": room_id,
            "confidence": None if excluded else classification.get("confidence"),
            "reason": (
                "Excluded from the walkthrough by the user."
                if excluded
                else "Confirmed by the user during photo review."
            ),
            "corrected_by_user": corrected,
            "conflict": conflict,
        }

    manifest["review"] = {
        "status": "confirmed",
        "confirmed_at": now,
        "room_types": {str(key): value for key, value in (room_types or {}).items()},
    }
    save_photo_manifest(manifest)
    return manifest


def build_review_payload(job: Dict, floor_plan_result: Dict, manifest: Dict) -> Dict:
    if (manifest.get("review") or {}).get("status") != "confirmed":
        refresh_duplicate_flags(manifest)
    rooms = room_shells(floor_plan_result)
    metadata = derive_room_metadata(rooms, manifest)
    assigned = assignments_by_room(manifest)
    suggestions = {item["photo_id"]: item for item in suggest_assignments(rooms, manifest)}
    photos = []
    for photo in manifest.get("photos", []):
        public = public_photo(job["id"], photo)
        public["suggestion"] = suggestions.get(photo["photo_id"])
        assigned_room_id = (photo.get("room_assignment") or {}).get("room_id")
        public["assigned_room"] = (
            metadata.get(int(assigned_room_id))
            if assigned_room_id is not None
            else None
        )
        photos.append(public)

    review_rooms = []
    for room in rooms:
        room_id = room["room_id"]
        room_meta = metadata[room_id]
        review_rooms.append(
            {
                **room,
                "name": room_meta["name"],
                "room_type": room_meta["room_type"],
                "assigned_photo_ids": [
                    photo["photo_id"] for photo in assigned.get(room_id, [])
                ],
                "assigned_photo_count": len(assigned.get(room_id, [])),
                "has_photos": bool(assigned.get(room_id)),
            }
        )

    summary = match_summary(manifest)
    summary["rooms"] = len(rooms)
    summary["rooms_without_photos"] = sum(
        1 for room in review_rooms if not room["has_photos"]
    )
    summary["ambiguous_photos"] = sum(
        1
        for photo in usable_photos(manifest)
        if (photo.get("classification") or {}).get("ambiguous")
    )
    summary["unavailable_photos"] = sum(
        1
        for photo in usable_photos(manifest)
        if (photo.get("classification") or {}).get("status") == "unavailable"
    )
    return {
        "job_id": job["id"],
        "property_id": job.get("property_id"),
        "status": job.get("status"),
        "rooms": review_rooms,
        "photos": photos,
        "summary": summary,
        "assignment_required": job.get("status") == "awaiting_review",
        "confirmed": (manifest.get("review") or {}).get("status") == "confirmed",
        "classifier": manifest.get("classifier"),
        "room_types": list(ROOM_TYPES),
        "room_type_labels": ROOM_TYPE_LABELS,
        "plan": {
            "scale_pixels_per_meter": floor_plan_result.get("scale_pixels_per_meter"),
            "scale_source": floor_plan_result.get("scale_source"),
            "scale_verified": floor_plan_result.get("scale_verified"),
            "footprint_m": floor_plan_result.get("footprint_m"),
            "floor_plan_url": f"/reconstructions/{job['id']}/floor-plan",
        },
    }
