"""Floor-plan-only reconstruction and automatic furnishing tests."""
from __future__ import annotations

import hashlib
import io
import json
import struct
import uuid
from collections import deque
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw
from shapely.geometry import box as shapely_box

from furnishing import (
    door_clearance_zones,
    furnish_floor_plan,
    furniture_collision_items,
    validate_furnishing,
)
from furniture_assets import STYLES
from scene_validator import SceneValidator

EXPECTED_STAGES = [
    "input_validation",
    "floor_plan_parse",
    "room_type_inference",
    "room_types",
    "furnishing_plan",
    "geometry_build",
    "scene_validation",
    "manifest",
]


def upload(client, plan_bytes: bytes, **form):
    return client.post(
        "/reconstructions",
        data={"project_name": "Floor-plan-only test property", **form},
        files={"floor_plan": ("plan.png", plan_bytes, "image/png")},
    )


def fetch_room_types(client, job_id: str) -> dict:
    response = client.get(f"/reconstructions/{job_id}/room-types")
    assert response.status_code == 200, response.text
    return response.json()


def room_payload(review: dict, room_types: list[str]) -> list[dict]:
    rooms = sorted(review["rooms"], key=lambda entry: int(entry["room_id"]))
    assert len(rooms) == len(room_types)
    return [
        {"room_id": int(room["room_id"]), "room_type": room_type, "name": ""}
        for room, room_type in zip(rooms, room_types)
    ]


def confirm_room_types(client, job_id: str, room_types: list[str], **extra):
    review = fetch_room_types(client, job_id)
    payload = {"rooms": room_payload(review, room_types), **extra}
    response = client.post(f"/reconstructions/{job_id}/room-types", json=payload)
    assert response.status_code == 200, response.text
    return review


def complete_job(client, plan_bytes: bytes, room_types: list[str], **form):
    response = upload(client, plan_bytes, **form)
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]
    pending = client.get(f"/reconstructions/{job_id}").json()
    assert pending["status"] == "awaiting_room_types", pending.get("error")
    review = confirm_room_types(client, job_id, room_types)
    job = client.get(f"/reconstructions/{job_id}").json()
    assert job["status"] == "completed", job.get("error")
    return job_id, review, job


def read_glb_json(content: bytes) -> dict:
    assert content[:4] == b"glTF"
    magic, version, _ = struct.unpack("<III", content[:12])
    assert magic == 0x46546C67
    assert version == 2
    offset = 12
    while offset < len(content):
        length, kind = struct.unpack("<II", content[offset:offset + 8])
        payload = content[offset + 8:offset + 8 + length]
        if kind == 0x4E4F534A:
            return json.loads(payload.decode("utf-8"))
        offset += 8 + length
    raise AssertionError("GLB has no JSON chunk")


def scene_bundle(client, job: dict) -> dict:
    assert job["scene_url"], job
    response = client.get(job["scene_url"])
    assert response.status_code == 200, response.text
    return response.json()


def asset_ids_in(furnishing, room_id: int) -> set[str]:
    items = furnishing["items"] if "items" in furnishing else furnishing
    return {
        item["asset_id"]
        for item in items
        if int(item["room_id"]) == room_id
    }


