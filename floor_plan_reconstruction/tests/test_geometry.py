"""Geometry, GLB, photo-manifest and scene-validation tests."""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import struct
import uuid
from pathlib import Path

import numpy as np
import pytest
import trimesh
from PIL import Image

from geometry_builder import GeometryBuilder
from improved_floor_plan_parser import ImprovedFloorPlanParser
from main import BASE_DIR as ENGINE_DIR
from photo_analyzer import PhotoAnalyzer
from photo_manifest import (
    ROOM_TYPE_LABELS,
    ROOM_TYPES,
    build_photo_manifest,
    load_photo_manifest,
    match_summary,
    save_photo_manifest,
    usable_photos,
)
from room_classifier import RoomClassifier
from room_matcher import (
    AssignmentError,
    apply_assignments,
    derive_room_metadata,
    room_shells,
    suggest_assignments,
)
from scene_validator import SceneValidator


def run(coro):
    return asyncio.run(coro)


def read_glb_json(path: Path) -> dict:
    with open(path, "rb") as handle:
        magic, version, _ = struct.unpack("<III", handle.read(12))
        assert magic == 0x46546C67, "not a GLB file"
        assert version == 2
        chunk_length, chunk_type = struct.unpack("<II", handle.read(8))
        assert chunk_type == 0x4E4F534A, "first chunk is not JSON"
        return json.loads(handle.read(chunk_length))


def point_hits_wall(x: float, z: float, walls, margin: float = 0.24) -> bool:
    for wall in walls:
        (min_x, min_z), (max_x, max_z) = wall["min"], wall["max"]
        if min_x - margin <= x <= max_x + margin and min_z - margin <= z <= max_z + margin:
            return True
    return False


