"""Structural reconciliation and missing-connection inference tests.

These tests exercise ``normalize_house`` on synthetic plans whose geometry is
fully known, so a passing assertion proves the structural rules held rather
than a validator being relaxed.
"""
from __future__ import annotations

import pytest
from shapely.geometry import Polygon

from house_model import HARD_MIN_PASSAGE_M, normalize_house
from structure_repair import infer_missing_connections, reconcile_openings


def room(room_id: int, x: float, z: float, width: float, depth: float) -> dict:
    return {
        "id": room_id,
        "polygon_m": [
            [x, z],
            [x + width, z],
            [x + width, z + depth],
            [x, z + depth],
        ],
        "room_type": None,
    }


def wall(
    orientation: str,
    line: float,
    start: float,
    end: float,
    thickness: float = 0.12,
    exterior: bool = False,
) -> dict:
    if orientation == "horizontal":
        return {
            "orientation": "horizontal",
            "start_m": [start, line],
            "end_m": [end, line],
            "thickness_m": thickness,
            "exterior": exterior,
        }
    return {
        "orientation": "vertical",
        "start_m": [line, start],
        "end_m": [line, end],
        "thickness_m": thickness,
        "exterior": exterior,
    }


def doorway(
    door_id: int,
    orientation: str,
    center: list[float],
    width: float,
    rooms: list,
    exterior: bool = False,
    kind: str = "door",
) -> dict:
    return {
        "id": door_id,
        "orientation": orientation,
        "center_m": center,
        "width_m": width,
        "rooms": rooms,
        "exterior": exterior,
        "kind": kind,
    }


def codes(result: dict) -> set[str]:
    return {repair.get("code") for repair in result["repairs"]}


def test_duplicate_openings_on_a_thick_wall_are_collapsed():
    plan = {
        "rooms": [room(0, 0, 0, 4, 4), room(1, 4.4, 0, 4, 4)],
        "wall_segments": [wall("vertical", 4.2, 0, 4, thickness=0.45)],
        "doorways": [
            doorway(0, "vertical", [4.2, 2.0], 0.9, [0, 1]),
            doorway(1, "vertical", [4.2, 2.0], 0.9, [0, 1]),
        ],
        "windows": [
            {
                "id": 0,
                "orientation": "vertical",
                "center_m": [4.2, 2.0],
                "width_m": 0.9,
                "room": 0,
            }
        ],
    }
    result = normalize_house(plan)
    assert result["valid"]
    assert len(result["plan"]["doorways"]) == 1
    assert result["plan"]["windows"] == []
    assert "duplicate_doorway_removed" in codes(result)
    assert "window_superseded_by_doorway" in codes(result)


def test_narrow_exterior_opening_is_preserved_as_a_window():
    plan = {
        "rooms": [room(0, 0, 0, 4, 4)],
        "wall_segments": [wall("horizontal", 0, 0, 4, exterior=True)],
        "doorways": [
            doorway(0, "horizontal", [2.0, 0.0], 0.5, [0, None], exterior=True)
        ],
        "windows": [],
    }
    result = normalize_house(plan)
    assert result["valid"]
    assert result["plan"]["doorways"] == []
    windows = result["plan"]["windows"]
    assert len(windows) == 1
    assert windows[0]["width_m"] == pytest.approx(0.5)
    assert windows[0]["preserved_from_door"] == 0
    assert "exterior_opening_preserved_as_window" in codes(result)


def test_doorway_width_is_limited_by_the_physical_wall_span():
    plan = {
        "rooms": [room(0, 0, 0, 3, 4), room(1, 3.2, 0, 2.8, 4)],
        "wall_segments": [
            wall("vertical", 3.05, 0, 1.8),
            wall("vertical", 3.05, 2.4, 4),
        ],
        "doorways": [doorway(0, "vertical", [3.05, 2.1], 0.596, [0, 1])],
        "windows": [],
    }
    result = normalize_house(plan)
    assert result["valid"]
    door = result["plan"]["doorways"][0]
    # The 0.60 m structural gap must not be widened to the 0.80 m comfort width.
    assert door["width_m"] == pytest.approx(0.6, abs=0.02)
    assert door["effective_width_m"] >= HARD_MIN_PASSAGE_M
    assert "doorway_limited_by_structure" in codes(result)


def test_missing_doorway_between_rooms_on_a_shared_wall_is_inferred():
    plan = {
        "rooms": [room(0, 0, 0, 4, 4), room(1, 4.2, 0, 4, 4)],
        "wall_segments": [wall("vertical", 4.1, 0, 4)],
        "doorways": [],
        "windows": [],
    }
    metadata = {
        0: {"room_type": "living_room"},
        1: {"room_type": "bedroom"},
    }
    result = normalize_house(plan, room_metadata=metadata)
    assert result["valid"]
    doorways = result["plan"]["doorways"]
    assert len(doorways) == 1
    door = doorways[0]
    assert door["inferred"] is True
    assert door["kind"] == "door"
    assert door.get("open_plan") is False
    assert {int(value) for value in door["rooms"]} == {0, 1}
    assert door["width_m"] >= HARD_MIN_PASSAGE_M
    assert "doorway_inferred" in codes(result)
    assert result["model"]["adjacency"]["0"] == [1]