def create_six_room_plan() -> bytes:
    image = Image.new("RGB", (1000, 700), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([50, 50, 950, 650], outline="black", width=12)
    for x in (333, 667):
        draw.rectangle([x - 6, 50, x + 6, 290], fill="black")
        draw.rectangle([x - 6, 340, x + 6, 480], fill="black")
        draw.rectangle([x - 6, 530, x + 6, 650], fill="black")
    for left, right in ((50, 250), (300, 550), (600, 950)):
        draw.rectangle([left, 344, right, 356], fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def grid_cell(xs, zs, point):
    return (
        int(np.argmin(np.abs(zs - point[1]))),
        int(np.argmin(np.abs(xs - point[0]))),
    )


def flood_from(free, xs, zs, point):
    row, col = grid_cell(xs, zs, point)
    assert free[row, col], f"No walkable cell near {point}"
    visited = np.zeros_like(free, dtype=bool)
    visited[row, col] = True
    queue = deque([(row, col)])
    while queue:
        current_row, current_col = queue.popleft()
        for delta_row, delta_col in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            next_row = current_row + delta_row
            next_col = current_col + delta_col
            if not (0 <= next_row < free.shape[0] and 0 <= next_col < free.shape[1]):
                continue
            if free[next_row, next_col] and not visited[next_row, next_col]:
                visited[next_row, next_col] = True
                queue.append((next_row, next_col))
    return visited


def test_floor_plan_only_flow_furnishes_and_completes(client, fixture_plan):
    plan = fixture_plan.read_bytes()
    response = upload(
        client,
        plan,
        known_width="12",
        known_length="9",
        property_id="prop-floor-plan-only",
        style="scandinavian",
        variant="1",
    )
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]

    pending = client.get(f"/reconstructions/{job_id}").json()
    assert pending["status"] == "awaiting_room_types"
    assert pending["room_types_confirmed"] is False
    assert pending["photo_count"] == 0
    assert pending["inputs"]["photos"] == []
    assert pending["scene_url"] is None
    assert pending["manifest_url"] is None
    assert client.get(f"/reconstructions/{job_id}/scene").status_code == 409
    assert client.get(f"/reconstructions/{job_id}/manifest").status_code == 409

    review = fetch_room_types(client, job_id)
    assert len(review["rooms"]) == 4
    assert review["requires_confirmation"] is True
    assert {label["value"] for label in review["labels"]} >= {
        "living_room",
        "bedroom",
        "kitchen",
        "bathroom",
        "entrance",
    }

    confirm_room_types(
        client,
        job_id,
        ["living_room", "kitchen", "bedroom", "bathroom"],
        style="scandinavian",
        variant=1,
    )
    job = client.get(f"/reconstructions/{job_id}").json()
    assert job["status"] == "completed", job.get("error")
    assert job["geometry_valid"] is True
    assert job["navigation_available"] is True
    assert job["room_types_confirmed"] is True
    assert job["room_count"] == 4
    assert job["furniture_count"] > 0
    assert job["furnishing_style"] == "scandinavian"
    assert job["furnishing_variant"] == 1
    assert job["scene_url"]
    assert job["manifest_url"]
    assert job["furniture_url"]
    assert not any(
        "photo" in limitation.lower() for limitation in job["limitations"]
    ), f"Floor-plan-only limitations must not mention photographs: {job['limitations']}"

    stages = [record["stage"] for record in job["stage_records"]]
    assert stages == EXPECTED_STAGES, stages

    confirmed_types = {entry["room_id"]: entry["room_type"] for entry in job["room_types"]}
    assert confirmed_types == {
        0: "living_room",
        1: "kitchen",
        2: "bedroom",
        3: "bathroom",
    }
    assert all(entry["source"] == "user" for entry in job["room_types"])

    scene = scene_bundle(client, job)
    assert scene["workflow"] == "floor_plan_only"
    assert scene["property_id"] == "prop-floor-plan-only"
    nodes = {node["id"]: node for node in scene["room_graph"]["nodes"]}
    assert set(nodes) == {0, 1, 2, 3}
    assert scene["room_graph"]["connected"] is True
    assert all(node["furniture_count"] > 0 for node in nodes.values())
    assert all(node["photo_count"] == 0 for node in nodes.values())
    assert nodes[0]["name"] == "Living Room"
    assert nodes[1]["name"] == "Kitchen"
    assert nodes[2]["name"] == "Bedroom 1"
    assert nodes[3]["name"] == "Bathroom 1"

    items = scene["furniture"]
    assert isinstance(items, list)
    assert len(items) == job["furniture_count"]
    furniture = client.get(job["furniture_url"])
    assert furniture.status_code == 200, furniture.text
    furniture_doc = furniture.json()
    assert len(furniture_doc["items"]) == len(items)
    assert furniture_doc["style"] == "scandinavian"
    assert furniture_doc["variant"] == 1

    seen_ids = set()
    for item in items:
        for key in (
            "id",
            "room_id",
            "asset_id",
            "model_id",
            "position",
            "rotation_deg",
            "scale",
            "size_m",
            "bbox",
            "materials",
            "placement",
            "status",
        ):
            assert key in item, (key, item)
        assert item["id"] not in seen_ids
        seen_ids.add(item["id"])
        assert item["room_id"] in nodes
        assert item["room_type"] == nodes[item["room_id"]]["room_type"]
        assert item["status"] == "placed"
        assert item["material_style"] == "scandinavian"
        node_items = {
            entry["id"] for entry in nodes[item["room_id"]]["furniture"]
        }
        assert item["id"] in node_items

    collision = scene["collision"]
    assert collision["walls"]
    assert collision["spawn"]["position"]
    collision_items = furniture_collision_items(furniture_doc)
    assert len(collision["furniture"]) == len(collision_items)
    assert {entry["item_id"] for entry in collision["furniture"]} == {
        item["id"] for item in items if item["collision"]
    }

    glb = client.get(f"/reconstructions/{job_id}/assets/scene.glb")
    assert glb.status_code == 200
    glb_json = read_glb_json(glb.content)
    assert glb_json.get("images", []) == []
    node_names = [node.get("name", "") for node in glb_json.get("nodes", [])]
    assert any(name.startswith("Furniture_") for name in node_names)
    exported_items = {
        name.split("__", 1)[0].removeprefix("Furniture_")
        for name in node_names
        if name.startswith("Furniture_")
    }
    assert exported_items == seen_ids

    manifest = client.get(job["manifest_url"]).json()
    assert manifest["schema_version"] == "3.0"
    assert manifest["workflow"] == "floor_plan_only"
    assert manifest["property_id"] == "prop-floor-plan-only"
    assert manifest["provenance"]["engine"]["photographs_used"] is False
    assert len(manifest["room_types"]) == 4
    assert len(manifest["furniture"]) == len(items)
    assert manifest["validation"]["geometry_valid"] is True
    assert "photo_manifest" not in manifest
    assert [record["stage"] for record in manifest["provenance"]["stages"]] == (
        EXPECTED_STAGES[:-1]
    )
    assert manifest["provenance"]["inputs"]["room_types_confirmed"] is True


def test_room_type_review_returns_plan_geometry_for_visual_selection(client, fixture_plan):
    response = upload(client, fixture_plan.read_bytes())
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]
    review = fetch_room_types(client, job_id)

    assert review["image_dimensions"] == [1000, 800]
    assert review["footprint_px"]["width"] > 0
    assert review["footprint_px"]["height"] > 0
    assert review["scale_pixels_per_meter"] > 0
    assert review["normalized_floor_plan_url"] == (
        f"/reconstructions/{job_id}/diagnostics/floor_plan_normalized.png"
    )
    normalized = client.get(review["normalized_floor_plan_url"])
    assert normalized.status_code == 200, normalized.text
    assert normalized.headers["content-type"].startswith("image/")
    original = client.get(review["floor_plan_url"])
    assert original.status_code == 200, original.text
    assert original.headers["content-type"].startswith("image/")

    width_px, height_px = review["image_dimensions"]
    assert len(review["rooms"]) == 4
    seen_polygons = set()
    for room in review["rooms"]:
        x, y, width, height = room["bounds_px"]
        assert 0 <= x < x + width <= width_px, room
        assert 0 <= y < y + height <= height_px, room
        polygon = room["polygon_px"]
        assert polygon is not None and len(polygon) >= 3, room
        for px, py in polygon:
            assert 0 <= px <= width_px and 0 <= py <= height_px, room
        seen_polygons.add(json.dumps(polygon))
        center_x, center_y = room["center_px"]
        assert x <= center_x <= x + width, room
        assert y <= center_y <= y + height, room
        assert room["area_m2"] > 0, room
        assert len(room["polygon_m"]) >= 3, room
        assert room["room_type"], room
    assert len(seen_polygons) == 4