def copy_photos_into_job(job_id: str, photo_paths) -> tuple[list[dict], list[str]]:
    job_input = ENGINE_DIR / "input" / job_id
    job_input.mkdir(parents=True, exist_ok=True)
    inputs, stored_paths = [], []
    for index, raw_path in enumerate(photo_paths):
        path = Path(raw_path)
        target = job_input / f"photo_{index:02d}_{path.name}"
        shutil.copy2(path, target)
        content = target.read_bytes()
        inputs.append(
            {
                "original_filename": path.name,
                "stored_filename": target.name,
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
        stored_paths.append(str(target))
    return inputs, stored_paths


def set_classification(photo: dict, room_type: str, confidence: float = 0.9):
    photo["classification"] = {
        "status": "classified",
        "model": "test-fixture-classifier",
        "predicted_room_type": room_type,
        "predicted_label": ROOM_TYPE_LABELS[room_type],
        "confidence": confidence,
        "ambiguous": False,
        "candidates": [
            {"room_type": room_type, "label": ROOM_TYPE_LABELS[room_type], "confidence": confidence}
        ],
        "error": None,
        "classified_at": None,
    }


def create_manifest(
    job_id: str,
    property_id: str,
    photo_paths,
    room_types,
    room_ids,
    room_type_overrides: dict[int, str] | None = None,
    floor_plan_result: dict | None = None,
):
    inputs, stored_paths = copy_photos_into_job(job_id, photo_paths)
    manifest = build_photo_manifest(job_id, property_id, inputs, stored_paths)
    for photo, room_type in zip(manifest["photos"], room_types):
        set_classification(photo, room_type)
    save_photo_manifest(manifest)
    if floor_plan_result is not None:
        rooms = room_shells(floor_plan_result)
        apply_assignments(
            manifest,
            rooms,
            [
                {"photo_id": photo["photo_id"], "room_id": room_id}
                for photo, room_id in zip(manifest["photos"], room_ids)
            ],
            room_type_overrides,
        )
    return manifest


def build_scene(fixture_plan, fixture_photos, cleanup_job_dirs):
    job_id = f"pytest-geometry-{uuid.uuid4().hex[:12]}"
    cleanup_job_dirs.append(job_id)

    parser = ImprovedFloorPlanParser()
    floor_plan = run(parser.parse_floor_plan(job_id, str(fixture_plan), 12.0, 9.0))
    assert floor_plan["status"] == "completed"

    types = ["living_room", "kitchen", "bedroom", "bathroom"]
    manifest = create_manifest(
        job_id,
        f"prop-{job_id}",
        fixture_photos,
        types,
        list(range(len(fixture_photos))),
        {str(index): room_type for index, room_type in enumerate(types)},
        floor_plan,
    )
    photo_analyzer = PhotoAnalyzer()
    photos = run(
        photo_analyzer.analyze_photos(
            job_id,
            [
                str(ENGINE_DIR / "input" / job_id / photo["original"]["stored_filename"])
                for photo in manifest["photos"]
            ],
            floor_plan,
        )
    )
    photos.pop("photos", None)
    from photo_manifest import apply_photo_analysis

    apply_photo_analysis(manifest, photos)
    rooms = room_shells(floor_plan)
    geometry = run(
        GeometryBuilder().build_geometry(
            job_id,
            floor_plan,
            photos,
            1,
            12.0,
            9.0,
            photo_manifest=manifest,
            room_metadata=derive_room_metadata(rooms, manifest),
        )
    )
    return job_id, floor_plan, geometry, manifest


def test_geometry_exports_a_loadable_glb(fixture_plan, fixture_photos, cleanup_job_dirs):
    job_id, floor_plan, geometry, manifest = build_scene(
        fixture_plan, fixture_photos, cleanup_job_dirs
    )

    output_dir = Path(geometry["output_dir"])
    glb_path = output_dir / "scene.glb"
    assert glb_path.exists()
    assert glb_path.read_bytes()[:4] == b"glTF"
    assert glb_path.stat().st_size > 1024

    scene = trimesh.load(str(glb_path), force="scene")
    assert len(scene.geometry) >= 1
    assert sum(len(mesh.faces) for mesh in scene.geometry.values()) > 0
    assert any(name.startswith("Walls_photo_") for name in scene.geometry)
    assert not any(name.startswith("Floors_photo_") for name in scene.geometry)

    glb_json = read_glb_json(glb_path)
    # Wall crops from each confirmed photo; floors are deliberately neutral.
    assert len(glb_json["images"]) == len(fixture_photos)
    assert len(glb_json["textures"]) == len(fixture_photos)

    metadata = json.loads((output_dir / "metadata.json").read_text())
    assert metadata["method"]["photogrammetry_used_for_geometry"] is False
    assert metadata["method"]["image_reconstruction"]["sfm"]["status"] == "not_run"
    assert "room-confirmed wall texture crops" in metadata["method"]["appearance"]
    textures = metadata["method"]["textures"]
    assert textures["applied"] is True
    assert textures["count"] == len(fixture_photos)
    assert textures["floor_textures_used"] is False
    assert {source["file"] for source in textures["sources"]} == {
        path.name for path in fixture_photos
    }
    for source in textures["sources"]:
        photo = next(
            item for item in manifest["photos"] if item["photo_id"] == source["photo_id"]
        )
        assert photo["room_assignment"]["room_id"] == source["room_id"]
        assert (ENGINE_DIR / "geometry" / job_id / source["wall_texture"]).exists()
        assert (ENGINE_DIR / "geometry" / job_id / photo["textures"]["floor_texture"]).exists()
        assert source["floor_texture"] is None
    room_map = textures["room_map"]
    assert set(room_map) == {"0", "1", "2", "3"}
    assert room_map["0"]["files"] == ["living_room.jpg"]
    assert room_map["1"]["files"] == ["kitchen.jpg"]
    assert room_map["2"]["files"] == ["bedroom.jpg"]
    assert room_map["3"]["files"] == ["bathroom.jpg"]
    assert metadata["method"]["textures"]["assignment"].startswith(
        "Room textures come only from photographs the user explicitly confirmed"
    )
    assert metadata["scale"]["verified"] is True
    assert metadata["rooms"] == 4
    assert metadata["num_floors"] == 1
    assert {entry["name"] for entry in metadata["room_assignments"]} == {
        "Living room",
        "Kitchen",
        "Bedroom 1",
        "Bathroom",
    }

    collision = json.loads((output_dir / "collision.json").read_text())
    assert collision["walls"]
    spawn = collision["spawn"]["position"]
    assert len(spawn) == 3
    assert not point_hits_wall(spawn[0], spawn[2], collision["walls"])
    assert collision["footprint"]["width"] > 1
    assert any(door["width_m"] >= 0.5 for door in collision["doors"])
    assert collision["rooms"][0]["name"]

    graph = json.loads((output_dir / "room_graph.json").read_text())
    assert len(graph["nodes"]) == 4
    assert graph["connected"] is True
    assert len(graph["edges"]) >= 4
    for node in graph["nodes"]:
        assert node["photo_count"] == 1
        assert node["photos"][0]["thumbnail_url"] == (
            f"/reconstructions/{job_id}/photos/{node['photo_ids'][0]}/thumbnail"
        )

    for asset in geometry["assets"]:
        path = output_dir / asset["name"]
        assert path.exists()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == asset["sha256"]

    assert geometry["rooms_count"] == 4
    assert geometry["collision_walls"] >= 4
    assert any("Only axis-aligned" in item for item in geometry["limitations"])
    assert any("Door leaves, stairs and furniture" in item for item in geometry["limitations"])
    if floor_plan.get("windows"):
        assert any("window" in item.lower() for item in geometry["limitations"])
        assert metadata["windows_included"] == len(floor_plan["windows"])
        assert "Windows" in scene.geometry
    assert any(
        "assigned to rooms through classification" in item
        for item in geometry["limitations"]
    )
    assert any(
        "complete room photographs are not mapped onto floor or ceiling" in item
        for item in geometry["limitations"]
    )
    assert floor_plan["scale_verified"] is True


def test_scene_validator_accepts_the_built_scene(fixture_plan, fixture_photos, cleanup_job_dirs):
    job_id, floor_plan, geometry, manifest = build_scene(
        fixture_plan, fixture_photos, cleanup_job_dirs
    )

    validation = run(
        SceneValidator().validate_scene(
            job_id,
            geometry,
            photo_manifest=manifest,
            floor_plan_result=floor_plan,
        )
    )

    assert validation["status"] == "completed"
    assert validation["geometry_valid"] is True
    assert validation["navigation_available"] is True
    assert validation["errors"] == []
    for check in (
        "glb_exists",
        "glb_loadable",
        "has_meshes",
        "has_faces",
        "finite_bounds",
        "plausible_size",
        "no_degenerate_faces",
        "metadata_file",
        "asset_job_match",
        "photo_textures_embedded",
        "collision_file",
        "collision_walls",
        "collision_walls_valid",
        "spawn_present",
        "spawn_walkable",
        "room_graph",
        "rooms_present",
        "rooms_connected",
        "passable_doorways",
        "photo_manifest",
        "photo_manifest_scope",
        "photo_review_confirmed",
        "photo_assignments_valid",
        "photo_files_exist",
        "photo_decodable",
        "room_photo_references",
        "room_textures_match",
        "texture_files_decode",
    ):
        assert validation["checks"].get(check) is True, check

    report = Path(geometry["output_dir"]) / "validation_report.json"
    assert report.exists()
    assert json.loads(report.read_text())["geometry_valid"] is True


def test_validator_rejects_a_missing_glb(fixture_plan, fixture_photos, cleanup_job_dirs):
    job_id, floor_plan, geometry, manifest = build_scene(
        fixture_plan, fixture_photos, cleanup_job_dirs
    )
    (Path(geometry["output_dir"]) / "scene.glb").unlink()

    validation = run(
        SceneValidator().validate_scene(
            job_id, geometry, photo_manifest=manifest, floor_plan_result=floor_plan
        )
    )

    assert validation["geometry_valid"] is False
    assert validation["navigation_available"] is False
    assert validation["checks"]["glb_exists"] is False
    assert any("scene.glb is missing" in error for error in validation["errors"])


def test_validator_rejects_an_out_of_bounds_spawn(fixture_plan, fixture_photos, cleanup_job_dirs):
    job_id, floor_plan, geometry, manifest = build_scene(
        fixture_plan, fixture_photos, cleanup_job_dirs
    )
    collision_path = Path(geometry["output_dir"]) / "collision.json"
    collision = json.loads(collision_path.read_text())
    wall = collision["walls"][0]
    collision["spawn"]["position"] = [
        (wall["min"][0] + wall["max"][0]) / 2,
        1.65,
        (wall["min"][1] + wall["max"][1]) / 2,
    ]
    collision_path.write_text(json.dumps(collision))

    validation = run(
        SceneValidator().validate_scene(
            job_id, geometry, photo_manifest=manifest, floor_plan_result=floor_plan
        )
    )

    assert validation["geometry_valid"] is False
    assert validation["checks"]["spawn_walkable"] is False
    assert any("Spawn point intersects" in error for error in validation["errors"])


def test_plan_photo_alignment_conflict_is_flagged():
    builder = GeometryBuilder()
    warnings = builder._photogrammetry_alignment_warnings(
        {
            "status": "reconstructed",
            "alignment": {"aspect_delta": 0.87},
        }
    )
    assert len(warnings) == 1
    assert "may not describe the same property" in warnings[0]

    assert (
        builder._photogrammetry_alignment_warnings(
            {"status": "reconstructed", "alignment": {"aspect_delta": 0.05}}
        )
        == []
    )
    assert (
        builder._photogrammetry_alignment_warnings(
            {"status": "insufficient_overlap", "alignment": {"aspect_delta": 0.87}}
        )
        == []
    )


def test_validator_rejects_metadata_from_another_job(
    fixture_plan, fixture_photos, cleanup_job_dirs
):
    job_id, floor_plan, geometry, manifest = build_scene(
        fixture_plan, fixture_photos, cleanup_job_dirs
    )
    metadata_path = Path(geometry["output_dir"]) / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["job_id"] = "some-other-property-job"
    metadata_path.write_text(json.dumps(metadata))

    validation = run(
        SceneValidator().validate_scene(
            job_id, geometry, photo_manifest=manifest, floor_plan_result=floor_plan
        )
    )

    assert validation["geometry_valid"] is False
    assert validation["checks"]["asset_job_match"] is False
    assert any("does not belong" in error for error in validation["errors"])


def test_validator_rejects_an_unconfirmed_photo_review(
    fixture_plan, fixture_photos, cleanup_job_dirs
):
    job_id, floor_plan, geometry, manifest = build_scene(
        fixture_plan, fixture_photos, cleanup_job_dirs
    )
    manifest["review"] = {"status": "pending", "confirmed_at": None, "room_types": {}}
    for photo in manifest["photos"]:
        photo["room_assignment"]["confirmed"] = False
    save_photo_manifest(manifest)

    validation = run(
        SceneValidator().validate_scene(
            job_id, geometry, photo_manifest=manifest, floor_plan_result=floor_plan
        )
    )

    assert validation["geometry_valid"] is False
    assert validation["checks"]["photo_review_confirmed"] is False
    assert validation["checks"]["photo_assignments_valid"] is False
    assert any("not been confirmed" in error for error in validation["errors"])


def test_validator_rejects_cross_job_manifest_entries(
    fixture_plan, fixture_photos, cleanup_job_dirs
):
    job_id, floor_plan, geometry, manifest = build_scene(
        fixture_plan, fixture_photos, cleanup_job_dirs
    )
    manifest["photos"][0]["job_id"] = "another-job"
    save_photo_manifest(manifest)

    validation = run(
        SceneValidator().validate_scene(
            job_id, geometry, photo_manifest=manifest, floor_plan_result=floor_plan
        )
    )

    assert validation["geometry_valid"] is False
    assert validation["checks"]["photo_manifest_scope"] is False
    assert any("another job" in error for error in validation["errors"])


def test_validator_rejects_cross_room_photo_references(
    fixture_plan, fixture_photos, cleanup_job_dirs
):
    job_id, floor_plan, geometry, manifest = build_scene(
        fixture_plan, fixture_photos, cleanup_job_dirs
    )
    output_dir = Path(geometry["output_dir"])
    graph_path = output_dir / "room_graph.json"
    graph = json.loads(graph_path.read_text())
    first, second = graph["nodes"][0], graph["nodes"][1]
    first["photo_ids"] = list(second["photo_ids"])
    first["photo_count"] = len(first["photo_ids"])
    first["photos"] = list(second["photos"])
    graph_path.write_text(json.dumps(graph))

    validation = run(
        SceneValidator().validate_scene(
            job_id, geometry, photo_manifest=manifest, floor_plan_result=floor_plan
        )
    )

    assert validation["geometry_valid"] is False
    assert validation["checks"]["room_photo_references"] is False
    assert any("assigned to a different room" in error for error in validation["errors"])


def test_validator_rejects_a_corrupt_photo_thumbnail(
    fixture_plan, fixture_photos, cleanup_job_dirs
):
    job_id, floor_plan, geometry, manifest = build_scene(
        fixture_plan, fixture_photos, cleanup_job_dirs
    )
    thumbnail = manifest["photos"][0]["thumbnail"]["filename"]
    thumbnail_path = (
        ENGINE_DIR / "geometry" / job_id / "photos" / "thumbs" / thumbnail
    )
    thumbnail_path.write_bytes(b"not an image")

    validation = run(
        SceneValidator().validate_scene(
            job_id, geometry, photo_manifest=manifest, floor_plan_result=floor_plan
        )
    )

    assert validation["geometry_valid"] is False
    assert validation["checks"]["photo_decodable"] is False
    assert any("cannot be decoded" in error for error in validation["errors"])


def test_outputs_stay_inside_the_engine_workspace(fixture_plan, fixture_photos, cleanup_job_dirs):
    _, _, geometry, _ = build_scene(fixture_plan, fixture_photos, cleanup_job_dirs)
    output_dir = Path(geometry["output_dir"]).resolve()

    assert output_dir.is_relative_to((ENGINE_DIR / "output").resolve())
    assert output_dir.name.startswith("pytest-geometry-")


def test_multiple_photos_per_room_keep_textures_in_their_room(
    fixture_plan, fixture_photos, cleanup_job_dirs
):
    job_id = f"pytest-geometry-{uuid.uuid4().hex[:12]}"
    cleanup_job_dirs.append(job_id)
    floor_plan = run(
        ImprovedFloorPlanParser().parse_floor_plan(
            job_id, str(fixture_plan), 12.0, 9.0
        )
    )
    living_two = Path(fixture_photos[0]).parent / "living_room_second_view.jpg"
    shutil.copy2(fixture_photos[0], living_two)
    selected = [fixture_photos[0], living_two, fixture_photos[1], fixture_photos[2], fixture_photos[3]]
    types = ["living_room", "living_room", "kitchen", "bedroom", "bathroom"]
    manifest = create_manifest(
        job_id,
        f"prop-{job_id}",
        selected,
        types,
        [0, 0, 1, 2, 3],
        {"0": "living_room", "1": "kitchen", "2": "bedroom", "3": "bathroom"},
        floor_plan,
    )
    analysis = run(
        PhotoAnalyzer().analyze_photos(
            job_id,
            [
                str(ENGINE_DIR / "input" / job_id / photo["original"]["stored_filename"])
                for photo in manifest["photos"]
            ],
            floor_plan,
        )
    )
    analysis.pop("photos", None)
    from photo_manifest import apply_photo_analysis

    apply_photo_analysis(manifest, analysis)
    rooms = room_shells(floor_plan)
    geometry = run(
        GeometryBuilder().build_geometry(
            job_id,
            floor_plan,
            analysis,
            1,
            12.0,
            9.0,
            photo_manifest=manifest,
            room_metadata=derive_room_metadata(rooms, manifest),
        )
    )
    metadata = json.loads((Path(geometry["output_dir"]) / "metadata.json").read_text())
    room_map = metadata["method"]["textures"]["room_map"]
    assert sorted(room_map["0"]["files"]) == ["living_room.jpg", "living_room_second_view.jpg"]
    assert room_map["1"]["files"] == ["kitchen.jpg"]
    assert room_map["2"]["files"] == ["bedroom.jpg"]
    assert room_map["3"]["files"] == ["bathroom.jpg"]
    living_ids = set(room_map["0"]["photo_ids"])
    assert len(living_ids) == 2
    kitchen_id = room_map["1"]["photo_ids"][0]
    assert kitchen_id not in living_ids

    graph = json.loads((Path(geometry["output_dir"]) / "room_graph.json").read_text())
    node0 = next(node for node in graph["nodes"] if node["id"] == 0)
    node1 = next(node for node in graph["nodes"] if node["id"] == 1)
    assert set(node0["photo_ids"]) == living_ids
    assert node1["photo_ids"] == [kitchen_id]

    validation = run(
        SceneValidator().validate_scene(
            job_id, geometry, photo_manifest=manifest, floor_plan_result=floor_plan
        )
    )
    assert validation["geometry_valid"] is True, validation["errors"]
    assert validation["checks"]["room_textures_match"] is True


def test_two_bedrooms_are_never_merged_into_one_room(
    fixture_plan, fixture_photos, cleanup_job_dirs
):
    job_id = f"pytest-geometry-{uuid.uuid4().hex[:12]}"
    cleanup_job_dirs.append(job_id)
    floor_plan = run(
        ImprovedFloorPlanParser().parse_floor_plan(
            job_id, str(fixture_plan), 12.0, 9.0
        )
    )
    second_bedroom = Path(fixture_photos[2]).parent / "bedroom_second_view.jpg"
    shutil.copy2(fixture_photos[2], second_bedroom)
    manifest = create_manifest(
        job_id,
        f"prop-{job_id}",
        [fixture_photos[2], second_bedroom],
        ["bedroom", "bedroom"],
        [0, 1],
        {"0": "bedroom", "1": "bedroom"},
        floor_plan,
    )
    rooms = room_shells(floor_plan)
    metadata = derive_room_metadata(rooms, manifest)
    assert metadata[0]["name"] == "Bedroom 1"
    assert metadata[1]["name"] == "Bedroom 2"
    assert metadata[0]["photo_ids"] != metadata[1]["photo_ids"]
    assert metadata[0]["room_type"] == metadata[1]["room_type"] == "bedroom"
    assert (
        load_photo_manifest(job_id)["photos"][0]["room_assignment"]["room_id"] == 0
    )
    assert (
        load_photo_manifest(job_id)["photos"][1]["room_assignment"]["room_id"] == 1
    )


def test_user_correction_overrides_a_classifier_mistake(
    fixture_plan, fixture_photos, cleanup_job_dirs
):
    job_id = f"pytest-geometry-{uuid.uuid4().hex[:12]}"
    cleanup_job_dirs.append(job_id)
    floor_plan = run(
        ImprovedFloorPlanParser().parse_floor_plan(
            job_id, str(fixture_plan), 12.0, 9.0
        )
    )
    manifest = create_manifest(
        job_id,
        f"prop-{job_id}",
        [fixture_photos[2]],
        ["kitchen"],
        [0],
        {"0": "bedroom"},
        floor_plan,
    )
    photo = manifest["photos"][0]
    assert photo["classification"]["predicted_room_type"] == "kitchen"
    assert photo["match"]["corrected_by_user"] is True
    rooms = room_shells(floor_plan)
    metadata = derive_room_metadata(rooms, manifest)
    assert metadata[0]["room_type"] == "bedroom"
    assert metadata[0]["name"] == "Bedroom 1"


def test_assignments_reject_unresolved_photos_and_unknown_rooms(
    fixture_plan, fixture_photos, cleanup_job_dirs
):
    job_id = f"pytest-geometry-{uuid.uuid4().hex[:12]}"
    cleanup_job_dirs.append(job_id)
    floor_plan = run(
        ImprovedFloorPlanParser().parse_floor_plan(
            job_id, str(fixture_plan), 12.0, 9.0
        )
    )
    manifest = create_manifest(
        job_id,
        f"prop-{job_id}",
        [fixture_photos[0], fixture_photos[1]],
        ["living_room", "kitchen"],
        [],
    )
    rooms = room_shells(floor_plan)
    first = manifest["photos"][0]["photo_id"]
    with pytest.raises(AssignmentError, match="not been reviewed"):
        apply_assignments(
            manifest,
            rooms,
            [{"photo_id": first, "room_id": 0}],
            {"0": "living_room"},
        )
    second = manifest["photos"][1]["photo_id"]
    with pytest.raises(AssignmentError, match="not part of this job"):
        apply_assignments(
            manifest,
            rooms,
            [
                {"photo_id": first, "room_id": 0},
                {"photo_id": second, "room_id": 99},
            ],
            {"0": "living_room", "99": "kitchen"},
        )
    with pytest.raises(AssignmentError, match="cannot be both excluded and assigned"):
        apply_assignments(
            manifest,
            rooms,
            [
                {"photo_id": first, "room_id": 0},
                {"photo_id": second, "room_id": 1, "excluded": True},
            ],
            None,
        )


def test_ambiguous_classifications_are_not_auto_assigned(
    fixture_plan, fixture_photos, cleanup_job_dirs
):
    job_id = f"pytest-geometry-{uuid.uuid4().hex[:12]}"
    cleanup_job_dirs.append(job_id)
    floor_plan = run(
        ImprovedFloorPlanParser().parse_floor_plan(
            job_id, str(fixture_plan), 12.0, 9.0
        )
    )
    manifest = create_manifest(
        job_id,
        f"prop-{job_id}",
        [fixture_photos[0], fixture_photos[1]],
        ["unknown", "unknown"],
        [],
    )
    for photo in manifest["photos"]:
        photo["classification"].update(
            {
                "status": "ambiguous",
                "predicted_room_type": "unknown",
                "predicted_label": ROOM_TYPE_LABELS["unknown"],
                "confidence": 0.41,
                "ambiguous": True,
            }
        )
    suggestions = suggest_assignments(room_shells(floor_plan), manifest)
    assert len(suggestions) == 2
    assert all(item["status"] == "needs_review" for item in suggestions)
    assert all(item["room_id"] is None for item in suggestions)
    assert all("not applied automatically" in item["reason"] for item in suggestions)
    assert manifest["review"] == {"status": "pending", "confirmed_at": None, "room_types": {}}
    single = suggest_assignments(room_shells(floor_plan)[:1], manifest)
    assert all(item["status"] == "needs_review" for item in single)


def test_single_room_single_photo_can_be_suggested_but_not_confirmed(
    fixture_plan, fixture_photos, cleanup_job_dirs
):
    job_id = f"pytest-geometry-{uuid.uuid4().hex[:12]}"
    cleanup_job_dirs.append(job_id)
    floor_plan = run(
        ImprovedFloorPlanParser().parse_floor_plan(
            job_id, str(fixture_plan), 12.0, 9.0
        )
    )
    manifest = create_manifest(
        job_id,
        f"prop-{job_id}",
        [fixture_photos[0]],
        ["living_room"],
        [],
    )
    suggestion = suggest_assignments(room_shells(floor_plan)[:1], manifest)[0]
    assert suggestion["status"] == "suggested"
    assert suggestion["room_id"] == 0
    assert manifest["photos"][0]["room_assignment"]["confirmed"] is False


def test_photo_manifest_preserves_orientation_and_aspect(tmp_path, cleanup_job_dirs):
    job_id = f"pytest-geometry-{uuid.uuid4().hex[:12]}"
    cleanup_job_dirs.append(job_id)
    rng = np.random.default_rng(7)

    def write(path: Path, size):
        array = rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8)
        Image.fromarray(array).save(path, "JPEG", quality=90)
        return path

    portrait = write(tmp_path / "portrait.jpg", (300, 600))
    landscape = write(tmp_path / "landscape.jpg", (800, 400))
    panoramic = write(tmp_path / "panoramic.jpg", (4000, 200))
    manifest = create_manifest(
        job_id,
        f"prop-{job_id}",
        [portrait, landscape, panoramic],
        ["bedroom", "living_room", "balcony"],
        [],
    )
    by_name = {photo["original"]["original_filename"]: photo for photo in manifest["photos"]}
    assert by_name["portrait.jpg"]["original"]["orientation"] == "portrait"
    assert by_name["landscape.jpg"]["original"]["orientation"] == "landscape"
    assert by_name["panoramic.jpg"]["original"]["orientation"] == "landscape"
    assert by_name["portrait.jpg"]["original"]["aspect_ratio"] == pytest.approx(0.5)
    assert by_name["landscape.jpg"]["original"]["aspect_ratio"] == pytest.approx(2.0)
    assert by_name["panoramic.jpg"]["original"]["aspect_ratio"] == pytest.approx(20.0)
    warning = "unusual aspect ratio"
    assert any(
        warning in item
        for item in by_name["panoramic.jpg"]["quality"]["warnings"]
    )
    for photo in manifest["photos"]:
        original = photo["original"]
        optimized = photo["optimized"]
        thumbnail = photo["thumbnail"]
        assert optimized["width"] <= 1600 and optimized["height"] <= 1600
        assert thumbnail["width"] <= 480 and thumbnail["height"] <= 480
        assert optimized["width"] / optimized["height"] == pytest.approx(
            original["aspect_ratio"], rel=0.01
        )
        assert thumbnail["width"] / thumbnail["height"] == pytest.approx(
            original["aspect_ratio"], rel=0.01
        )
        path = (
            ENGINE_DIR
            / "geometry"
            / job_id
            / "photos"
            / "optimized"
            / optimized["filename"]
        )
        with Image.open(path) as image:
            assert image.width == optimized["width"]
            assert image.height == optimized["height"]


def test_corrupt_photos_are_rejected_and_never_used(tmp_path, cleanup_job_dirs):
    job_id = f"pytest-geometry-{uuid.uuid4().hex[:12]}"
    cleanup_job_dirs.append(job_id)
    corrupt = tmp_path / "corrupt.jpg"
    corrupt.write_bytes(b"\xff\xd8\xff\xe0not really a jpeg")
    manifest = create_manifest(
        job_id,
        f"prop-{job_id}",
        [corrupt],
        ["unknown"],
        [],
    )
    photo = manifest["photos"][0]
    assert photo["processing"]["status"] == "rejected"
    assert photo["processing"]["errors"]
    assert usable_photos(manifest) == []
    assert match_summary(manifest)["rejected"] == 1
    assert match_summary(manifest)["ready"] == 0


def test_manifest_photo_ids_are_job_scoped(fixture_photos, cleanup_job_dirs):
    first_id = f"pytest-geometry-{uuid.uuid4().hex[:12]}"
    second_id = f"pytest-geometry-{uuid.uuid4().hex[:12]}"
    cleanup_job_dirs.extend([first_id, second_id])
    first = create_manifest(
        first_id, "prop-one", [fixture_photos[0]], ["living_room"], []
    )
    second = create_manifest(
        second_id, "prop-two", [fixture_photos[1]], ["kitchen"], []
    )
    first_photo = first["photos"][0]
    second_photo = second["photos"][0]
    assert first_photo["job_id"] == first_id
    assert second_photo["job_id"] == second_id
    assert first_photo["property_id"] == "prop-one"
    assert second_photo["property_id"] == "prop-two"
    assert first_photo["photo_id"] != second_photo["photo_id"]
    assert first["property_id"] != second["property_id"]


def test_real_room_classifier_runs_on_real_photographs():
    assets = Path(__file__).parent / "assets" / "sfm"
    paths = [str(assets / "overlap_p1180142.jpg"), str(assets / "overlap_p1180143.jpg")]
    results = RoomClassifier().classify_paths(paths)
    assert len(results) == 2
    for result in results:
        assert result["status"] in {"classified", "ambiguous"}, result.get("error")
        assert result["model"] == "openai/clip-vit-base-patch32"
        assert result["predicted_room_type"] in ROOM_TYPES
        assert result["predicted_label"] == ROOM_TYPE_LABELS[result["predicted_room_type"]]
        assert 0.0 < result["confidence"] <= 1.0
        assert result["candidates"]
        assert all(
            candidate["room_type"] in ROOM_TYPES
            for candidate in result["candidates"]
        )