def test_open_boundary_is_connected_without_inventing_a_door():
    plan = {
        "rooms": [room(0, 0, 0, 4, 4), room(1, 4, 0, 4, 4)],
        "wall_segments": [],
        "doorways": [],
        "windows": [],
    }
    metadata = {
        0: {"room_type": "living_room"},
        1: {"room_type": "dining_room"},
    }
    result = normalize_house(plan, room_metadata=metadata)
    assert result["valid"]
    doorways = result["plan"]["doorways"]
    assert len(doorways) == 1
    connection = doorways[0]
    assert connection["open_plan"] is True
    assert connection["kind"] == "opening"
    assert "open_plan_connection_inferred" in codes(result)
    assert result["model"]["adjacency"]["0"] == [1]
    # No wall exists on the boundary, so no wall opening may be reported.
    assert connection["wall_openings"] == []


def test_bathroom_is_not_connected_to_an_unrelated_living_room():
    plan = {
        "rooms": [room(0, 0, 0, 4, 4), room(1, 4.2, 0, 4, 4)],
        "wall_segments": [wall("vertical", 4.1, 0, 4)],
        "doorways": [],
        "windows": [],
    }
    metadata = {
        0: {"room_type": "bathroom"},
        1: {"room_type": "living_room"},
    }
    result = normalize_house(plan, room_metadata=metadata)
    assert result["valid"]
    assert result["plan"]["doorways"] == []
    assert result["model"]["adjacency"] == {"0": [], "1": []}


def test_connection_is_not_inferred_through_an_exterior_wall():
    plan = {
        "rooms": [room(0, 0, 0, 4, 4), room(1, 4.2, 0, 4, 4)],
        "wall_segments": [wall("vertical", 4.1, 0, 4, exterior=True)],
        "doorways": [],
        "windows": [],
    }
    metadata = {
        0: {"room_type": "living_room"},
        1: {"room_type": "bedroom"},
    }
    result = normalize_house(plan, room_metadata=metadata)
    assert result["valid"]
    assert result["plan"]["doorways"] == []


def test_connection_is_not_inferred_across_a_wide_void():
    plan = {
        "rooms": [room(0, 0, 0, 4, 4), room(1, 5.4, 0, 3.6, 4)],
        "wall_segments": [],
        "doorways": [],
        "windows": [],
    }
    metadata = {
        0: {"room_type": "living_room"},
        1: {"room_type": "bedroom"},
    }
    result = normalize_house(plan, room_metadata=metadata)
    assert result["valid"]
    assert result["plan"]["doorways"] == []


def test_inferred_doorway_cannot_be_wider_than_the_room_it_serves():
    plan = {
        "rooms": [room(0, 0, 0, 6, 4), room(1, 6.2, 0, 0.53, 6.4)],
        "wall_segments": [wall("vertical", 6.1, 0, 6.4)],
        "doorways": [],
        "windows": [],
    }
    metadata = {
        0: {"room_type": "living_room"},
        1: {"room_type": "hallway"},
    }
    result = normalize_house(plan, room_metadata=metadata)
    assert result["valid"]
    assert result["plan"]["doorways"] == []


def test_reconcile_openings_is_idempotent_on_an_already_normalized_plan():
    plan = {
        "rooms": [room(0, 0, 0, 4, 4), room(1, 4.4, 0, 4, 4)],
        "wall_segments": [wall("vertical", 4.2, 0, 4, thickness=0.45)],
        "doorways": [doorway(0, "vertical", [4.2, 2.0], 0.9, [0, 1])],
        "windows": [],
    }
    first = normalize_house(plan)
    second = normalize_house(first["plan"])
    assert len(first["plan"]["doorways"]) == len(second["plan"]["doorways"]) == 1
    assert second["plan"]["doorways"][0]["width_m"] == pytest.approx(0.9)


def test_reconcile_openings_leaves_unrelated_openings_untouched():
    doorways = [doorway(0, "horizontal", [2.0, 1.0], 0.9, [0, 1])]
    windows = [
        {"id": 0, "orientation": "horizontal", "center_m": [2.0, 3.0], "width_m": 1.2}
    ]
    repairs: list[dict] = []
    ambiguities: list[dict] = []
    reconcile_openings(doorways, windows, repairs, ambiguities)
    assert len(doorways) == 1
    assert len(windows) == 1
    assert repairs == []


def test_infer_missing_connections_respects_the_hard_passage_minimum():
    rooms = [
        {**room(0, 0, 0, 4, 4), "bounds_m": [0, 0, 4, 4]},
        {**room(1, 4.2, 0, 0.47, 4), "bounds_m": [4.2, 0, 0.47, 4]},
    ]
    polygons = {
        0: Polygon([(0, 0), (4, 0), (4, 4), (0, 4)]),
        1: Polygon([(4.2, 0), (4.67, 0), (4.67, 4), (4.2, 4)]),
    }
    doorways: list[dict] = []
    repairs: list[dict] = []
    ambiguities: list[dict] = []
    infer_missing_connections(
        rooms,
        polygons,
        [wall("vertical", 4.1, 0, 4)],
        doorways,
        [],
        repairs,
        ambiguities,
    )
    assert doorways == []