def test_thick_wall_interior_doorway_is_bridged_in_navigation_grid():
    validator = SceneValidator()
    polygons = {
        0: shapely_box(0.0, 0.0, 2.0, 3.0),
        1: shapely_box(2.5, 0.0, 4.5, 3.0),
    }
    walls = [
        {"min": [2.0, 0.0], "max": [2.5, 1.05]},
        {"min": [2.0, 1.95], "max": [2.5, 3.0]},
        {"min": [0.0, 0.0], "max": [4.5, 0.1]},
        {"min": [0.0, 2.9], "max": [4.5, 3.0]},
        {"min": [0.0, 0.0], "max": [0.1, 3.0]},
        {"min": [4.4, 0.0], "max": [4.5, 3.0]},
    ]
    door = {
        "id": "door-0-1",
        "orientation": "vertical",
        "center_m": [2.25, 1.5],
        "width_m": 0.9,
        "rooms": [0, 1],
    }

    grid = validator._build_navigation_grid(walls, [], polygons, [door])
    assert grid is not None
    xs, zs, free = grid
    reached = flood_from(free, xs, zs, (1.0, 1.5))
    far_row, far_col = grid_cell(xs, zs, (3.6, 1.5))
    assert reached[far_row, far_col], "Both rooms must be reachable through the doorway"
    door_row, door_col = grid_cell(xs, zs, (2.25, 1.5))
    assert free[door_row, door_col], "The thick wall band must stay walkable at the doorway"

    grid_without_door = validator._build_navigation_grid(walls, [], polygons, [])
    assert grid_without_door is not None
    plain_xs, plain_zs, plain_free = grid_without_door
    plain_reached = flood_from(plain_free, plain_xs, plain_zs, (1.0, 1.5))
    plain_far_row, plain_far_col = grid_cell(plain_xs, plain_zs, (3.6, 1.5))
    assert not plain_reached[plain_far_row, plain_far_col], (
        "Without the interior-door connector the room polygons alone cannot bridge a thick wall"
    )

    patches = validator._door_connector_patches(
        [
            {**door, "exterior": True},
            {**door, "rooms": [0, None]},
            door,
        ]
    )
    assert len(patches) == 1, "Exterior and single-sided doors must not add interior connectors"


