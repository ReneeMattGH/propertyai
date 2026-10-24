"""Deterministic room-type inference from parsed floor-plan geometry.

The parser reads walls, rooms, doors and windows but does not perform OCR, so
labels printed on a plan are not available as text.  Types are therefore
inferred from structural evidence (area, proportions, door count, exterior
doorways, windows and adjacency).  Every assignment carries a confidence and
the alternatives that scored close behind; the API asks the user to confirm or
correct the proposal before the furnished walkthrough is generated.

Nothing in this module fabricates a type it cannot justify: rooms with unusual
geometry remain ``unknown`` and are left unfurnished.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

ROOM_TYPES: Tuple[str, ...] = (
    "living_room",
    "bedroom",
    "kitchen",
    "bathroom",
    "dining_room",
    "entrance",
    "hallway",
    "balcony",
    "unknown",
)

ROOM_TYPE_LABELS: Dict[str, str] = {
    "living_room": "Living room",
    "bedroom": "Bedroom",
    "kitchen": "Kitchen",
    "bathroom": "Bathroom",
    "dining_room": "Dining room",
    "entrance": "Entrance / foyer",
    "hallway": "Hallway",
    "balcony": "Balcony",
    "unknown": "Unspecified",
}

DEFAULT_NAME_BY_TYPE: Dict[str, str] = {
    "living_room": "Living Room",
    "bedroom": "Bedroom",
    "kitchen": "Kitchen",
    "bathroom": "Bathroom",
    "dining_room": "Dining Room",
    "entrance": "Entrance",
    "hallway": "Hallway",
    "balcony": "Balcony",
    "unknown": "Unspecified Room",
}


@dataclass
class RoomFeatures:
    room_id: int
    area_m2: float
    min_dim: float
    max_dim: float
    aspect: float
    fill_ratio: float
    interior_doors: int
    exterior_doors: int
    windows: int
    is_largest: bool
    adjacent_to_largest: bool
    door_neighbours: List[int] = field(default_factory=list)

    @property
    def has_window(self) -> bool:
        return self.windows > 0

    @property
    def door_count(self) -> int:
        return self.interior_doors + self.exterior_doors


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _features(
    rooms: Sequence[Dict],
    doorways: Sequence[Dict],
    windows: Sequence[Dict],
) -> Dict[int, RoomFeatures]:
    if not rooms:
        return {}
    largest_id = max(rooms, key=lambda room: float(room.get("area_m2") or 0.0))["id"]
    largest_area = float(max(room.get("area_m2") or 0.0 for room in rooms))

    doors_by_room: Dict[int, List[Dict]] = {int(room["id"]): [] for room in rooms}
    for door in doorways:
        for room_id in (door.get("rooms") or [])[:2]:
            if room_id is None:
                continue
            doors_by_room.setdefault(int(room_id), []).append(door)

    windows_by_room: Dict[int, int] = {int(room["id"]): 0 for room in rooms}
    for window in windows:
        room_id = window.get("room")
        if room_id is None:
            continue
        windows_by_room[int(room_id)] = windows_by_room.get(int(room_id), 0) + 1

    result: Dict[int, RoomFeatures] = {}
    for room in rooms:
        room_id = int(room["id"])
        bounds = room.get("bounds_m") or [0.0, 0.0, 1.0, 1.0]
        width = max(0.01, float(bounds[2]))
        depth = max(0.01, float(bounds[3]))
        min_dim = min(width, depth)
        max_dim = max(width, depth)
        area = float(room.get("area_m2") or (width * depth))
        doors = doors_by_room.get(room_id, [])
        interior = 0
        exterior = 0
        neighbours: List[int] = []
        for door in doors:
            other_rooms = [rid for rid in (door.get("rooms") or []) if rid is not None]
            if door.get("exterior") or not other_rooms or other_rooms == [room_id]:
                exterior += 1
                continue
            interior += 1
            for other in other_rooms:
                if int(other) != room_id and int(other) not in neighbours:
                    neighbours.append(int(other))
        result[room_id] = RoomFeatures(
            room_id=room_id,
            area_m2=round(area, 3),
            min_dim=min_dim,
            max_dim=max_dim,
            aspect=max_dim / min_dim,
            fill_ratio=float(room.get("fill_ratio", 1.0) or 1.0),
            interior_doors=interior,
            exterior_doors=exterior,
            windows=windows_by_room.get(room_id, 0),
            is_largest=room_id == largest_id and largest_area > 0,
            adjacent_to_largest=largest_id in neighbours,
            door_neighbours=neighbours,
        )
    return result


def _scores(features: RoomFeatures) -> Dict[str, float]:
    area = features.area_m2
    aspect = features.aspect
    unknown_markers = 0.06

    scores = {room_type: 0.02 for room_type in ROOM_TYPES if room_type != "unknown"}

    living = 0.15
    if area >= 25:
        living += 0.45
    elif area >= 18:
        living += 0.38
    elif area >= 12:
        living += 0.25
    elif area >= 8:
        living += 0.08
    if features.is_largest:
        living += 0.12
    if 1 <= features.interior_doors <= 3:
        living += 0.06
    if aspect > 2.4:
        living -= 0.2
    if features.has_window:
        living += 0.05
    if features.fill_ratio < 0.7:
        living -= 0.1
    scores["living_room"] = living

    bedroom = 0.1
    if 9 <= area <= 25:
        bedroom += 0.4
    elif 6 <= area < 9:
        bedroom += 0.18
    elif 25 < area <= 35:
        bedroom += 0.2
    elif area < 6:
        bedroom -= 0.05
    if features.interior_doors == 1:
        bedroom += 0.08
    if features.has_window:
        bedroom += 0.08
    if 0.55 <= aspect <= 2.0:
        bedroom += 0.05
    if features.fill_ratio < 0.75:
        bedroom -= 0.08
    scores["bedroom"] = bedroom

    kitchen = 0.08
    if 5 <= area <= 18:
        kitchen += 0.35
    elif 18 < area <= 24:
        kitchen += 0.18
    elif 3.5 <= area < 5:
        kitchen += 0.12
    if features.has_window:
        kitchen += 0.07
    if features.interior_doors <= 2:
        kitchen += 0.05
    if features.is_largest:
        kitchen -= 0.1
    if features.adjacent_to_largest:
        kitchen += 0.12
    scores["kitchen"] = kitchen

    bathroom = 0.05
    if 2.5 <= area <= 7.5:
        bathroom += 0.55
    elif 7.5 < area <= 10:
        bathroom += 0.3
    elif 10 < area <= 13:
        bathroom += 0.1
    if features.interior_doors == 1:
        bathroom += 0.08
    if 0.5 <= aspect <= 2.2:
        bathroom += 0.05
    if features.is_largest:
        bathroom -= 0.2
    scores["bathroom"] = bathroom

    dining = 0.05
    if 8 <= area <= 20:
        dining += 0.28
    if 2 <= features.interior_doors <= 3:
        dining += 0.1
    if features.adjacent_to_largest:
        dining += 0.1
    if 0.7 <= aspect <= 1.9:
        dining += 0.05
    if features.is_largest:
        dining -= 0.05
    scores["dining_room"] = dining

    entrance = 0.03
    if features.exterior_doors:
        entrance += 0.62
    if features.min_dim <= 2.6:
        entrance += 0.05
    if features.has_window:
        entrance -= 0.08
    if features.is_largest:
        entrance -= 0.15
    scores["entrance"] = entrance

    hallway = 0.03
    if aspect >= 2.6 and features.min_dim <= 1.9:
        hallway += 0.6
    elif features.min_dim <= 1.5 and area <= 8:
        hallway += 0.5
    elif aspect >= 2.0 and features.min_dim <= 2.2:
        hallway += 0.25
    if features.interior_doors >= 2:
        hallway += 0.1
    if area > 14:
        hallway -= 0.15
    if features.is_largest:
        hallway -= 0.2
    scores["hallway"] = hallway

    balcony = 0.02
    if area <= 6 and aspect >= 1.4 and features.has_window and features.interior_doors <= 1:
        balcony += 0.18
    scores["balcony"] = balcony

    unknown = unknown_markers
    if features.fill_ratio < 0.55:
        unknown += 0.35
    if area > 55:
        unknown += 0.3
    if area < 1.5:
        unknown += 0.3
    scores["unknown"] = unknown

    return {room_type: round(_clamp(score, 0.0, 0.95), 4) for room_type, score in scores.items()}


def _reasons(features: RoomFeatures, room_type: str) -> List[str]:
    reasons: List[str] = [
        f"Room area is {features.area_m2:.1f} m² with a {features.min_dim:.1f} m "
        f"short side and {features.max_dim:.1f} m long side."
    ]
    if features.interior_doors:
        reasons.append(
            f"{features.interior_doors} interior doorway"
            f"{'s' if features.interior_doors != 1 else ''} connect it to the rest of the plan."
        )
    if features.exterior_doors:
        reasons.append("It has a doorway to the outside of the building.")
    if features.windows:
        reasons.append(f"{features.windows} exterior window(s) are on its walls.")
    if features.is_largest:
        reasons.append("It is the largest room in the plan.")
    if features.adjacent_to_largest:
        reasons.append("It opens directly onto the largest room.")
    if features.aspect >= 2.6 and features.min_dim <= 1.9:
        reasons.append("Its long, narrow proportions match a corridor.")
    if features.fill_ratio < 0.75:
        reasons.append("Its outline is irregular rather than a simple rectangle.")
    reasons.append(
        "The plan carries no machine-readable room label, so this type is inferred "
        "from geometry and must be confirmed."
    )
    return reasons


def infer_room_types(
    rooms: Sequence[Dict],
    doorways: Sequence[Dict] = (),
    windows: Sequence[Dict] = (),
    explicit: Optional[Dict[int, str]] = None,
    footprint_m: Optional[Dict] = None,
) -> Dict:
    """Return a per-room type proposal plus review metadata.

    ``explicit`` holds user-supplied types (from manual correction); those rooms
    keep the supplied type at full confidence and no longer require review.
    """
    explicit = {int(key): value for key, value in (explicit or {}).items()}
    features = _features(rooms, doorways, windows)
    assignments: List[Dict] = []
    for room in sorted(rooms, key=lambda item: int(item["id"])):
        room_id = int(room["id"])
        room_features = features[room_id]
        if room_id in explicit:
            room_type = explicit[room_id]
            if room_type not in ROOM_TYPES:
                raise ValueError(f"Unsupported room type {room_type!r} for room {room_id}")
            assignments.append(
                {
                    "room_id": room_id,
                    "room_type": room_type,
                    "confidence": 1.0,
                    "needs_confirmation": False,
                    "source": "user",
                    "alternatives": [],
                    "reasons": ["Room type supplied by the user."],
                }
            )
            continue

        scores = _scores(room_features)
        ranked = sorted(scores.items(), key=lambda item: (-item[1], ROOM_TYPES.index(item[0])))
        top_type, top_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        margin = top_score - second_score
        confidence = top_score * (0.75 + 0.25 * _clamp(margin / 0.3))
        assignments.append(
            {
                "room_id": room_id,
                "room_type": top_type,
                "confidence": round(_clamp(confidence, 0.0, 0.98), 3),
                "needs_confirmation": confidence < 0.8 or margin < 0.12,
                "source": "inferred",
                "alternatives": [
                    {
                        "room_type": candidate,
                        "score": round(score, 3),
                    }
                    for candidate, score in ranked[1:4]
                ],
                "reasons": _reasons(room_features, top_type),
            }
        )

    _assign_names(assignments)
    by_room = {assignment["room_id"]: assignment for assignment in assignments}
    requires_confirmation = any(
        assignment["needs_confirmation"] or assignment["room_type"] == "unknown"
        for assignment in assignments
    )
    return {
        "rooms": [
            {
                **by_room[room_id],
                "name": name,
            }
            for room_id, name in _name_map(assignments).items()
        ],
        "requires_confirmation": requires_confirmation,
        "source": "user" if explicit else "inferred",
        "room_type_labels": ROOM_TYPE_LABELS,
        "footprint_m": footprint_m,
        "notes": (
            "Room types were inferred from floor-plan geometry because the plan "
            "contains no machine-readable room labels. Confirm or correct them before "
            "furnishing."
        )
        if not explicit
        else "Room types were supplied by the user.",
    }


def _name_map(assignments: Sequence[Dict]) -> Dict[int, str]:
    counters: Dict[str, int] = {}
    names: Dict[int, str] = {}
    for assignment in sorted(assignments, key=lambda item: item["room_id"]):
        room_type = assignment["room_type"]
        base = DEFAULT_NAME_BY_TYPE.get(room_type, "Room")
        counters[room_type] = counters.get(room_type, 0) + 1
        if room_type in ("bedroom", "bathroom"):
            name = f"{base} {counters[room_type]}"
        elif counters[room_type] > 1:
            name = f"{base} {counters[room_type]}"
        else:
            name = base
        names[assignment["room_id"]] = name
    return names


def _assign_names(assignments: List[Dict]) -> None:
    names = _name_map(assignments)
    for assignment in assignments:
        assignment["name"] = names[assignment["room_id"]]


def normalize_confirmed_types(
    payload: Optional[Dict],
    rooms: Sequence[Dict],
    fallback: Optional[Sequence[Dict]] = None,
) -> Dict[int, Dict]:
    """Validate a client's room-type selection and return a metadata mapping.

    Every room must be present exactly once; unknown types are rejected.  When a
    payload omits name, the deterministic default name for that type is used.
    """
    valid_ids = {int(room["id"]) for room in rooms}
    fallback_by_id = {
        int(entry["room_id"]): entry for entry in (fallback or []) if entry.get("room_id") is not None
    }
    entries: Iterable[Dict]
    if payload is None:
        if not fallback_by_id:
            raise ValueError("Room types have not been confirmed for this floor plan.")
        entries = [
            {
                "room_id": room_id,
                "room_type": fallback_by_id[room_id]["room_type"],
                "name": fallback_by_id[room_id].get("name"),
            }
            for room_id in sorted(fallback_by_id)
        ]
    else:
        raw = payload.get("rooms")
        if not isinstance(raw, list):
            raise ValueError("Room type confirmation must contain a 'rooms' list.")
        entries = raw

    result: Dict[int, Dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Each room type entry must be an object.")
        if entry.get("room_id") is None:
            raise ValueError("Each room type entry needs a room_id.")
        room_id = int(entry["room_id"])
        if room_id not in valid_ids:
            raise ValueError(f"Room {room_id} does not exist in this floor plan.")
        if room_id in result:
            raise ValueError(f"Room {room_id} was confirmed more than once.")
        room_type = str(entry.get("room_type") or "").strip()
        if room_type not in ROOM_TYPES:
            raise ValueError(
                f"Unsupported room type {room_type!r}. Choose one of: {', '.join(ROOM_TYPES)}."
            )
        name = str(entry.get("name") or "").strip()
        if len(name) > 60:
            raise ValueError("Room names must be 60 characters or fewer.")
        result[room_id] = {
            "room_type": room_type,
            "name": name or None,
        }
    missing = sorted(valid_ids - set(result))
    if missing:
        raise ValueError(
            "Room type confirmation is missing room(s): "
            + ", ".join(str(room_id) for room_id in missing)
        )
    return result


def apply_names(assignments: Dict[int, Dict], rooms: Sequence[Dict]) -> Dict[int, Dict]:
    """Fill default names for confirmed rooms that did not supply a custom one."""
    counters: Dict[str, int] = {}
    for room_id in sorted(assignments):
        metadata = assignments[room_id]
        room_type = metadata["room_type"]
        counters[room_type] = counters.get(room_type, 0) + 1
        if metadata.get("name"):
            continue
        base = DEFAULT_NAME_BY_TYPE.get(room_type, "Room")
        if room_type in ("bedroom", "bathroom") or counters[room_type] > 1:
            metadata["name"] = f"{base} {counters[room_type]}"
        else:
            metadata["name"] = base
    return assignments
