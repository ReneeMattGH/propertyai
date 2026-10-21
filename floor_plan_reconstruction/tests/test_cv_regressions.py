"""Computer-vision regressions for the floor-plan parser.

The real-world fixture is the user-provided plan that originally failed with
"No enclosed rooms found". Synthetic plans cover wall thickness, door gaps,
light-on-dark drawings, low contrast and noise. Synthetic fixtures are test
inputs only and are never served as a user's reconstruction.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import cv2
import numpy as np
import pytest
import trimesh

from geometry_builder import GeometryBuilder
from improved_floor_plan_parser import ImprovedFloorPlanParser
from photo_analyzer import PhotoAnalyzer

ENGINE_DIR = Path(__file__).resolve().parents[1]
REAL_PLAN = Path(__file__).parent / "fixtures" / "real-world" / "user-floor-plan-door-gaps.png"
REAL_PLAN_SHA256 = "61702e1ef3f056fa7f5f6536e32bf269ef259fc68cf67ade02194b2f8327e18e"


def run(coro):
    return asyncio.run(coro)


def new_job(cleanup_job_dirs, prefix: str = "pytest-cv") -> str:
    job_id = f"{prefix}-{uuid.uuid4().hex[:12]}"
    cleanup_job_dirs.append(job_id)
    return job_id


def parse(plan_path: Path, cleanup_job_dirs, **kwargs):
    job_id = new_job(cleanup_job_dirs)
    result = run(
        ImprovedFloorPlanParser().parse_floor_plan(
            job_id, str(plan_path), kwargs.get("known_width"), kwargs.get("known_length"),
            preprocessing=kwargs.get("preprocessing"),
        )
    )
    return job_id, result


def draw_rect_wall(canvas, orientation, line, start, end, thickness, gaps=(), color=(0, 0, 0)):
    half = thickness // 2
    cursor = start
    color = color if isinstance(color, tuple) else (color, color, color)
    for gap_start, gap_end in sorted(gaps):
        if gap_start > cursor:
            if orientation == "horizontal":
                cv2.rectangle(canvas, (cursor, line - half), (gap_start, line + half), color, -1)
            else:
                cv2.rectangle(canvas, (line - half, cursor), (line + half, gap_start), color, -1)
        cursor = max(cursor, gap_end)
    if cursor < end:
        if orientation == "horizontal":
            cv2.rectangle(canvas, (cursor, line - half), (end, line + half), color, -1)
        else:
            cv2.rectangle(canvas, (line - half, cursor), (line + half, end), color, -1)


def create_four_room_plan(
    path: Path,
    wall: int = 8,
    exterior_gaps=(),
    interior_gaps=True,
    background=255,
    foreground=0,
    noise_sigma: float = 0.0,
) -> Path:
    height, width = 700, 900
    canvas = np.full((height, width, 3), background, dtype=np.uint8)
    x0, y0, x1, y1 = 60, 60, width - 60, height - 60
    mid_x, mid_y = width // 2, height // 2
    door = 58

    draw_rect_wall(canvas, "horizontal", y0, x0, x1, wall, exterior_gaps, foreground)
    draw_rect_wall(canvas, "horizontal", y1, x0, x1, wall, (), foreground)
    draw_rect_wall(canvas, "vertical", x0, y0, y1, wall, (), foreground)
    draw_rect_wall(canvas, "vertical", x1, y0, y1, wall, (), foreground)
    horizontal_gaps = (
        ((x0 + 120, x0 + 120 + door), (x1 - 120 - door, x1 - 120))
        if interior_gaps
        else ()
    )
    vertical_gaps = (
        ((y0 + 110, y0 + 110 + door), (y1 - 110 - door, y1 - 110))
        if interior_gaps
        else ()
    )
    draw_rect_wall(canvas, "horizontal", mid_y, x0 + wall, x1 - wall, wall, horizontal_gaps, foreground)
    draw_rect_wall(canvas, "vertical", mid_x, y0 + wall, y1 - wall, wall, vertical_gaps, foreground)

    if noise_sigma:
        noisy = canvas.astype(np.float32) + np.random.default_rng(7).normal(
            0, noise_sigma, canvas.shape
        )
        canvas = np.clip(noisy, 0, 255).astype(np.uint8)
    cv2.imwrite(str(path), canvas)
    return path


def assert_room_geometry_is_consistent(result):
    assert result["status"] == "completed", result.get("error")
    assert result["rooms"]
    rooms = result["rooms"]
    scale = result["scale_pixels_per_meter"]
    assert scale > 0
    footprint = result["footprint_m"]
    assert 2.5 <= footprint["width"] <= 80
    assert 2.5 <= footprint["height"] <= 80
    for room in rooms:
        assert len(room["polygon_px"]) >= 4
        assert len(room["polygon_m"]) >= 4
        assert room["area_m2"] > 0.3
        x, y, w, h = room["bounds_px"]
        assert w > 0 and h > 0
        assert room["bounds_m"][2] > 0 and room["bounds_m"][3] > 0
        assert 0 <= room["center_px"][0] <= result["image_dimensions"][0]
        assert 0 <= room["center_px"][1] <= result["image_dimensions"][1]
    for door in result["doorways"]:
        assert 0.3 <= door["width_m"] <= 3.5
        assert len(door["center_m"]) == 2
    for window in result.get("windows", []):
        assert window["width_m"] > 0.3
        assert window["sill_m"] < window["head_m"]


def assert_openings_touch_walls(result, tolerance: float = 0.35) -> None:
    walls = result["wall_segments"]
    for opening in result["doorways"] + result.get("windows", []):
        axis = 1 if opening["orientation"] == "horizontal" else 0
        run_axis = 1 - axis
        center = opening["center_m"]
        gap_start = center[run_axis] - opening["width_m"] / 2
        gap_end = center[run_axis] + opening["width_m"] / 2
        matched = False
        for wall in walls:
            if wall.get("orientation") != opening["orientation"]:
                continue
            if abs(wall["start_m"][axis] - center[axis]) > tolerance:
                continue
            run_start = min(wall["start_m"][run_axis], wall["end_m"][run_axis])
            run_end = max(wall["start_m"][run_axis], wall["end_m"][run_axis])
            if (
                abs(run_end - gap_start) <= tolerance
                or abs(run_start - gap_end) <= tolerance
            ):
                matched = True
                break
        assert matched, f"opening {opening} is not a gap in a detected wall"


def test_real_user_plan_parses_rooms_doors_and_windows(cleanup_job_dirs):
    import hashlib

    assert REAL_PLAN.exists()
    assert hashlib.sha256(REAL_PLAN.read_bytes()).hexdigest() == REAL_PLAN_SHA256

    job_id, result = parse(REAL_PLAN, cleanup_job_dirs)

    assert_room_geometry_is_consistent(result)
    assert len(result["rooms"]) >= 5, result.get("segmentation")
    assert len(result["doorways"]) >= 4
    assert len(result["windows"]) >= 2
    assert all(room.get("connected") for room in result["rooms"])
    assert result["segmentation"]["selected_attempt"]
    assert result["preprocessing"] == {"wall_polarity": "auto", "sealing": "auto"}

    diagnostics = ENGINE_DIR / "geometry" / job_id
    for name in (
        "walls_mask.png",
        "barrier.png",
        "rooms_detected.png",
        "openings_detected.png",
        "segmentation.json",
    ):
        assert (diagnostics / name).exists(), name

    # Windows must sit on exterior walls, not be treated as doorways.
    exterior_door_widths = [door["width_m"] for door in result["doorways"] if door["exterior"]]
    assert exterior_door_widths
    assert all(width <= 2.5 for width in exterior_door_widths)
    assert_openings_touch_walls(result)


def test_real_plan_with_the_jobs_supplied_dimensions_stays_honest(cleanup_job_dirs):
    """The originally failing upload supplied 25 m x 40 m dimensions.

    Those dimensions give a much smaller pixels-per-metre value than the
    drawing's furniture implies, so the scale-dependent gap classification
    changes. The parser must still return a usable layout and report any room
    it cannot connect instead of dropping it or claiming full connectivity.
    """
    _, result = parse(REAL_PLAN, cleanup_job_dirs, known_width=25.0, known_length=40.0)

    assert result["status"] == "completed", result.get("error")
    assert result["scale_source"] == "provided_dimensions"
    assert result["scale_verified"] is True
    assert result["scale_pixels_per_meter"] > 0
    assert len(result["rooms"]) >= 5
    assert len(result["doorways"]) >= 4
    disconnected = [room for room in result["rooms"] if not room.get("connected", True)]
    if disconnected:
        assert any("no detected opening" in warning for warning in result["warnings"])
        assert any("unreachable" in warning for warning in result["warnings"])
    for room in result["rooms"]:
        assert len(room["polygon_px"]) >= 4
        assert room["area_px"] > 0


@pytest.mark.parametrize("wall", [4, 8, 16])
def test_wall_thickness_variants_are_parsed(wall, tmp_path, cleanup_job_dirs):
    plan = create_four_room_plan(tmp_path / f"walls_{wall}.png", wall=wall)
    _, result = parse(plan, cleanup_job_dirs)

    assert_room_geometry_is_consistent(result)
    assert len(result["rooms"]) >= 3
    assert len(result["doorways"]) >= 2
    assert_openings_touch_walls(result)


def test_door_gaps_do_not_reject_rooms(tmp_path, cleanup_job_dirs):
    plan = create_four_room_plan(tmp_path / "door_gaps.png")
    _, result = parse(plan, cleanup_job_dirs)

    assert_room_geometry_is_consistent(result)
    assert len(result["rooms"]) == 4
    assert len(result["doorways"]) == 4
    assert all(room.get("connected") for room in result["rooms"])
    width_cm = {round(door["width_m"] * 100) for door in result["doorways"]}
    assert min(width_cm) >= 45


def test_light_lines_on_dark_background_are_inverted(tmp_path, cleanup_job_dirs):
    plan = create_four_room_plan(
        tmp_path / "light_on_dark.png", background=24, foreground=238
    )
    _, result = parse(plan, cleanup_job_dirs)

    assert_room_geometry_is_consistent(result)
    assert result["inverted_drawing"] is True
    assert len(result["rooms"]) >= 3


def test_low_contrast_noisy_plan_is_still_parsed(tmp_path, cleanup_job_dirs):
    plan = create_four_room_plan(
        tmp_path / "low_contrast_noisy.png",
        wall=12,
        background=232,
        foreground=118,
        noise_sigma=4.0,
    )
    _, result = parse(plan, cleanup_job_dirs)

    assert_room_geometry_is_consistent(result)
    assert len(result["rooms"]) >= 3


def test_unusable_plan_fails_honestly_with_diagnostics(tmp_path, cleanup_job_dirs):
    blank = tmp_path / "blank.png"
    cv2.imwrite(str(blank), np.full((700, 900, 3), 255, dtype=np.uint8))
    job_id, result = parse(blank, cleanup_job_dirs)

    assert result["status"] == "failed"
    assert result["error_code"] in {
        "no_wall_pixels",
        "no_wall_segments",
        "no_wall_lines",
        "no_enclosed_rooms",
    }
    assert result["error"]
    diagnostics = ENGINE_DIR / "geometry" / job_id
    assert (diagnostics / "parse_failure.json").exists()

    rng = np.random.default_rng(3)
    noise = tmp_path / "noise.png"
    cv2.imwrite(str(noise), rng.integers(0, 255, (700, 900, 3), dtype=np.uint8))
    _, noise_result = parse(noise, cleanup_job_dirs)
    assert noise_result["status"] == "failed"
    assert noise_result["error_code"]


def test_preprocessing_retry_options_are_real(cleanup_job_dirs):
    _, dark = parse(
        REAL_PLAN,
        cleanup_job_dirs,
        preprocessing={"wall_polarity": "dark", "sealing": "auto"},
    )
    assert_room_geometry_is_consistent(dark)
    assert dark["preprocessing"]["wall_polarity"] == "dark"
    assert any("user-selected preprocessing" in item for item in dark["warnings"])

    _, aggressive = parse(
        REAL_PLAN,
        cleanup_job_dirs,
        preprocessing={"wall_polarity": "auto", "sealing": "aggressive"},
    )
    assert_room_geometry_is_consistent(aggressive)
    assert aggressive["segmentation"]["selected_attempt"] != "primary"

    _, light = parse(
        REAL_PLAN,
        cleanup_job_dirs,
        preprocessing={"wall_polarity": "light", "sealing": "auto"},
    )
    assert light["status"] == "failed"
    assert light["error_code"]


def test_manual_room_outlines_build_geometry_without_cv(tmp_path, cleanup_job_dirs):
    plan = create_four_room_plan(tmp_path / "manual_base.png")
    job_id = new_job(cleanup_job_dirs)
    rooms = [
        {"polygon_px": [[60, 60], [450, 60], [450, 594], [60, 594]]},
        {"polygon_px": [[450, 60], [840, 60], [840, 594], [450, 594]]},
    ]
    doorways = [{"orientation": "vertical", "center_px": [450, 330], "width_px": 58}]
    result = run(
        ImprovedFloorPlanParser().parse_manual_outline(
            job_id, str(plan), 12.0, 9.0, rooms, doorways, 10
        )
    )

    assert_room_geometry_is_consistent(result)
    assert result["segmentation"]["source"] == "manual_outline"
    assert len(result["rooms"]) == 2
    assert len(result["wall_segments"]) == 5
    assert len(result["doorways"]) == 1
    assert result["doorways"][0]["rooms"] == [0, 1]
    assert result["doorways"][0]["manual"] is True
    assert all(room["manual"] for room in result["rooms"])
    assert all(wall["manual"] for wall in result["wall_segments"])
    diagnostics = ENGINE_DIR / "geometry" / job_id
    assert (diagnostics / "manual_outline.json").exists()
    assert (diagnostics / "rooms_detected.png").exists()


def test_manual_room_outlines_reject_ambiguous_input(tmp_path, cleanup_job_dirs):
    plan = create_four_room_plan(tmp_path / "manual_invalid.png")
    parser = ImprovedFloorPlanParser()

    def manual(rooms, doorways):
        job_id = new_job(cleanup_job_dirs)
        return run(
            parser.parse_manual_outline(
                job_id, str(plan), 12.0, 9.0, rooms, doorways, 10
            )
        )

    overlapping = manual(
        [
            {"polygon_px": [[60, 60], [500, 60], [500, 594], [60, 594]]},
            {"polygon_px": [[400, 60], [840, 60], [840, 594], [400, 594]]},
        ],
        [{"orientation": "vertical", "center_px": [500, 300], "width_px": 58}],
    )
    assert overlapping["status"] == "failed"
    assert overlapping["error_code"] == "manual_rooms_overlap"

    disconnected = manual(
        [
            {"polygon_px": [[60, 60], [440, 60], [440, 594], [60, 594]]},
            {"polygon_px": [[440, 60], [840, 60], [840, 594], [440, 594]]},
        ],
        [],
    )
    assert disconnected["status"] == "failed"
    assert disconnected["error_code"] == "manual_outline_disconnected"

    off_wall = manual(
        [
            {"polygon_px": [[60, 60], [440, 60], [440, 594], [60, 594]]},
            {"polygon_px": [[440, 60], [840, 60], [840, 594], [440, 594]]},
        ],
        [{"orientation": "vertical", "center_px": [300, 300], "width_px": 58}],
    )
    assert off_wall["status"] == "failed"
    assert off_wall["error_code"] == "manual_doorway_not_on_wall"

    non_rectangular = manual(
        [{"polygon_px": [[60, 60], [440, 60], [440, 594], [250, 300], [60, 594]]}],
        [],
    )
    assert non_rectangular["status"] == "failed"
    assert non_rectangular["error_code"] == "manual_room_not_axis_aligned"


def test_real_plan_builds_a_valid_glb_with_window_geometry(
    cleanup_job_dirs, fixture_photos
):
    job_id, floor_plan = parse(REAL_PLAN, cleanup_job_dirs)
    assert floor_plan["windows"]

    photos = run(
        PhotoAnalyzer().analyze_photos(
            job_id, [str(path) for path in fixture_photos], floor_plan
        )
    )
    photos.pop("photos", None)
    geometry = run(
        GeometryBuilder().build_geometry(job_id, floor_plan, photos, 1, None, None)
    )

    output_dir = Path(geometry["output_dir"])
    glb = output_dir / "scene.glb"
    assert glb.exists() and glb.stat().st_size > 1024
    scene = trimesh.load(str(glb), force="scene")
    assert "Windows" in scene.geometry
    # Without a confirmed photo manifest, walls use neutral materials only.
    assert "Walls" in scene.geometry

    metadata = json.loads((output_dir / "metadata.json").read_text())
    assert metadata["windows_included"] == len(floor_plan["windows"])
    assert geometry["geometry"]["windows_rendered"] == len(floor_plan["windows"])

    collision = json.loads((output_dir / "collision.json").read_text())
    # Windows are glazed, not passable: collision walls still span the opening.
    window = floor_plan["windows"][0]
    axis = 1 if window["orientation"] == "horizontal" else 0
    run_axis = 1 - axis
    wall_cover = [
        box
        for box in collision["walls"]
        if box["min"][axis] - 0.3
        <= window["center_m"][axis]
        <= box["max"][axis] + 0.3
        and box["min"][run_axis] - 0.2
        <= window["center_m"][run_axis]
        <= box["max"][run_axis] + 0.2
    ]
    assert wall_cover, "window opening must remain a collision barrier"