def test_room_type_confirmation_rejects_incomplete_or_invalid_input(
    client, fixture_plan
):
    response = upload(client, fixture_plan.read_bytes())
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]
    review = fetch_room_types(client, job_id)
    rooms = sorted(review["rooms"], key=lambda entry: int(entry["room_id"]))
    ids = [int(room["room_id"]) for room in rooms]
    complete = [
        {"room_id": room_id, "room_type": "living_room"} for room_id in ids
    ]

    def post(payload):
        return client.post(f"/reconstructions/{job_id}/room-types", json=payload)

    missing = post({"rooms": complete[:-1]})
    assert missing.status_code == 400
    assert "missing room" in missing.json()["detail"]

    duplicated = post({"rooms": complete + [complete[0]]})
    assert duplicated.status_code == 400
    assert "more than once" in duplicated.json()["detail"]

    unknown_type = post(
        {
            "rooms": [
                {**entry, "room_type": "garage"}
                for entry in complete
            ]
        }
    )
    assert unknown_type.status_code == 400
    assert "Unsupported room type" in unknown_type.json()["detail"]

    unknown_room = post(
        {"rooms": [{"room_id": 999, "room_type": "living_room"}] + complete}
    )
    assert unknown_room.status_code == 400
    assert "does not exist" in unknown_room.json()["detail"]

    bad_style = post({"rooms": complete, "style": "baroque"})
    assert bad_style.status_code == 400
    assert "Unsupported furnishing style" in bad_style.json()["detail"]

    bad_variant = post({"rooms": complete, "variant": 999})
    assert bad_variant.status_code == 400
    assert "variant must be between" in bad_variant.json()["detail"]

    premature = client.post(
        f"/reconstructions/{job_id}/furnishings", json={"style": "modern"}
    )
    assert premature.status_code == 409
    assert "Confirm room types" in premature.json()["detail"]

    pending = client.get(f"/reconstructions/{job_id}").json()
    assert pending["status"] == "awaiting_room_types"
    assert pending["room_types_confirmed"] is False
    assert pending["furniture_count"] == 0
    assert pending["scene_url"] is None

    confirm_room_types(client, job_id, ["living_room", "kitchen", "bedroom", "bathroom"])
    assert client.get(f"/reconstructions/{job_id}").json()["status"] == "completed"


