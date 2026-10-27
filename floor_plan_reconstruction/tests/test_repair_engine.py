"""Autonomous repair-loop regression tests.

Every scenario builds a real scene through the parser, furnisher, geometry
builder and scene validator.  Faults are injected into the real plan or the
generated artifacts, never into the validator, so a passing test proves the
engine repaired the scene rather than bypassed a check.
"""
from __future__ import annotations

import asyncio
import copy
import json
import uuid
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from shapely.geometry import Polygon
from shapely.geometry import box as shapely_box

from furnishing import furnish_floor_plan
from geometry_builder import GeometryBuilder
from house_model import normalize_house
from improved_floor_plan_parser import ImprovedFloorPlanParser
from repair_engine import SceneRepairEngine
from scene_validator import SceneValidator

ENGINE_DIR = Path(__file__).resolve().parents[1]
ROOM_TYPES = ["living_room", "kitchen", "bedroom", "bathroom"]
FOUR_ROOM_PLAN = ENGINE_DIR / "input/test_floor_plan.png"


def run(coro):
    return asyncio.run(coro)


def create_plan_image(path: Path, draw, size=(800, 600)) -> Path:
    image = Image.new("RGB", size, "white")
    draw(ImageDraw.Draw(image), size)
    image.save(path)
    return path


def one_room_plan(path: Path) -> Path:
    return create_plan_image(
        path,
        lambda draw, size: draw.rectangle(
            [80, 80, size[0] - 80, size[1] - 80], outline="black", width=12
        ),
        (700, 700),
    )


