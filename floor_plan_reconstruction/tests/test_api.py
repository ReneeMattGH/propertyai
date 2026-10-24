"""HTTP API, job lifecycle and end-to-end pipeline tests."""
from __future__ import annotations

import hashlib
import io
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import main


def make_job_id(prefix: str = "job") -> str:
    return f"pytest-{prefix}-{uuid.uuid4().hex[:12]}"


def seed_job(job_id: str, **overrides) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    job = {
        "id": job_id,
        "status": "queued",
        "current_stage": "queued",
        "progress": 0.0,
        "created_at": now,
        "updated_at": now,
        "error": None,
        "project_name": "Seeded test job",
        "floor_plan_filename": "plan.png",
        "photo_count": 1,
        "num_floors": 1,
        "known_dimensions": {},
        "diagnostics": {},
        "assets": [],
        "warnings": [],
        "limitations": [],
        "retry_count": 0,
        "cancel_requested": False,
        "floor_plan_path": "",
        "photo_paths": [],
    }
    job.update(overrides)
    with main._JOBS_LOCK:
        jobs = main._read_jobs_unlocked()
        jobs[job_id] = job
        main._write_jobs_unlocked(jobs)
    return job


def upload(client, plan_bytes: bytes, plan_name="plan.png", photos=None, **form):
    files = [("floor_plan", (plan_name, plan_bytes, "application/octet-stream"))]
    for name, content in photos or []:
        files.append(("photos", (name, content, "image/jpeg")))
    data = {"project_name": "API test property", **form}
    return client.post("/reconstructions", data=data, files=files)


def fetch_room_types(client, job_id: str) -> dict:
    response = client.get(f"/reconstructions/{job_id}/room-types")
    assert response.status_code == 200, response.text
    return response.json()


def make_room_selections(review: dict, room_types: list[str] | None = None) -> list[dict]:
    rooms = sorted(review["rooms"], key=lambda entry: int(entry["room_id"]))
    assert rooms
    selections = []
    for index, room in enumerate(rooms):
        room_type = (
            room_types[index] if room_types and index < len(room_types) else room["room_type"]
        )
        selections.append(
            {
                "room_id": int(room["room_id"]),
                "room_type": room_type,
                "name": room.get("name") if room_type == room.get("room_type") else "",
            }
        )
    return selections


def confirm_room_types(
    client,
    job_id: str,
    room_types: list[str] | None = None,
    style: str | None = None,
    variant: int | None = None,
):
    review = fetch_room_types(client, job_id)
    payload: dict = {"rooms": make_room_selections(review, room_types)}
    if style:
        payload["style"] = style
    if variant is not None:
        payload["variant"] = variant
    response = client.post(f"/reconstructions/{job_id}/room-types", json=payload)
    assert response.status_code == 200, response.text
    return response, review


def upload_confirm_and_complete(
    client,
    plan_bytes: bytes,
    room_types: list[str] | None = None,
    style: str | None = None,
    variant: int | None = None,
    **form,
):
    response = upload(client, plan_bytes, **form)
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]
    pending = client.get(f"/reconstructions/{job_id}").json()
    assert pending["status"] == "awaiting_room_types", pending.get("error")
    confirm, review = confirm_room_types(
        client, job_id, room_types=room_types, style=style, variant=variant
    )
    return job_id, review, confirm