def test_unknown_room_stays_unfurnished_until_the_type_changes(client, fixture_plan):
    job_id, _, job = complete_job(
        client,
        fixture_plan.read_bytes(),
        ["living_room", "kitchen", "bedroom", "unknown"],
    )
    scene = scene_bundle(client, job)
    unknown_node = next(
        node for node in scene["room_graph"]["nodes"] if node["room_type"] == "unknown"
    )
    assert unknown_node["furniture_count"] == 0
    assert unknown_node["furniture"] == []
    assert unknown_node["furnishing_status"] == "unspecified"
    assert any("no confirmed room type" in warning for warning in job["warnings"])
    assert scene["furnishing"]["summary"]["rooms_unspecified"] == 1
    for item in scene["furniture"]:
        assert item["room_id"] != unknown_node["id"]

    baseline_architecture = scene["collision"]["walls"]
    baseline_bounds = {
        node["id"]: node["bounds"] for node in scene["room_graph"]["nodes"]
    }

    changed = client.patch(
        f"/reconstructions/{job_id}/rooms/{unknown_node['id']}",
        json={"room_type": "bedroom", "name": "Guest Bedroom"},
    )
    assert changed.status_code == 200, changed.text
    retyped = client.get(f"/reconstructions/{job_id}").json()
    assert retyped["status"] == "completed", retyped.get("error")
    assert retyped["furniture_count"] > job["furniture_count"]

    updated_scene = scene_bundle(client, retyped)
    updated_node = next(
        node
        for node in updated_scene["room_graph"]["nodes"]
        if node["id"] == unknown_node["id"]
    )
    assert updated_node["room_type"] == "bedroom"
    assert updated_node["name"] == "Guest Bedroom"
    assert updated_node["furniture_count"] > 0
    assert any(
        item["asset_id"].startswith("bed_") for item in updated_node["furniture"]
    )
    assert updated_scene["collision"]["walls"] == baseline_architecture
    assert {
        node["id"]: node["bounds"] for node in updated_scene["room_graph"]["nodes"]
    } == baseline_bounds