def sealed_two_room_plan(path: Path) -> Path:
    def draw(canvas, size):
        canvas.rectangle([80, 80, size[0] - 80, size[1] - 80], outline="black", width=12)
        canvas.rectangle(
            [size[0] // 2 - 6, 80, size[0] // 2 + 6, size[1] - 80], fill="black"
        )

    return create_plan_image(path, draw)


def l_shaped_plan(path: Path) -> Path:
    def draw(canvas, size):
        points = [(80, 80), (620, 80), (620, 300), (400, 300), (400, 520), (80, 520)]
        canvas.line(points + [points[0]], fill="black", width=12, joint="curve")

    return create_plan_image(path, draw, (700, 600))


async def parse(plan_path: Path, job_id: str, width: float, length: float) -> dict:
    return await ImprovedFloorPlanParser().parse_floor_plan(
        job_id, str(plan_path), width, length
    )


def build_metadata(plan: dict, room_types) -> dict:
    metadata = {}
    for room, room_type in zip(
        sorted(plan.get("rooms") or [], key=lambda entry: int(entry["id"])), room_types
    ):
        metadata[int(room["id"])] = {
            "room_type": room_type,
            "name": "",
            "source": "user",
        }
    return metadata


def item_area(item: dict) -> float:
    footprint = item.get("footprint") or {}
    minimum = footprint.get("min") or [0.0, 0.0]
    maximum = footprint.get("max") or [0.0, 0.0]
    return max(0.0, float(maximum[0]) - float(minimum[0])) * max(
        0.0, float(maximum[1]) - float(minimum[1])
    )


def move_footprint(item: dict, center) -> dict:
    footprint = item["footprint"]
    width = float(footprint["max"][0]) - float(footprint["min"][0])
    depth = float(footprint["max"][1]) - float(footprint["min"][1])
    rect = {
        "min": [round(center[0] - width / 2, 4), round(center[1] - depth / 2, 4)],
        "max": [round(center[0] + width / 2, 4), round(center[1] + depth / 2, 4)],
    }
    item["position"][0] = round(center[0], 4)
    item["position"][2] = round(center[1], 4)
    item["footprint"] = rect
    item["collision_bounds"] = copy.deepcopy(rect)
    item["bbox"]["min"][0] = rect["min"][0]
    item["bbox"]["min"][2] = rect["min"][1]
    item["bbox"]["max"][0] = rect["max"][0]
    item["bbox"]["max"][2] = rect["max"][1]
    return item


def place_furniture_in_doorway(plan: dict, furnishing: dict) -> str:
    """Move a real furnished item into a doorway clearance zone."""
    door = sorted(plan["doorways"], key=lambda entry: int(entry["id"]))[0]
    center_x, center_z = (float(value) for value in door["center_m"])
    rooms = {int(room["id"]): room for room in plan["rooms"]}
    for room_id in [value for value in door.get("rooms") or [] if value is not None]:
        room = rooms.get(int(room_id))
        if room is None:
            continue
        polygon = shapely_box(*(
            room["bounds_m"][0],
            room["bounds_m"][1],
            room["bounds_m"][0] + room["bounds_m"][2],
            room["bounds_m"][1] + room["bounds_m"][3],
        ))
        room_center_z = room["bounds_m"][1] + room["bounds_m"][3] / 2
        direction = 1.0 if room_center_z > center_z else -1.0
        candidates = [
            item
            for item in furnishing["items"]
            if int(item.get("room_id", -1)) == int(room_id) and item.get("collision")
        ]
        candidates.sort(key=item_area)
        for item in candidates[:5]:
            footprint = item["footprint"]
            depth = float(footprint["max"][1]) - float(footprint["min"][1])
            for offset in (0.45, 0.35, 0.55):
                target = (center_x, center_z + direction * offset)
                moved = copy.deepcopy(item)
                try:
                    move_footprint(moved, target)
                except (KeyError, TypeError):
                    continue
                rect = moved["footprint"]
                if not polygon.covers(
                    shapely_box(rect["min"][0], rect["min"][1], rect["max"][0], rect["max"][1])
                ):
                    continue
                overlaps = False
                for other in furnishing["items"]:
                    if other is item or not other.get("collision"):
                        continue
                    bounds = other["footprint"]
                    if (
                        rect["min"][0] < bounds["max"][0]
                        and rect["max"][0] > bounds["min"][0]
                        and rect["min"][1] < bounds["max"][1]
                        and rect["max"][1] > bounds["min"][1]
                    ):
                        overlaps = True
                        break
                if overlaps:
                    continue
                item.update(moved)
                return str(item["id"])
    raise AssertionError("Could not place a real furniture item in a doorway")


async def run_scene(
    plan_path: Path,
    cleanup_job_dirs,
    *,
    room_types=ROOM_TYPES,
    known=(12.0, 9.0),
    plan_mutator=None,
    furnishing_mutator=None,
    output_mutator=None,
    max_attempts: int = 3,
    job_prefix: str = "pytest-repair",
):
    job_id = f"{job_prefix}-{uuid.uuid4().hex[:10]}"
    cleanup_job_dirs.append(job_id)
    plan = await parse(plan_path, job_id, known[0], known[1])
    assert plan.get("status") == "completed", plan.get("error")
    if plan_mutator is not None:
        plan_mutator(plan)
    metadata = build_metadata(plan, room_types)
    builder = GeometryBuilder()
    validator = SceneValidator()
    state = {"furnishing_mutated": False, "builds": 0}

    def furnish(current_plan, room_metadata, style, variant, allow_partial):
        result = furnish_floor_plan(
            current_plan.get("rooms") or [],
            room_metadata,
            current_plan.get("doorways") or [],
            current_plan.get("windows") or [],
            style,
            variant,
            allow_partial=allow_partial,
        )
        if furnishing_mutator is not None and not state["furnishing_mutated"]:
            furnishing_mutator(current_plan, result)
            state["furnishing_mutated"] = True
        return result

    async def build(current_plan, current_furnishing, style):
        result = await builder.build_geometry(
            job_id,
            current_plan,
            {},
            1,
            known[0],
            known[1],
            None,
            photo_manifest=None,
            room_metadata=metadata,
            use_photo_floor_textures=False,
            furnishing=current_furnishing,
            style=style,
        )
        state["builds"] += 1
        if output_mutator is not None:
            output_mutator(
                Path(result["output_dir"]),
                current_furnishing,
                current_plan,
                state["builds"],
            )
        return result

    async def validate(geometry, current_plan, current_furnishing):
        return await validator.validate_scene(
            job_id,
            geometry,
            photo_manifest=None,
            floor_plan_result=current_plan,
            furnishing=current_furnishing,
        )

    result = await SceneRepairEngine(max_attempts=max_attempts).run(
        job_id=job_id,
        plan=plan,
        room_metadata=metadata,
        style="modern",
        variant=0,
        furnish=furnish,
        build=build,
        validate=validate,
    )
    return job_id, plan, metadata, result


def repair_codes(result: dict) -> set[str]:
    return {entry.get("code") for entry in (result.get("repair_log") or {}).get("repairs") or []}


def normalization_codes(result: dict) -> set[str]:
    normalization = (result.get("repair_log") or {}).get("normalization") or {}
    return {entry.get("code") for entry in normalization.get("repairs") or []}


def test_full_multi_room_scene_passes_without_furniture_removal(cleanup_job_dirs):
    _, plan, _, result = run(
        run_scene(FOUR_ROOM_PLAN, cleanup_job_dirs, job_prefix="pytest-valid")
    )
    assert result["ok"], result.get("message")
    validation = result["validation"]
    checks = validation["checks"]
    for name in (
        "navigation_from_spawn",
        "doorways_reachable",
        "rooms_walkable",
        "room_navigation_targets",
        "spawn_clearance",
        "doorway_openings_clear",
        "furniture_doorway_clearance",
        "furniture_colliders_accurate",
        "navigation_graph_current",
        "collision_doors_current",
    ):
        assert checks.get(name) is True, (name, checks)
    assert result["house_model"]["rooms"]
    assert len(plan["doorways"]) >= 4
    assert len({room["id"] for room in result["plan"]["rooms"]}) == 4
    assert not any(code == "furniture_removed" for code in repair_codes(result))


def test_single_room_plan_reconstructs_and_validates(cleanup_job_dirs, tmp_path):
    plan_path = one_room_plan(tmp_path / "one_room.png")
    _, _, _, result = run(
        run_scene(
            plan_path,
            cleanup_job_dirs,
            room_types=["living_room"],
            known=(6.0, 6.0),
            job_prefix="pytest-one-room",
        )
    )
    assert result["ok"], result.get("message")
    assert len(result["plan"]["rooms"]) == 1
    assert result["plan"]["doorways"] == []
    assert result["validation"]["checks"]["navigation_from_spawn"] is True
    assert result["furnishing"]["items"]


def test_irregular_room_boundary_stays_valid_and_navigable(cleanup_job_dirs, tmp_path):
    plan_path = l_shaped_plan(tmp_path / "l_shaped.png")
    _, plan, _, result = run(
        run_scene(
            plan_path,
            cleanup_job_dirs,
            room_types=["living_room"],
            known=(8.0, 6.0),
            job_prefix="pytest-irregular",
        )
    )
    assert result["ok"], result.get("message")
    assert len(plan["rooms"]) == 1
    polygon = plan["rooms"][0]["polygon_m"]
    assert len(polygon) >= 6, polygon
    bounds = plan["rooms"][0]["bounds_m"]
    bounds_area = float(bounds[2]) * float(bounds[3])
    polygon_area = Polygon(polygon).area
    assert polygon_area < bounds_area * 0.9, (polygon_area, bounds_area)
    checks = result["validation"]["checks"]
    assert checks["navigation_from_spawn"] is True, checks
    assert checks["rooms_walkable"] is True, checks


def test_narrow_doorway_is_widened_only_within_clearance_limits(cleanup_job_dirs):
    rooms_before = {}

    def narrow(plan):
        door = sorted(plan["doorways"], key=lambda entry: int(entry["id"]))[0]
        door["width_m"] = 0.62
        rooms_before.update(
            {int(room["id"]): copy.deepcopy(room["polygon_m"]) for room in plan["rooms"]}
        )

    _, _, _, result = run(
        run_scene(
            FOUR_ROOM_PLAN,
            cleanup_job_dirs,
            plan_mutator=narrow,
            job_prefix="pytest-narrow-door",
        )
    )
    assert result["ok"], result.get("message")
    assert "doorway_widened_for_clearance" in normalization_codes(result)
    widened = [
        entry
        for entry in (result["repair_log"]["normalization"]["repairs"] or [])
        if entry.get("code") == "doorway_widened_for_clearance"
    ]
    assert widened and widened[0]["after_m"] <= 1.8
    door = sorted(result["plan"]["doorways"], key=lambda entry: int(entry["id"]))[0]
    assert door["effective_width_m"] >= 0.8
    assert door["rooms"] == [0, 2]
    assert result["plan"]["wall_segments"]
    for room in result["plan"]["rooms"]:
        assert room["polygon_m"] == rooms_before[int(room["id"])]


def test_furniture_blocking_a_doorway_is_relocated_not_removed(cleanup_job_dirs):
    obstructing = {}

    def block(plan, furnishing):
        obstructing["id"] = place_furniture_in_doorway(plan, furnishing)

    _, _, _, result = run(
        run_scene(
            FOUR_ROOM_PLAN,
            cleanup_job_dirs,
            furnishing_mutator=block,
            job_prefix="pytest-furniture-door",
        )
    )
    assert obstructing["id"], "the fixture did not inject an obstruction"
    assert result["ok"], result.get("message")
    assert "furniture_moved" in repair_codes(result)
    assert "furniture_removed" not in repair_codes(result)
    final_ids = {str(item["id"]) for item in result["furnishing"]["items"]}
    assert obstructing["id"] in final_ids
    assert result["validation"]["checks"]["furniture_doorway_clearance"] is True
    assert result["validation"]["checks"]["doorways_reachable"] is True


def test_wall_covering_a_doorway_is_trimmed_before_meshes(cleanup_job_dirs):
    injected = {}

    def add_blocking_wall(plan):
        door = sorted(plan["doorways"], key=lambda entry: int(entry["id"]))[0]
        center_x, center_z = (float(value) for value in door["center_m"])
        injected["door_id"] = door["id"]
        plan["wall_segments"].append(
            {
                "orientation": "vertical",
                "start_m": [center_x, center_z - 3.0],
                "end_m": [center_x, center_z + 3.0],
                "thickness_m": 0.12,
            }
        )

    _, _, _, result = run(
        run_scene(
            FOUR_ROOM_PLAN,
            cleanup_job_dirs,
            plan_mutator=add_blocking_wall,
            job_prefix="pytest-blocked-door",
        )
    )
    codes = normalization_codes(result)
    assert codes & {"blocking_wall_trimmed", "blocking_wall_removed"}, codes
    assert result["ok"], result.get("message")
    assert result["validation"]["checks"]["doorway_openings_clear"] is True
    door_ids = {int(door["id"]) for door in result["plan"]["doorways"]}
    assert injected["door_id"] in door_ids
    model = normalize_house(result["plan"])["model"]
    blockers = [
        wall
        for wall in model["walls"]
        if wall.get("openings")
        and any(opening.get("door_id") == injected["door_id"] for opening in wall["openings"])
    ]
    assert blockers, "the doorway must remain cut into a real host wall"


def test_invalid_spawn_is_relocated_inside_a_walkable_room(cleanup_job_dirs):
    def corrupt_spawn(output_dir: Path, furnishing, plan, build_index):
        if build_index != 1:
            return
        path = output_dir / "collision.json"
        collision = json.loads(path.read_text())
        wall = max(
            collision.get("walls") or [],
            key=lambda entry: (entry["max"][0] - entry["min"][0])
            * (entry["max"][1] - entry["min"][1]),
        )
        center_x = (float(wall["min"][0]) + float(wall["max"][0])) / 2
        center_z = (float(wall["min"][1]) + float(wall["max"][1])) / 2
        collision["spawn"]["position"] = [round(center_x, 3), 0.0, round(center_z, 3)]
        path.write_text(json.dumps(collision, indent=2))

    _, _, _, result = run(
        run_scene(
            FOUR_ROOM_PLAN,
            cleanup_job_dirs,
            output_mutator=corrupt_spawn,
            job_prefix="pytest-spawn",
        )
    )
    assert result["ok"], result.get("message")
    assert "spawn_relocated" in repair_codes(result)
    checks = result["validation"]["checks"]
    assert checks["spawn_clearance"] is True
    assert checks["navigation_from_spawn"] is True
    position = result["validation"]["stats"]["navigation"]
    assert position["reachable_area_m2"] > 0


def test_oversized_furniture_collider_is_restored_to_its_footprint(cleanup_job_dirs):
    target = {}

    def inflate(plan, furnishing):
        for item in furnishing["items"]:
            if item.get("collision") and item.get("collision_bounds"):
                bounds = copy.deepcopy(item["collision_bounds"])
                bounds["min"] = [round(bounds["min"][0] - 0.35, 4), round(bounds["min"][1] - 0.35, 4)]
                bounds["max"] = [round(bounds["max"][0] + 0.35, 4), round(bounds["max"][1] + 0.35, 4)]
                item["collision_bounds"] = bounds
                target["id"] = item["id"]
                target["count"] = len(furnishing["items"])
                break

    _, _, _, result = run(
        run_scene(
            FOUR_ROOM_PLAN,
            cleanup_job_dirs,
            furnishing_mutator=inflate,
            job_prefix="pytest-collider",
        )
    )
    assert target["id"], "no collidable furniture was available"
    assert result["ok"], result.get("message")
    assert "furniture_collider_restored" in repair_codes(result)
    assert result["validation"]["checks"]["furniture_colliders_accurate"] is True
    assert len(result["furnishing"]["items"]) == target["count"]
    restored = next(
        item for item in result["furnishing"]["items"] if item["id"] == target["id"]
    )
    assert restored["collision_bounds"] == restored["footprint"]


def test_stale_navigation_graph_is_rebuilt_from_the_plan(cleanup_job_dirs):
    def corrupt_graph(output_dir: Path, furnishing, plan, build_index):
        if build_index != 1:
            return
        path = output_dir / "room_graph.json"
        graph = json.loads(path.read_text())
        graph["edges"] = []
        graph["reachable_rooms"] = [graph.get("spawn_room")]
        graph["connected"] = False
        graph["navigation_available"] = False
        path.write_text(json.dumps(graph, indent=2))

    _, _, _, result = run(
        run_scene(
            FOUR_ROOM_PLAN,
            cleanup_job_dirs,
            output_mutator=corrupt_graph,
            job_prefix="pytest-graph",
        )
    )
    assert result["ok"], result.get("message")
    assert "navigation_graph_rebuilt" in repair_codes(result)
    assert result["validation"]["checks"]["navigation_graph_current"] is True
    graph = json.loads(
        (Path(result["geometry"]["output_dir"]) / "room_graph.json").read_text()
    )
    assert len(graph["edges"]) == len(result["plan"]["doorways"])
    assert set(graph["reachable_rooms"]) == {
        int(room["id"]) for room in result["plan"]["rooms"]
    }


def test_invalid_room_polygon_is_repaired_without_moving_room_boundaries(cleanup_job_dirs):
    original = {}

    def duplicate_vertex(plan):
        room = sorted(plan["rooms"], key=lambda entry: int(entry["id"]))[0]
        original["polygon"] = [list(point) for point in room["polygon_m"]]
        original["area"] = shapely_box(*_polygon_bounds(room["polygon_m"])).area
        room["polygon_m"] = list(room["polygon_m"]) + [list(room["polygon_m"][0])]

    _, _, _, result = run(
        run_scene(
            FOUR_ROOM_PLAN,
            cleanup_job_dirs,
            plan_mutator=duplicate_vertex,
            job_prefix="pytest-invalid-polygon",
        )
    )
    assert result["ok"], result.get("message")
    assert "room_polygon_repaired" in normalization_codes(result)
    repaired = sorted(result["plan"]["rooms"], key=lambda entry: int(entry["id"]))[0]
    assert repaired["polygon_m"] == original["polygon"]
    assert shapely_box(*_polygon_bounds(repaired["polygon_m"])).area == pytest.approx(
        original["area"], abs=0.02
    )
    assert result["validation"]["checks"]["room_navigation_targets"] is True


def _polygon_bounds(points):
    xs = [float(point[0]) for point in points]
    zs = [float(point[1]) for point in points]
    return min(xs), min(zs), max(xs), max(zs)


def test_missing_doorway_between_adjacent_rooms_is_inferred_and_navigable(
    cleanup_job_dirs, tmp_path
):
    plan_path = sealed_two_room_plan(tmp_path / "sealed_two_rooms.png")
    _, _, _, result = run(
        run_scene(
            plan_path,
            cleanup_job_dirs,
            room_types=["living_room", "bedroom"],
            known=(8.0, 6.0),
            job_prefix="pytest-disconnected",
        )
    )
    assert result["ok"], result.get("message")
    inferred = [door for door in result["plan"]["doorways"] if door.get("inferred")]
    assert len(inferred) == 1
    doorway = inferred[0]
    assert {int(room) for room in doorway["rooms"] if room is not None} == {0, 1}
    assert float(doorway["width_m"]) >= 0.5
    adjacency = result["house_model"]["adjacency"]
    assert adjacency["0"] == [1]
    assert adjacency["1"] == [0]
    assert result["validation"]["navigation_available"] is True
    assert result["validation"]["checks"]["rooms_connected"] is True
    assert result["validation"]["checks"]["doorway_openings_clear"] is True
    output_dir = Path(result["geometry"]["output_dir"])
    collision = json.loads((output_dir / "collision.json").read_text())
    collision_door = next(
        door for door in collision["doors"] if int(door["id"]) == int(doorway["id"])
    )
    assert float(collision_door["width_m"]) >= 0.5
    assert any(
        repair.get("code") == "doorway_inferred"
        for repair in (
            (result.get("repair_log") or {}).get("normalization") or {}
        ).get("repairs") or []
    )


def test_no_detectable_rooms_fails_honestly_without_building_a_scene(cleanup_job_dirs):
    job_id = f"pytest-empty-{uuid.uuid4().hex[:10]}"
    cleanup_job_dirs.append(job_id)
    calls = {"build": 0}

    async def build(*args, **kwargs):
        calls["build"] += 1
        raise AssertionError("no scene may be built without a room")

    async def validate(*args, **kwargs):
        raise AssertionError("an empty plan must be rejected before validation")

    def furnish(*args, **kwargs):
        raise AssertionError("an empty plan must be rejected before furnishing")

    result = run(
        SceneRepairEngine(max_attempts=2).run(
            job_id=job_id,
            plan={"rooms": [], "doorways": [], "wall_segments": []},
            room_metadata={},
            style="modern",
            variant=0,
            furnish=furnish,
            build=build,
            validate=validate,
        )
    )
    assert result["ok"] is False
    assert result["error_code"] == "structural_ambiguity"
    assert result["geometry"] is None
    assert calls["build"] == 0
    assert "The house has no usable rooms." in result["message"]


def test_unrepairable_wall_blocker_ends_with_an_honest_error(cleanup_job_dirs):
    def persist_blocker(output_dir: Path, furnishing, plan, build_index):
        path = output_dir / "collision.json"
        collision = json.loads(path.read_text())
        door = collision["doors"][0]
        center_x, center_z = (float(value) for value in door["position"])
        width = float(door["width_m"])
        half = width / 2 + 0.05
        if door["orientation"] == "horizontal":
            box = {
                "min": [center_x - half, center_z - 0.15],
                "max": [center_x + half, center_z + 0.15],
            }
        else:
            box = {
                "min": [center_x - 0.15, center_z - half],
                "max": [center_x + 0.15, center_z + half],
            }
        box["height_m"] = 2.8
        collision["walls"].append(box)
        path.write_text(json.dumps(collision, indent=2))

    _, _, _, result = run(
        run_scene(
            FOUR_ROOM_PLAN,
            cleanup_job_dirs,
            output_mutator=persist_blocker,
            max_attempts=2,
            job_prefix="pytest-unresolved",
        )
    )
    assert result["ok"] is False
    assert result["error_code"] == "scene_validation_failed"
    assert result["repair_log"]["status"] == "failed"
    assert result["repair_log"]["unresolved"]
    assert len(result["repair_log"]["attempts"]) <= 4
    assert result["validation"]["checks"]["doorway_openings_clear"] is False
    # Room 2 remains reachable through the plan's other doorways, so doorway
    # side reachability can still pass; the covered opening is the true fault.
    assert result["validation"]["checks"]["all_connected_rooms_reachable"] is True
    assert any(
        "doorway" in message.lower() or "reach" in message.lower()
        for message in result["repair_log"]["unresolved"]
    )