def create_two_room_plan() -> bytes:
    image = Image.new("RGB", (800, 600), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([60, 60, 740, 540], outline="black", width=10)
    draw.rectangle([395, 60, 405, 270], fill="black")
    draw.rectangle([395, 340, 405, 540], fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def read_glb_chunks(content: bytes):
    assert content[:4] == b"glTF"
    offset = 12
    gltf = None
    binary = b""
    while offset < len(content):
        length = int.from_bytes(content[offset:offset + 4], "little")
        kind = content[offset + 4:offset + 8]
        payload = content[offset + 8:offset + 8 + length]
        if kind == b"JSON":
            gltf = json.loads(payload.decode("utf-8"))
        elif kind == b"BIN\x00":
            binary = payload
        offset += 8 + length
    assert gltf is not None
    return gltf, binary


def test_rejects_blank_project_name(client, fixture_plan):
    response = upload(client, fixture_plan.read_bytes(), project_name="   ")
    assert response.status_code == 400
    assert "Project name" in response.json()["detail"]


def test_rejects_unsupported_floor_plan_extension(client):
    response = upload(client, b"whatever", plan_name="plan.txt")
    assert response.status_code == 400
    assert "Unsupported floor plan format" in response.json()["detail"]


def test_photos_are_optional_and_ignored(client, fixture_plan):
    response = upload(
        client,
        fixture_plan.read_bytes(),
        photos=[("empty.jpg", b""), ("not-an-image.jpg", b"not an image")],
    )
    assert response.status_code == 201, response.text
    job = client.get(f"/reconstructions/{response.json()['job_id']}").json()
    assert job["status"] == "awaiting_room_types", job.get("error")
    assert job["photo_count"] == 0
    assert job["inputs"]["photos"] == []
    assert job["inputs"]["photos_ignored"] == 2
    assert client.get(f"/reconstructions/{job['job_id']}/review").status_code == 409


def test_rejects_oversized_floor_plan(client, fixture_plan, monkeypatch):
    monkeypatch.setattr(main, "MAX_FLOOR_PLAN_BYTES", 1024)
    response = upload(client, fixture_plan.read_bytes())
    assert response.status_code == 400
    assert "too large" in response.json()["detail"].lower()


def test_rejects_out_of_range_dimensions(client, fixture_plan):
    response = upload(client, fixture_plan.read_bytes(), known_width="0.5")
    assert response.status_code == 400
    assert "width" in response.json()["detail"].lower()

    response = upload(client, fixture_plan.read_bytes(), known_length="500")
    assert response.status_code == 400
    assert "length" in response.json()["detail"].lower()


def test_floor_plan_only_upload_does_not_apply_photo_limits(
    client, fixture_plan, monkeypatch
):
    monkeypatch.setattr(main, "MAX_PHOTOS", 2)
    response = upload(
        client,
        fixture_plan.read_bytes(),
        photos=[("a.jpg", b"a"), ("b.jpg", b"b"), ("c.jpg", b"c")],
    )
    assert response.status_code == 201, response.text
    job = client.get(f"/reconstructions/{response.json()['job_id']}").json()
    assert job["status"] == "awaiting_room_types", job.get("error")
    assert job["inputs"]["photos"] == []
    assert job["inputs"]["photos_ignored"] == 3


def test_rejects_an_unknown_furnishing_style(client, fixture_plan):
    response = upload(client, fixture_plan.read_bytes(), style="baroque")
    assert response.status_code == 400
    assert "style" in response.json()["detail"].lower()


def test_unknown_job_returns_404(client):
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/reconstructions/{missing}").status_code == 404
    assert client.get(f"/reconstructions/{missing}/scene").status_code == 404
    assert client.get(f"/reconstructions/{missing}/manifest").status_code == 404
    assert client.get(f"/reconstructions/{missing}/logs").status_code == 404
    assert client.post(f"/reconstructions/{missing}/cancel").status_code == 404
    assert client.post(f"/reconstructions/{missing}/retry").status_code == 404
    assert client.get(f"/reconstructions/{missing}/assets/scene.glb").status_code == 404


def test_manifest_and_scene_gated_until_completed(client, stub_pipeline, fixture_plan):
    response = upload(client, fixture_plan.read_bytes())
    assert response.status_code == 201
    job_id = response.json()["job_id"]
    assert response.json()["status"] == "queued"

    manifest = client.get(f"/reconstructions/{job_id}/manifest")
    assert manifest.status_code == 409
    assert "Manifest not available" in manifest.json()["detail"]

    scene = client.get(f"/reconstructions/{job_id}/scene")
    assert scene.status_code == 409
    assert "Scene not available" in scene.json()["detail"]

    report = client.get(f"/reconstructions/{job_id}/report")
    assert report.status_code == 200
    assert report.json()["geometry_valid"] is None


def test_cancel_marks_requested_and_terminal_jobs_conflict(client, stub_pipeline, fixture_plan):
    response = upload(client, fixture_plan.read_bytes())
    job_id = response.json()["job_id"]

    cancel = client.post(f"/reconstructions/{job_id}/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["cancel_requested"] is True

    completed_id = make_job_id("completed")
    seed_job(completed_id, status="completed", progress=100.0)
    conflict = client.post(f"/reconstructions/{completed_id}/cancel")
    assert conflict.status_code == 409
    assert "already completed" in conflict.json()["detail"]


def test_retry_rejects_non_terminal_jobs(client, stub_pipeline, fixture_plan):
    response = upload(client, fixture_plan.read_bytes())
    job_id = response.json()["job_id"]

    retry = client.post(f"/reconstructions/{job_id}/retry")
    assert retry.status_code == 409
    assert "partially navigable" in retry.json()["detail"]


def test_retry_requires_the_original_inputs(client, stub_pipeline):
    job_id = make_job_id("missing-inputs")
    seed_job(
        job_id,
        status="failed",
        error="boom",
        floor_plan_path="/tmp/does-not-exist/plan.png",
        photo_paths=["/tmp/does-not-exist/photo.jpg"],
    )

    retry = client.post(f"/reconstructions/{job_id}/retry")
    assert retry.status_code == 409
    assert "no longer available" in retry.json()["detail"]


def test_retry_restarts_a_failed_job(client, stub_pipeline, tmp_path):
    plan = tmp_path / "plan.png"
    photo = tmp_path / "photo.jpg"
    plan.write_bytes(b"plan")
    photo.write_bytes(b"photo")
    job_id = make_job_id("retry")
    seed_job(
        job_id,
        status="failed",
        error="previous failure",
        progress=100.0,
        floor_plan_path=str(plan),
        photo_paths=[str(photo)],
        warnings=["stale warning"],
    )

    retry = client.post(f"/reconstructions/{job_id}/retry")
    assert retry.status_code == 200
    payload = retry.json()
    assert payload["status"] == "queued"
    assert payload["retry_count"] == 1
    assert payload["warnings"] == []
    assert payload["error"] is None

    stored = client.get(f"/reconstructions/{job_id}").json()
    assert stored["retry_count"] == 1
    assert stored["cancel_requested"] is False


def test_retry_accepts_preprocessing_options(client, stub_pipeline, tmp_path):
    plan = tmp_path / "plan.png"
    photo = tmp_path / "photo.jpg"
    plan.write_bytes(b"plan")
    photo.write_bytes(b"photo")

    job_id = make_job_id("preprocessing-retry")
    seed_job(
        job_id,
        status="failed",
        error="no enclosed rooms",
        error_code="no_enclosed_rooms",
        failed_stage="detecting_rooms",
        progress=15.0,
        floor_plan_path=str(plan),
        photo_paths=[str(photo)],
    )
    response = client.post(
        f"/reconstructions/{job_id}/retry",
        json={"preprocessing": {"wall_polarity": "light", "sealing": "aggressive"}},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["progress"] == 0.0
    assert payload["error"] is None
    assert payload["failed_stage"] is None

    stored = json.loads(main.JOBS_FILE.read_text())[job_id]
    assert stored["retry_preprocessing"] == {
        "wall_polarity": "light",
        "sealing": "aggressive",
    }

    invalid_id = make_job_id("invalid-preprocessing")
    seed_job(
        invalid_id,
        status="failed",
        error="no enclosed rooms",
        floor_plan_path=str(plan),
        photo_paths=[str(photo)],
    )
    invalid = client.post(
        f"/reconstructions/{invalid_id}/retry",
        json={"preprocessing": {"wall_polarity": "sideways"}},
    )
    assert invalid.status_code == 400
    assert "wall_polarity" in invalid.json()["detail"]

    unknown = client.post(
        f"/reconstructions/{invalid_id}/retry",
        json={"preprocessing": {"threshold": "low"}},
    )
    assert unknown.status_code == 400
    assert "Unknown preprocessing option" in unknown.json()["detail"]


def test_manual_plan_endpoint_corrects_a_failed_job(
    client, fixture_photos, tmp_path
):
    plan = Image.new("RGB", (900, 700), "white")
    draw = ImageDraw.Draw(plan)
    draw.rectangle([60, 60, 840, 640], outline="black", width=10)
    draw.rectangle([60, 630, 840, 640], fill="white")
    plan_path = tmp_path / "open-room.png"
    plan.save(plan_path)

    response = upload(
        client,
        plan_path.read_bytes(),
        photos=[("room.jpg", fixture_photos[0].read_bytes())],
        known_width="12",
        known_length="9",
        property_id="prop-manual-correction",
    )
    assert response.status_code == 201
    job_id = response.json()["job_id"]
    failed_job = client.get(f"/reconstructions/{job_id}").json()
    assert failed_job["status"] == "failed"

    diagnostics = client.get(failed_job["diagnostics_url"]).json()
    assert diagnostics["manual_plan_supported"] is True
    assert diagnostics["parser"]["error_code"]

    correction = {
        "rooms": [
            {"polygon_px": [[60, 60], [450, 60], [450, 640], [60, 640]]},
            {"polygon_px": [[450, 60], [840, 60], [840, 640], [450, 640]]},
        ],
        "doorways": [
            {"orientation": "vertical", "center_px": [450, 350], "width_px": 60}
        ],
        "wall_thickness_px": 10,
    }
    corrected = client.post(
        f"/reconstructions/{job_id}/manual-plan", json=correction
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["status"] == "queued"

    pending = client.get(f"/reconstructions/{job_id}").json()
    assert pending["status"] == "awaiting_room_types", pending.get("error")
    review = fetch_room_types(client, job_id)
    assert len(review["rooms"]) == 2
    assert review["floor_plan_url"] == f"/reconstructions/{job_id}/floor-plan"
    room_id = sorted(room["room_id"] for room in review["rooms"])[0]
    confirmed, _ = confirm_room_types(
        client, job_id, room_types=["bathroom", "living_room"]
    )
    assert confirmed.json()["status"] == "queued"

    job = client.get(f"/reconstructions/{job_id}").json()
    assert job["status"] == "completed", job.get("error")
    assert job["geometry_valid"] is True
    assert job["navigation_available"] is True
    assert job["scene_url"]
    assert job["retry_count"] == 1
    assert job["room_types_confirmed"] is True
    assert job["furniture_count"] > 0
    assert job["manual_outline"]["doorways"][0]["orientation"] == "vertical"

    diagnostics = client.get(job["diagnostics_url"]).json()
    assert diagnostics["parser"]["segmentation"]["source"] == "manual_outline"
    assert diagnostics["failure_report"]["error_code"] == ("no_enclosed_rooms")
    assert diagnostics["furniture_count"] == job["furniture_count"]
    assert diagnostics["furnishing_style"] == "modern"
    assert diagnostics["room_types_confirmed"] is True

    scene = client.get(job["scene_url"]).json()
    assert scene["workflow"] == "floor_plan_only"
    assert len(scene["room_graph"]["nodes"]) == 2
    assert len(scene["room_graph"]["edges"]) == 1
    assert scene["collision"]["walls"]
    assert scene["collision"]["furniture"]
    node = next(
        node for node in scene["room_graph"]["nodes"] if node["id"] == room_id
    )
    assert node["name"].startswith("Bathroom")
    assert node["furniture_count"] > 0
    assert node["furniture"]
    glb = client.get(f"/reconstructions/{job_id}/assets/scene.glb")
    assert glb.status_code == 200
    assert glb.content[:4] == b"glTF"

    manifest = client.get(f"/reconstructions/{job_id}/manifest").json()
    assert manifest["property_id"] == "prop-manual-correction"
    assert manifest["workflow"] == "floor_plan_only"
    assert manifest["validation"]["geometry_valid"] is True


def test_manual_plan_endpoint_validates_correction_input(
    client, stub_pipeline, tmp_path
):
    plan = tmp_path / "plan.png"
    photo = tmp_path / "photo.jpg"
    plan.write_bytes(b"plan")
    photo.write_bytes(b"photo")
    job_id = make_job_id("manual-validation")
    seed_job(
        job_id,
        status="failed",
        error="no enclosed rooms",
        floor_plan_path=str(plan),
        photo_paths=[str(photo)],
    )

    response = client.post(
        f"/reconstructions/{job_id}/manual-plan",
        json={
            "rooms": [
                {"polygon_px": [[0, 0], [100, 0], [100, 100], [0, 100]]}
            ],
            "doorways": [
                {"orientation": "diagonal", "center_px": [50, 100], "width_px": 30}
            ],
        },
    )
    assert response.status_code == 400
    assert "orientation" in response.json()["detail"]

    empty = client.post(f"/reconstructions/{job_id}/manual-plan", json={"rooms": []})
    assert empty.status_code == 400
    assert "At least one room" in empty.json()["detail"]

    completed_id = make_job_id("manual-completed")
    seed_job(completed_id, status="completed", progress=100.0)
    conflict = client.post(
        f"/reconstructions/{completed_id}/manual-plan",
        json={"rooms": [{"polygon_px": [[0, 0], [100, 0], [100, 100], [0, 100]]}]},
    )
    assert conflict.status_code == 409


def test_completed_partial_navigation_can_be_corrected_or_retried(
    client, stub_pipeline, tmp_path
):
    plan = tmp_path / "plan.png"
    photo = tmp_path / "photo.jpg"
    plan.write_bytes(b"plan")
    photo.write_bytes(b"photo")

    job_id = make_job_id("partial-nav")
    seed_job(
        job_id,
        status="completed",
        current_stage="completed",
        progress=100.0,
        navigation_available=False,
        floor_plan_path=str(plan),
        photo_paths=[str(photo)],
    )
    diagnostics = client.get(f"/reconstructions/{job_id}/diagnostics").json()
    assert diagnostics["manual_plan_supported"] is True
    assert diagnostics["retry_supported"] is True
    assert diagnostics["status"] == "completed"

    corrected = client.post(
        f"/reconstructions/{job_id}/manual-plan",
        json={
            "rooms": [
                {"polygon_px": [[0, 0], [100, 0], [100, 100], [0, 100]]}
            ]
        },
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["status"] == "queued"

    retry_id = make_job_id("partial-nav-retry")
    seed_job(
        retry_id,
        status="completed",
        current_stage="completed",
        progress=100.0,
        navigation_available=False,
        floor_plan_path=str(plan),
        photo_paths=[str(photo)],
    )
    retried = client.post(
        f"/reconstructions/{retry_id}/retry",
        json={"preprocessing": {"sealing": "aggressive"}},
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "queued"

    navigable_id = make_job_id("navigable")
    seed_job(
        navigable_id,
        status="completed",
        current_stage="completed",
        progress=100.0,
        navigation_available=True,
        floor_plan_path=str(plan),
        photo_paths=[str(photo)],
    )
    correction_conflict = client.post(
        f"/reconstructions/{navigable_id}/manual-plan",
        json={"rooms": [{"polygon_px": [[0, 0], [100, 0], [100, 100], [0, 100]]}]},
    )
    assert correction_conflict.status_code == 409
    retry_conflict = client.post(f"/reconstructions/{navigable_id}/retry")
    assert retry_conflict.status_code == 409


def test_asset_endpoint_is_path_safe_and_sets_media_type(
    client, tmp_path, monkeypatch
):
    output_dir = tmp_path / "output"
    monkeypatch.setattr(main, "OUTPUT_DIR", output_dir)
    job_id = make_job_id("assets")
    seed_job(job_id, status="completed", progress=100.0)
    job_output = output_dir / job_id
    job_output.mkdir(parents=True)
    (job_output / "scene.glb").write_bytes(b"glTF-test-payload")
    (tmp_path / "secret.txt").write_text("do not serve")

    for traversal in (
        "..%2F..%2Fsecret.txt",
        "%2e%2e%2f%2e%2e%2fsecret.txt",
        "..%2F..%2F..%2Fetc%2Fpasswd",
    ):
        response = client.get(f"/reconstructions/{job_id}/assets/{traversal}")
        assert response.status_code == 404, traversal
        assert b"do not serve" not in response.content

    asset = client.get(f"/reconstructions/{job_id}/assets/scene.glb")
    assert asset.status_code == 200
    assert asset.headers["content-type"] == "model/gltf-binary"
    assert asset.content == b"glTF-test-payload"

    listing = client.get(f"/reconstructions/{job_id}/assets")
    assert listing.status_code == 200
    assert [item["name"] for item in listing.json()["assets"]] == ["scene.glb"]


def test_scene_endpoint_reports_missing_assets(client, tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    monkeypatch.setattr(main, "OUTPUT_DIR", output_dir)
    job_id = make_job_id("no-assets")
    seed_job(job_id, status="completed", progress=100.0)

    response = client.get(f"/reconstructions/{job_id}/scene")
    assert response.status_code == 500
    assert "missing or malformed" in response.json()["detail"]


def test_blank_floor_plan_fails_the_job_honestly(client, fixture_photos, tmp_path):
    blank = tmp_path / "blank.png"
    Image.fromarray(np.full((1000, 800, 3), 255, dtype=np.uint8)).save(blank)

    response = upload(
        client,
        blank.read_bytes(),
        photos=[("room.jpg", fixture_photos[0].read_bytes())],
    )
    assert response.status_code == 201
    job_id = response.json()["job_id"]

    job = client.get(f"/reconstructions/{job_id}").json()
    assert job["status"] == "failed"
    assert job["geometry_valid"] is None
    assert job["error"]
    assert "wall" in job["error"].lower() or "blank" in job["error"].lower()
    assert job["progress"] < 100
    assert job["failed_stage"] == "parsing_floor_plan"
    assert job["error_code"] == "invalid_inputs"
    assert job["current_stage"] == "failed"
    assert job["floor_plan_url"] == f"/reconstructions/{job_id}/floor-plan"
    assert job["diagnostics_url"] == f"/reconstructions/{job_id}/diagnostics"
    assert client.get(f"/reconstructions/{job_id}/manifest").status_code == 409

    plan_preview = client.get(job["floor_plan_url"])
    assert plan_preview.status_code == 200
    assert plan_preview.content == blank.read_bytes()


def test_parse_failure_reports_stage_progress_and_diagnostics(
    client, fixture_photos, tmp_path
):
    plan = Image.new("RGB", (900, 700), "white")
    draw = ImageDraw.Draw(plan)
    draw.rectangle([60, 60, 840, 640], outline="black", width=10)
    draw.rectangle([60, 630, 840, 640], fill="white")
    plan_path = tmp_path / "open-room.png"
    plan.save(plan_path)

    response = upload(
        client,
        plan_path.read_bytes(),
        photos=[("room.jpg", fixture_photos[0].read_bytes())],
    )
    assert response.status_code == 201
    job_id = response.json()["job_id"]

    job = client.get(f"/reconstructions/{job_id}").json()
    assert job["status"] == "failed"
    assert job["failed_stage"] == "detecting_rooms_openings"
    assert job["error_code"]
    assert job["progress"] < 100
    assert job["progress"] <= main.STAGE_PROGRESS["detecting_rooms_openings"]
    assert job["error"]

    diagnostics = client.get(job["diagnostics_url"]).json()
    assert diagnostics["status"] == "failed"
    assert diagnostics["failed_stage"] == "detecting_rooms_openings"
    assert diagnostics["retry_supported"] is True
    names = {artifact["name"] for artifact in diagnostics["artifacts"]}
    assert "walls_mask.png" in names
    assert "parse_failure.json" in names
    artifact_url = next(
        artifact["url"]
        for artifact in diagnostics["artifacts"]
        if artifact["name"] == "parse_failure.json"
    )
    artifact = client.get(artifact_url)
    assert artifact.status_code == 200
    body = artifact.json()
    assert body["error_code"] == job["error_code"]
    assert body["gaps"] or body["lines"]


def test_completed_job_without_assets_never_serves_a_demo_scene(
    client, tmp_path, monkeypatch
):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setattr(main, "OUTPUT_DIR", output_dir)
    job_id = make_job_id("no-demo")
    seed_job(job_id, status="completed", progress=100.0)
    other_id = make_job_id("other")
    seed_job(other_id, status="completed", progress=100.0)

    assert client.get(f"/reconstructions/{job_id}/assets/scene.glb").status_code == 404
    assert client.get(f"/reconstructions/{other_id}/assets/scene.glb").status_code == 404
    assert client.get(f"/reconstructions/{job_id}/scene").status_code == 500
    assert client.get(f"/reconstructions/{job_id}/assets").json()["assets"] == []


def make_conflict_manifest(predicted: str, confidence: float, ambiguous: bool) -> dict:
    return {
        "schema_version": "1.0",
        "job_id": f"pytest-conflict-{uuid.uuid4().hex[:8]}",
        "property_id": "prop-conflict",
        "classifier": {"status": "completed", "model": "test-double", "labels": []},
        "review": {"status": "pending", "confirmed_at": None, "room_types": {}},
        "photos": [
            {
                "photo_id": "ph-conflict",
                "original": {"original_filename": "kitchen_view.jpg"},
                "processing": {"status": "ready", "errors": []},
                "classification": {
                    "status": "ambiguous" if ambiguous else "classified",
                    "predicted_room_type": predicted,
                    "predicted_label": predicted.replace("_", " "),
                    "confidence": confidence,
                    "ambiguous": ambiguous,
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
                    "reason": "",
                },
            }
        ],
    }


def test_conflicting_room_assignment_is_flagged_for_the_user(monkeypatch):
    from room_matcher import apply_assignments

    monkeypatch.setattr(
        "room_matcher.save_photo_manifest", lambda manifest: manifest
    )
    rooms = [{"room_id": 0, "default_name": "Room 1"}]

    manifest = make_conflict_manifest("kitchen", 0.91, False)
    apply_assignments(
        manifest,
        rooms,
        [{"photo_id": "ph-conflict", "room_id": 0}],
        {"0": "bedroom"},
    )
    conflict = manifest["photos"][0]["match"]["conflict"]
    assert conflict["detected_room_type"] == "kitchen"
    assert conflict["assigned_room_type"] == "bedroom"
    assert "user's assignment takes precedence" in conflict["message"]

    matching = make_conflict_manifest("kitchen", 0.91, False)
    apply_assignments(
        matching,
        rooms,
        [{"photo_id": "ph-conflict", "room_id": 0}],
        {"0": "kitchen"},
    )
    assert matching["photos"][0]["match"]["conflict"] is None

    uncertain = make_conflict_manifest("kitchen", 0.91, True)
    apply_assignments(
        uncertain,
        rooms,
        [{"photo_id": "ph-conflict", "room_id": 0}],
        {"0": "bedroom"},
    )
    assert uncertain["photos"][0]["match"]["conflict"] is None


def create_six_room_plan() -> bytes:
    image = Image.new("RGB", (1000, 700), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([50, 50, 950, 650], outline="black", width=12)
    for x in (333, 667):
        draw.rectangle([x - 6, 50, x + 6, 290], fill="black")
        draw.rectangle([x - 6, 340, x + 6, 650], fill="black")
    for left, right in ((50, 250), (300, 550), (600, 950)):
        draw.rectangle([left, 344, right, 356], fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