def test_style_change_keeps_architecture_and_updates_materials(client, fixture_plan):
    job_id, _, job = complete_job(
        client,
        fixture_plan.read_bytes(),
        ["living_room", "kitchen", "bedroom", "bathroom"],
    )
    before = scene_bundle(client, job)
    before_bounds = {
        node["id"]: node["bounds"] for node in before["room_graph"]["nodes"]
    }
    before_edges = before["room_graph"]["edges"]
    before_walls = before["collision"]["walls"]
    before_materials = client.get(job["furniture_url"]).json()["materials"]

    response = client.post(
        f"/reconstructions/{job_id}/furnishings", json={"style": "minimalist"}
    )
    assert response.status_code == 200, response.text
    restyled = client.get(f"/reconstructions/{job_id}").json()
    assert restyled["status"] == "completed", restyled.get("error")
    assert restyled["furnishing_style"] == "minimalist"
    assert restyled["furniture_count"] > 0

    after = scene_bundle(client, restyled)
    assert after["collision"]["walls"] == before_walls
    assert after["room_graph"]["edges"] == before_edges
    assert {
        node["id"]: node["bounds"] for node in after["room_graph"]["nodes"]
    } == before_bounds
    assert all(item["material_style"] == "minimalist" for item in after["furniture"])
    after_materials = client.get(restyled["furniture_url"]).json()["materials"]
    assert after_materials != before_materials
    assert after["furnishing"]["style"] == "minimalist"

    regenerated = client.post(
        f"/reconstructions/{job_id}/furnishings", json={"regenerate": True}
    )
    assert regenerated.status_code == 200, regenerated.text
    variant_job = client.get(f"/reconstructions/{job_id}").json()
    assert variant_job["status"] == "completed", variant_job.get("error")
    assert variant_job["furnishing_variant"] == 1
    variant_scene = scene_bundle(client, variant_job)
    assert variant_scene["collision"]["walls"] == before_walls
    assert {
        node["id"]: node["bounds"] for node in variant_scene["room_graph"]["nodes"]
    } == before_bounds


def test_six_room_plan_furnishes_every_room_by_type(client):
    job_id, review, job = complete_job(
        client,
        create_six_room_plan(),
        ["entrance", "living_room", "kitchen", "bedroom", "bedroom", "bathroom"],
        known_width="12",
        known_length="9",
        property_id="prop-six-rooms",
    )
    assert len(review["rooms"]) == 6
    assert job["room_count"] == 6
    scene = scene_bundle(client, job)
    nodes = {node["id"]: node for node in scene["room_graph"]["nodes"]}
    assert len(nodes) == 6
    assert scene["room_graph"]["connected"] is True
    assert job["navigation_available"] is True

    assert "shoe_cabinet" in asset_ids_in(scene["furniture"], 0)
    assert {"tv_stand", "television", "sofa", "coffee_table"} <= asset_ids_in(
        scene["furniture"], 1
    )
    kitchen_assets = asset_ids_in(scene["furniture"], 2)
    assert any(asset.startswith("kitchen_counter_") for asset in kitchen_assets)
    assert {"kitchen_sink", "kitchen_stove", "refrigerator"} <= kitchen_assets
    for bedroom_id in (3, 4):
        bedroom_assets = asset_ids_in(scene["furniture"], bedroom_id)
        assert any(asset.startswith("bed_") for asset in bedroom_assets)
        assert "wardrobe" in bedroom_assets or "wardrobe_wide" in bedroom_assets
    bathroom_assets = asset_ids_in(scene["furniture"], 5)
    assert {"toilet", "vanity"} <= bathroom_assets
    assert bathroom_assets & {"shower", "bathtub"}

    assert nodes[3]["name"] == "Bedroom 1"
    assert nodes[4]["name"] == "Bedroom 2"
    assert nodes[5]["name"] == "Bathroom 1"
    assert all(node["furniture_count"] > 0 for node in nodes.values())

    glb = client.get(f"/reconstructions/{job_id}/assets/scene.glb")
    glb_json = read_glb_json(glb.content)
    exported = {
        node["name"].split("__", 1)[0].removeprefix("Furniture_")
        for node in glb_json["nodes"]
        if node.get("name", "").startswith("Furniture_")
    }
    assert exported == {item["id"] for item in scene["furniture"]}


