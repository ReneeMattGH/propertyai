"""Floor-plan input validation and parsing tests."""
from __future__ import annotations

import asyncio
import uuid

import numpy as np
from PIL import Image

from improved_floor_plan_parser import ImprovedFloorPlanParser


def run(coro):
    return asyncio.run(coro)


def make_job_id(prefix: str) -> str:
    return f"pytest-{prefix}-{uuid.uuid4().hex[:12]}"


def test_validate_inputs_accepts_the_fixture(fixture_plan, fixture_photos):
    parser = ImprovedFloorPlanParser()
    result = run(parser.validate_inputs(str(fixture_plan), [str(p) for p in fixture_photos]))

    assert result["errors"] == []
    assert result["floor_plan_valid"] is True
    assert result["photos_valid"] is True
    assert result["photos"]["valid_count"] == len(fixture_photos)
    assert result["floor_plan"]["width_px"] == 1000
    assert result["floor_plan"]["height_px"] == 800


def test_validate_inputs_rejects_a_text_file_renamed_to_png(tmp_path, fixture_photos):
    fake_plan = tmp_path / "not-an-image.png"
    fake_plan.write_text("this is not a png")

    parser = ImprovedFloorPlanParser()
    result = run(parser.validate_inputs(str(fake_plan), [str(p) for p in fixture_photos]))

    assert result["floor_plan_valid"] is False
    assert result["errors"]
    assert "image" in " ".join(result["errors"]).lower()


def test_validate_inputs_rejects_a_tiny_plan(tmp_path, fixture_photos):
    tiny = tmp_path / "tiny.png"
    Image.fromarray(np.full((80, 80, 3), 255, dtype=np.uint8)).save(tiny)

    parser = ImprovedFloorPlanParser()
    result = run(parser.validate_inputs(str(tiny), [str(p) for p in fixture_photos]))

    assert result["floor_plan_valid"] is False
    assert any("too small" in error for error in result["errors"])


def test_validate_inputs_rejects_unreadable_photos(fixture_plan, tmp_path):
    bad_photo = tmp_path / "broken.jpg"
    bad_photo.write_bytes(b"not an image")

    parser = ImprovedFloorPlanParser()
    result = run(parser.validate_inputs(str(fixture_plan), [str(bad_photo)]))

    assert result["photos_valid"] is False
    assert result["photos"]["valid_count"] == 0
    assert result["photos"]["invalid"][0]["file"] == "broken.jpg"
    assert any("photograph" in error for error in result["errors"])


def test_parse_with_known_dimensions_is_measured(fixture_plan, cleanup_job_dirs):
    job_id = make_job_id("measured")
    cleanup_job_dirs.append(job_id)

    parser = ImprovedFloorPlanParser()
    result = run(parser.parse_floor_plan(job_id, str(fixture_plan), 12.0, 9.0))

    assert result["status"] == "completed", result.get("error")
    assert len(result["rooms"]) == 4
    assert len(result["doorways"]) >= 4
    assert result["scale_verified"] is True
    assert result["scale_source"] == "provided_dimensions"
    assert abs(result["scale_pixels_per_meter"] - 73.33) < 1.5
    assert abs(result["footprint_m"]["width"] - 12.0) < 0.1
    assert abs(result["footprint_m"]["height"] - 9.0) < 0.1
    assert len(set(result["warnings"])) == len(result["warnings"])
    assert not any("Scale was estimated" in warning for warning in result["warnings"])
    assert not any("will be widened" in warning for warning in result["warnings"])

    door_widths = [door["width_m"] for door in result["doorways"]]
    assert min(door_widths) >= 0.7

    for room in result["rooms"]:
        assert room["area_m2"] > 1.0
        assert len(room["polygon_m"]) >= 4


def test_parse_without_dimensions_is_flagged_approximate(fixture_plan, cleanup_job_dirs):
    job_id = make_job_id("estimated")
    cleanup_job_dirs.append(job_id)

    parser = ImprovedFloorPlanParser()
    result = run(parser.parse_floor_plan(job_id, str(fixture_plan), None, None))

    assert result["status"] == "completed", result.get("error")
    assert result["scale_verified"] is False
    assert any("Scale was estimated" in warning for warning in result["warnings"])
    assert len(set(result["warnings"])) == len(result["warnings"])


def test_parse_blank_plan_fails(tmp_path, cleanup_job_dirs):
    job_id = make_job_id("blank")
    cleanup_job_dirs.append(job_id)
    blank = tmp_path / "blank.png"
    Image.fromarray(np.full((1000, 800, 3), 255, dtype=np.uint8)).save(blank)

    parser = ImprovedFloorPlanParser()
    result = run(parser.parse_floor_plan(job_id, str(blank), None, None))

    assert result["status"] == "failed"
    assert result["error"]


def test_unusable_wall_cavities_are_dropped_and_rooms_renumbered():
    warnings = []
    rooms = [
        {"id": 0, "bounds_px": [0, 0, 350, 300], "area_px": 105_000},
        {"id": 1, "bounds_px": [400, 0, 14, 14], "area_px": 196},
        {"id": 2, "bounds_px": [450, 0, 20, 400], "area_px": 8_000},
        {"id": 3, "bounds_px": [500, 0, 250, 260], "area_px": 65_000},
    ]

    kept = ImprovedFloorPlanParser._drop_unusable_rooms(rooms, 50.0, warnings)

    assert [room["bounds_px"] for room in kept] == [
        [0, 0, 350, 300],
        [500, 0, 250, 260],
    ]
    assert [room["id"] for room in kept] == [0, 1]
    assert len(warnings) == 1
    assert "Ignored 2 wall cavity/sliver region(s)" in warnings[0]
    assert "0.08 m²" in warnings[0]
    assert "0.28 m wide" in warnings[0]


def test_usable_rooms_survive_the_sliver_filter():
    warnings = []
    rooms = [
        {"id": 0, "bounds_px": [0, 0, 350, 300], "area_px": 105_000},
        {"id": 1, "bounds_px": [400, 0, 250, 260], "area_px": 65_000},
    ]

    kept = ImprovedFloorPlanParser._drop_unusable_rooms(rooms, 50.0, warnings)

    assert [room["id"] for room in kept] == [0, 1]
    assert warnings == []