def test_two_properties_produce_independent_furnished_scenes(client, fixture_plan):
    first_id, _, first_job = complete_job(
        client,
        fixture_plan.read_bytes(),
        ["living_room", "kitchen", "bedroom", "bathroom"],
        property_id="prop-first",
    )
    second_id, _, second_job = complete_job(
        client,
        create_six_room_plan(),
        ["entrance", "living_room", "kitchen", "bedroom", "bedroom", "bathroom"],
        known_width="12",
        known_length="9",
        property_id="prop-second",
    )
    assert first_id != second_id
    assert first_job["property_id"] == "prop-first"
    assert second_job["property_id"] == "prop-second"
    assert first_job["room_count"] == 4
    assert second_job["room_count"] == 6

    first_scene = scene_bundle(client, first_job)
    second_scene = scene_bundle(client, second_job)
    assert first_scene["job_id"] == first_id
    assert second_scene["job_id"] == second_id
    assert first_scene["model_url"] != second_scene["model_url"]
    assert len(first_scene["room_graph"]["nodes"]) != len(
        second_scene["room_graph"]["nodes"]
    )
    first_glb = client.get(f"/reconstructions/{first_id}/assets/scene.glb").content
    second_glb = client.get(f"/reconstructions/{second_id}/assets/scene.glb").content
    assert hashlib.sha256(first_glb).hexdigest() != hashlib.sha256(second_glb).hexdigest()
    assert {
        item["id"] for item in first_scene["furniture"]
    } != {item["id"] for item in second_scene["furniture"]}


def synthetic_room(
    room_id: int,
    bounds,
    *,
    polygon=None,
    center=None,
):
    x, z, width, depth = bounds
    area = width * depth
    room = {
        "id": room_id,
        "bounds_m": list(bounds),
        "center_m": center or [x + width / 2, z + depth / 2],
        "area_m2": area,
    }
    if polygon:
        room["polygon_m"] = polygon
    return room


def synthetic_door(room_ids, orientation, center, width=0.8):
    return {
        "id": f"door-{'-'.join(str(room_id) for room_id in room_ids if room_id is not None)}",
        "kind": "door",
        "orientation": orientation,
        "center_m": list(center),
        "width_m": width,
        "rooms": list(room_ids),
    }


def furnish_single(room_type, bounds, *, polygon=None, door=None):
    room = synthetic_room(0, bounds, polygon=polygon)
    doors = [door] if door else []
    metadata = {0: {"room_type": room_type, "name": room_type.replace("_", " ").title()}}
    furnishing = furnish_floor_plan([room], metadata, doors, [], "modern", 0)
    validation = validate_furnishing(furnishing, [room], doors, [])
    assert validation["valid"], validation["issues"]
    assert validation["navigation"]["0"]["reachable"] is True
    return room, furnishing


def test_small_bedroom_prioritises_bed_and_omits_optional_furniture():
    room, furnishing = furnish_single(
        "bedroom",
        [0, 0, 2.3, 3.4],
        door=synthetic_door([0, None], "horizontal", [0.45, 3.4], width=0.7),
    )
    assets = asset_ids_in(furnishing, 0)
    assert "bed_single" in assets
    assert "desk" not in assets
    assert "rug" not in assets
    entry = furnishing["rooms"][0]
    assert entry["status"] in {"furnished", "partially_furnished"}
    for item in furnishing["items"]:
        footprint = item["footprint"]
        assert room["bounds_m"][0] <= footprint["min"][0]
        assert room["bounds_m"][1] <= footprint["min"][1]
        assert footprint["max"][0] <= room["bounds_m"][0] + room["bounds_m"][2]
        assert footprint["max"][1] <= room["bounds_m"][1] + room["bounds_m"][3]


def test_narrow_hallway_stays_clear_of_furniture():
    _, furnishing = furnish_single(
        "hallway",
        [0, 0, 1.1, 4.0],
        door=synthetic_door([0, None], "vertical", [1.1, 2.0]),
    )
    assert furnishing["items"] == []
    entry = furnishing["rooms"][0]
    assert entry["status"] == "unfurnished"
    assert any("kept clear" in note.lower() for note in entry["notes"])


def test_l_shaped_living_room_keeps_furniture_inside_the_polygon():
    polygon = [
        [0, 0],
        [4.0, 0],
        [4.0, 3.0],
        [2.5, 3.0],
        [2.5, 5.5],
        [0, 5.5],
    ]
    room, furnishing = furnish_single(
        "living_room",
        [0, 0, 4.0, 5.5],
        polygon=polygon,
        door=synthetic_door([0, None], "horizontal", [1.5, 5.5]),
    )
    assets = asset_ids_in(furnishing, 0)
    assert {"tv_stand", "television", "sofa", "coffee_table"} <= assets
    assert furnishing["summary"]["items"] == len(furnishing["items"])
    assert all(item["footprint"]["max"][0] <= 4.0 for item in furnishing["items"])


@pytest.mark.parametrize(
    "room_type,bounds,required,one_of",
    [
        (
            "living_room",
            [0, 0, 5.0, 6.0],
            {"tv_stand", "television", "sofa", "coffee_table"},
            set(),
        ),
        (
            "kitchen",
            [0, 0, 4.5, 4.5],
            {"kitchen_sink", "kitchen_stove", "refrigerator"},
            {"kitchen_counter_120", "kitchen_counter_180", "kitchen_counter_240"},
        ),
        (
            "bedroom",
            [0, 0, 4.0, 4.5],
            {"nightstand"},
            {"bed_double", "bed_queen", "bed_king"},
        ),
        ("bathroom", [0, 0, 2.6, 3.2], {"toilet", "vanity"}, {"shower", "bathtub"}),
        (
            "dining_room",
            [0, 0, 5.5, 5.5],
            {"dining_chair"},
            {"dining_table_4", "dining_table_6"},
        ),
        ("entrance", [0, 0, 2.4, 2.8], {"shoe_cabinet"}, set()),
    ],
)
def test_required_furniture_categories_per_room_type(
    room_type, bounds, required, one_of
):
    _, furnishing = furnish_single(
        room_type,
        bounds,
        door=synthetic_door([0, None], "horizontal", [bounds[2] / 2, bounds[3]]),
    )
    assets = asset_ids_in(furnishing, 0)
    assert required <= assets, assets
    if one_of:
        assert assets & one_of, assets
    assert furnishing["items"]
    assert all(item["room_id"] == 0 for item in furnishing["items"])


def test_furnishing_is_deterministic_and_never_blocks_doorways():
    room = synthetic_room(0, [0, 0, 5.0, 6.0])
    doors = [synthetic_door([0, None], "horizontal", [2.5, 6.0])]
    metadata = {0: {"room_type": "living_room", "name": "Living Room"}}

    first = furnish_floor_plan([room], metadata, doors, [], "modern", 0)
    second = furnish_floor_plan([room], metadata, doors, [], "modern", 0)
    assert first["items"] == second["items"]
    assert first["rooms"] == second["rooms"]

    validation = validate_furnishing(first, [room], doors, [])
    assert validation["valid"], validation["issues"]
    zones = door_clearance_zones(doors)
    for item in first["items"]:
        if not item["collision"]:
            continue
        footprint = item["footprint"]
        for zone in zones:
            separated = (
                footprint["max"][0] <= zone["min"][0]
                or footprint["min"][0] >= zone["max"][0]
                or footprint["max"][1] <= zone["min"][1]
                or footprint["min"][1] >= zone["max"][1]
            )
            assert separated, (item["id"], zone)


def test_every_style_furnishes_without_changing_room_geometry():
    room = synthetic_room(0, [0, 0, 4.5, 5.0])
    door = synthetic_door([0, None], "horizontal", [2.25, 5.0])
    metadata = {0: {"room_type": "living_room", "name": "Living Room"}}
    material_sets = {}
    for style in STYLES:
        furnishing = furnish_floor_plan([room], metadata, [door], [], style, 0)
        assert furnishing["style"] == style
        assert all(item["material_style"] == style for item in furnishing["items"])
        material_sets[style] = furnishing["materials"]
        validation = validate_furnishing(furnishing, [room], [door], [])
        assert validation["valid"], validation["issues"]
    assert len({json.dumps(materials, sort_keys=True) for materials in material_sets.values()}) == len(
        STYLES
    )
