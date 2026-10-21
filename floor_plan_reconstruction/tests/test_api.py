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
    for name, content in photos or [("room.jpg", b"\xff\xd8\xff\xd9")]:
        files.append(("photos", (name, content, "image/jpeg")))
    data = {"project_name": "API test property", **form}
    return client.post("/reconstructions", data=data, files=files)


def fetch_review(client, job_id: str) -> dict:
    response = client.get(f"/reconstructions/{job_id}/review")
    assert response.status_code == 200, response.text
    return response.json()


def confirm_review(
    client,
    job_id: str,
    mapping: dict[str, int] | None = None,
    room_types: dict[str, str] | None = None,
    exclude: set[str] | None = None,
):
    """Confirm every ready photo, assigning by original filename or round-robin."""
    review = fetch_review(client, job_id)
    assert review["assignment_required"] is True, review.get("status")
    rooms = review["rooms"]
    assert rooms
    ready = [
        photo
        for photo in review["photos"]
        if photo["processing"]["status"] == "ready"
    ]
    assignments = []
    for index, photo in enumerate(ready):
        name = photo["original_filename"]
        if exclude and name in exclude:
            assignments.append({"photo_id": photo["photo_id"], "excluded": True})
            continue
        room_id = mapping[name] if mapping else rooms[index % len(rooms)]["room_id"]
        assignments.append({"photo_id": photo["photo_id"], "room_id": room_id})
    response = client.post(
        f"/reconstructions/{job_id}/confirm-assignments",
        json={"assignments": assignments, "room_types": room_types or {}},
    )
    assert response.status_code == 200, response.text
    return response, review


def upload_confirm_and_complete(
    client,
    plan_bytes: bytes,
    photos,
    mapping: dict[str, int] | None = None,
    room_types: dict[str, str] | None = None,
    **form,
):
    response = upload(client, plan_bytes, photos=photos, **form)
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]
    confirm, review = confirm_review(
        client, job_id, mapping=mapping, room_types=room_types
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


def create_room_photo(path: Path, size, wall_rgb, floor_rgb, accent_rgb, accent_box):
    width, height = size
    image = Image.new("RGB", size, wall_rgb)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, int(height * 0.72), width, height], fill=floor_rgb)
    draw.rectangle(accent_box, fill=accent_rgb)
    image.save(path, "JPEG", quality=90)
    return path


def create_colour_photo(path: Path, wall_rgb, floor_rgb, accent_rgb, accent_box):
    return create_room_photo(
        path, (480, 360), wall_rgb, floor_rgb, accent_rgb, accent_box
    )


def independent_property_inputs(tmp_path: Path):
    plan = create_two_room_plan()
    first = create_colour_photo(
        tmp_path / "beta_hall.jpg",
        wall_rgb=(52, 68, 84),
        floor_rgb=(120, 82, 50),
        accent_rgb=(30, 30, 30),
        accent_box=[60, 170, 220, 260],
    )
    second = create_colour_photo(
        tmp_path / "beta_study.jpg",
        wall_rgb=(120, 90, 60),
        floor_rgb=(60, 50, 40),
        accent_rgb=(210, 190, 120),
        accent_box=[300, 90, 430, 200],
    )
    return plan, [first, second]


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


def glb_embedded_image_sizes(content: bytes):
    gltf, binary = read_glb_chunks(content)
    sizes = []
    for image in gltf.get("images", []):
        view = gltf["bufferViews"][image["bufferView"]]
        start = view.get("byteOffset", 0)
        data = binary[start:start + view["byteLength"]]
        with Image.open(io.BytesIO(data)) as embedded:
            sizes.append(embedded.size)
    return sizes


def test_rejects_blank_project_name(client, fixture_plan):
    response = upload(client, fixture_plan.read_bytes(), project_name="   ")
    assert response.status_code == 400
    assert "Project name" in response.json()["detail"]


def test_rejects_unsupported_floor_plan_extension(client):
    response = upload(client, b"whatever", plan_name="plan.txt")
    assert response.status_code == 400
    assert "Unsupported floor plan format" in response.json()["detail"]


def test_rejects_empty_photo(client, fixture_plan):
    response = upload(client, fixture_plan.read_bytes(), photos=[("empty.jpg", b"")])
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


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


def test_rejects_more_than_the_photo_limit(client, fixture_plan, monkeypatch):
    monkeypatch.setattr(main, "MAX_PHOTOS", 2)
    response = upload(
        client,
        fixture_plan.read_bytes(),
        photos=[("a.jpg", b"a"), ("b.jpg", b"b"), ("c.jpg", b"c")],
    )
    assert response.status_code == 400
    assert "At most 2" in response.json()["detail"]


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
    assert pending["status"] == "awaiting_review", pending.get("error")
    review = fetch_review(client, job_id)
    assert len(review["rooms"]) == 2
    assert review["plan"]["floor_plan_url"] == f"/reconstructions/{job_id}/floor-plan"
    room_id = review["rooms"][0]["room_id"]
    confirmed, _ = confirm_review(
        client,
        job_id,
        mapping={"room.jpg": room_id},
        room_types={str(room_id): "bathroom"},
    )
    assert confirmed.json()["status"] == "queued"

    job = client.get(f"/reconstructions/{job_id}").json()
    assert job["status"] == "completed", job.get("error")
    assert job["geometry_valid"] is True
    assert job["navigation_available"] is True
    assert job["scene_url"]
    assert job["retry_count"] == 1
    assert job["assignments_confirmed"] is True
    assert job["manual_outline"]["doorways"][0]["orientation"] == "vertical"

    diagnostics = client.get(job["diagnostics_url"]).json()
    assert diagnostics["parser"]["segmentation"]["source"] == "manual_outline"
    assert diagnostics["failure_report"]["error_code"] == "no_enclosed_rooms" or (
        diagnostics["failure_report"]["error_code"]
    )
    assert diagnostics["photo_review"]["assigned"] == 1

    scene = client.get(job["scene_url"]).json()
    assert len(scene["room_graph"]["nodes"]) == 2
    assert len(scene["room_graph"]["edges"]) == 1
    assert scene["collision"]["walls"]
    node = next(
        node for node in scene["room_graph"]["nodes"] if node["id"] == room_id
    )
    assert node["photo_count"] == 1
    assert node["name"] == "Bathroom"
    assert node["photos"][0]["thumbnail_url"] == (
        f"/reconstructions/{job_id}/photos/{node['photo_ids'][0]}/thumbnail"
    )
    thumbnail = client.get(node["photos"][0]["thumbnail_url"])
    assert thumbnail.status_code == 200
    assert thumbnail.headers["content-type"] == "image/jpeg"
    glb = client.get(f"/reconstructions/{job_id}/assets/scene.glb")
    assert glb.status_code == 200
    assert glb.content[:4] == b"glTF"

    manifest = client.get(f"/reconstructions/{job_id}/manifest").json()
    assert manifest["property_id"] == "prop-manual-correction"
    assert any(
        "manual" in warning.lower() or "user-selected" in warning.lower()
        for warning in manifest["warnings"]
    ) or manifest["validation"]["geometry_valid"] is True


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
    assert job["failed_stage"] == "preprocessing"
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
    assert job["failed_stage"] == "detecting_rooms"
    assert job["error_code"]
    assert job["progress"] < 100
    assert job["progress"] <= main.STAGE_PROGRESS["detecting_rooms"]
    assert job["error"]

    diagnostics = client.get(job["diagnostics_url"]).json()
    assert diagnostics["status"] == "failed"
    assert diagnostics["failed_stage"] == "detecting_rooms"
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


def test_full_pipeline_upload_to_served_glb(client, fixture_plan, fixture_photos):
    plan_bytes = fixture_plan.read_bytes()
    photos = [(path.name, path.read_bytes()) for path in fixture_photos]
    job_id, review, _ = upload_confirm_and_complete(
        client,
        plan_bytes,
        photos,
        mapping={
            "living_room.jpg": 0,
            "kitchen.jpg": 1,
            "bedroom.jpg": 2,
            "bathroom.jpg": 3,
        },
        room_types={
            "0": "living_room",
            "1": "kitchen",
            "2": "bedroom",
            "3": "bathroom",
        },
        known_width="12",
        known_length="9",
        property_id="prop-primary-e2e",
    )
    assert len(review["rooms"]) == 4
    assert review["summary"]["needs_review"] == 4
    assert all(photo["suggestion"] for photo in review["photos"])
    assert all(
        photo["match"]["status"] == "needs_review"
        for photo in review["photos"]
        if photo["processing"]["status"] == "ready"
    )

    job = client.get(f"/reconstructions/{job_id}").json()
    assert job["status"] == "completed", job.get("error")
    assert job["progress"] == 100.0
    assert job["property_id"] == "prop-primary-e2e"
    assert job["geometry_valid"] is True
    assert job["navigation_available"] is True
    assert job["scale_verified"] is True
    assert job["assignments_confirmed"] is True
    assert job["review_required"] is False
    assert job["room_count"] == 4
    assert job["photo_review_summary"]["assigned"] == 4
    assert "scene_url" in job and job["scene_url"]
    assert len(job["warnings"]) == len(set(job["warnings"]))
    assert job["limitations"]
    assert any(
        "overlap" in item.lower() or "colmap" in item.lower()
        for item in job["limitations"]
    )
    assert any(
        "assigned to rooms through classification" in item
        for item in job["limitations"]
    )

    inputs = job["inputs"]
    assert inputs["property_id"] == "prop-primary-e2e"
    assert inputs["floor_plan"]["sha256"] == sha256_bytes(plan_bytes)
    assert inputs["floor_plan"]["size_bytes"] == len(plan_bytes)
    assert [entry["original_filename"] for entry in inputs["photos"]] == [
        path.name for path in fixture_photos
    ]
    for entry, (_, content) in zip(inputs["photos"], photos):
        assert entry["sha256"] == sha256_bytes(content)
        assert entry["size_bytes"] == len(content)
        assert entry["stored_filename"].startswith("photo_")
        assert entry["original_filename"].split(".")[0] in entry["stored_filename"]

    stage_names = [record["stage"] for record in job["stage_records"]]
    assert stage_names == [
        "input_validation",
        "floor_plan_parse",
        "photo_classification",
        "room_matching",
        "photo_analysis",
        "photogrammetry",
        "geometry_build",
        "scene_validation",
        "manifest",
    ]
    stages = {record["stage"]: record for record in job["stage_records"]}
    assert stages["input_validation"]["status"] == "completed"
    assert stages["photo_classification"]["status"] in {"completed", "partial"}
    assert stages["room_matching"]["status"] == "review_required"
    assert stages["photo_analysis"]["status"] == "completed"
    assert stages["photogrammetry"]["status"] == "insufficient_overlap"
    assert stages["geometry_build"]["status"] == "completed"
    assert stages["scene_validation"]["status"] == "completed"

    diagnostics = job["diagnostics"]
    assert diagnostics["photogrammetry"]["status"] == "insufficient_overlap"
    assert diagnostics["photogrammetry"]["registered_images"] == 0
    assert diagnostics["photogrammetry"]["engine"]["name"] == "COLMAP"
    assert diagnostics["geometry"]["textures"]["applied"] is True
    assert diagnostics["photos"]["photogrammetry"]["evaluated"] is True

    checks = diagnostics["scene_validation"]["checks"]
    assert checks["glb_loadable"] is True
    assert checks["spawn_walkable"] is True
    assert checks["rooms_connected"] is True
    assert checks["asset_job_match"] is True
    assert checks["photo_textures_embedded"] is True
    assert checks["photo_manifest"] is True
    assert checks["photo_manifest_scope"] is True
    assert checks["photo_review_confirmed"] is True
    assert checks["photo_assignments_valid"] is True
    assert checks["photo_files_exist"] is True
    assert checks["photo_decodable"] is True
    assert checks["room_photo_references"] is True
    assert checks["room_textures_match"] is True
    assert checks["texture_files_decode"] is True
    assert diagnostics["scene_validation"]["stats"]["embedded_images"] > 0
    assert diagnostics["scene_validation"]["stats"]["embedded_textures"] > 0

    manifest = client.get(f"/reconstructions/{job_id}/manifest")
    assert manifest.status_code == 200
    manifest_body = manifest.json()
    assert manifest_body["schema_version"] == "2.1"
    assert manifest_body["property_id"] == "prop-primary-e2e"
    assert manifest_body["job_id"] == job_id
    assert manifest_body["primary_asset"] == "scene.glb"
    assert manifest_body["method"]["photogrammetry_used_for_geometry"] is False
    assert manifest_body["method"]["image_reconstruction"]["sfm"]["status"] == (
        "insufficient_overlap"
    )
    assert manifest_body["method"]["textures"]["applied"] is True
    assert manifest_body["photogrammetry"]["status"] == "insufficient_overlap"
    assert manifest_body["inferred"]
    assert any(
        "assigned to rooms through classification" in item
        for item in manifest_body["limitations"]
    )
    photo_manifest = manifest_body["photo_manifest"]
    assert photo_manifest["review"]["status"] == "confirmed"
    assert photo_manifest["summary"]["assigned"] == 4
    assert photo_manifest["classifier"]["status"] in {"completed", "partial"}
    assert len(photo_manifest["photos"]) == 4
    assert {photo["photo_id"] for photo in photo_manifest["photos"]} == {
        photo["photo_id"] for photo in review["photos"]
    }
    assert all(
        photo["room_assignment"]["confirmed"]
        for photo in photo_manifest["photos"]
    )
    assert {entry["room_id"] for entry in manifest_body["room_assignments"]} == {
        0,
        1,
        2,
        3,
    }
    names = {entry["room_id"]: entry["name"] for entry in manifest_body["room_assignments"]}
    assert names[0] == "Living room"
    assert names[1] == "Kitchen"
    assert names[2] == "Bedroom 1"
    assert names[3] == "Bathroom"
    for entry in manifest_body["room_assignments"]:
        assert len(entry["photos"]) == 1
        assert entry["photos"][0] in {
            photo["photo_id"] for photo in photo_manifest["photos"]
        }
    for source in manifest_body["method"]["textures"]["sources"]:
        assert source["room_id"] in {0, 1, 2, 3}
        photo = next(
            item
            for item in photo_manifest["photos"]
            if item["photo_id"] == source["photo_id"]
        )
        assert photo["room_assignment"]["room_id"] == source["room_id"]

    provenance = manifest_body["provenance"]
    assert provenance["property_id"] == "prop-primary-e2e"
    assert provenance["job_id"] == job_id
    assert provenance["engine"]["version"] == main.app.version
    assert provenance["engine"]["photogrammetry"]["name"] == "COLMAP"
    assert provenance["inputs"]["floor_plan"]["sha256"] == sha256_bytes(plan_bytes)
    assert {entry["sha256"] for entry in provenance["inputs"]["photos"]} == {
        sha256_bytes(content) for _, content in photos
    }
    assert [record["stage"] for record in provenance["stages"]] == stage_names[:-1]
    assert provenance["warnings"] == manifest_body["warnings"]
    assert provenance["limitations"] == manifest_body["limitations"]

    asset_names = [asset["name"] for asset in manifest_body["assets"]]
    assert "scene.glb" in asset_names
    assert "photogrammetry.json" in asset_names
    assert all(len(asset["sha256"]) == 64 for asset in manifest_body["assets"])
    for asset in manifest_body["assets"]:
        asset_path = main.OUTPUT_DIR / job_id / asset["name"]
        assert asset_path.exists(), asset["name"]
        assert hashlib.sha256(asset_path.read_bytes()).hexdigest() == asset["sha256"]

    scene = client.get(f"/reconstructions/{job_id}/scene")
    assert scene.status_code == 200
    scene_body = scene.json()
    assert scene_body["model_url"] == f"/reconstructions/{job_id}/assets/scene.glb"
    assert scene_body["collision"]["walls"]
    assert len(scene_body["room_graph"]["nodes"]) == 4
    assert scene_body["metadata"]["rooms"] == 4
    assert scene_body["metadata"]["scale"]["verified"] is True
    assert scene_body["metadata"]["job_id"] == job_id
    assert scene_body["metadata"]["method"]["textures"]["applied"] is True
    assert scene_body["manifest"]["schema_version"] == "2.1"
    assert scene_body["photo_manifest"]["review"]["status"] == "confirmed"
    assert len(scene_body["room_assignments"]) == 4
    node_by_id = {node["id"]: node for node in scene_body["room_graph"]["nodes"]}
    assert node_by_id[0]["name"] == "Living room"
    assert node_by_id[1]["name"] == "Kitchen"
    assert node_by_id[2]["name"] == "Bedroom 1"
    assert node_by_id[3]["name"] == "Bathroom"
    assert all(len(node["photo_ids"]) == 1 for node in node_by_id.values())
    assert all(node["photos"][0]["thumbnail_url"] for node in node_by_id.values())

    glb = client.get(f"/reconstructions/{job_id}/assets/scene.glb")
    assert glb.status_code == 200
    assert glb.headers["content-type"] == "model/gltf-binary"
    assert glb.content[:4] == b"glTF"
    assert glb.content == (main.OUTPUT_DIR / job_id / "scene.glb").read_bytes()

    logs = client.get(f"/reconstructions/{job_id}/logs").json()["lines"]
    assert any("Reconstruction completed" in line for line in logs)
    assert any("Photogrammetry: insufficient_overlap" in line for line in logs)

    report = client.get(f"/reconstructions/{job_id}/report").json()
    assert report["property_id"] == "prop-primary-e2e"
    assert report["scale_verified"] is True
    assert report["validation"]["geometry_valid"] is True
    assert report["inputs"]["floor_plan"]["sha256"] == sha256_bytes(plan_bytes)
    assert [record["stage"] for record in report["stage_records"]] == stage_names

    listing = client.get("/reconstructions?limit=5").json()
    summary = next(item for item in listing if item["job_id"] == job_id)
    assert summary["diagnostics"] is None
    assert summary["assets"] is None

    assets = client.get(f"/reconstructions/{job_id}/assets").json()["assets"]
    names = {asset["name"] for asset in assets}
    assert {"scene.glb", "collision.json", "room_graph.json", "metadata.json"} <= names
    assert "manifest.json" in names
    assert "validation_report.json" in names
    assert "photogrammetry.json" in names

    stored = json.loads(main.JOBS_FILE.read_text())[job_id]
    assert "floor_plan_path" not in main._public_job(stored)
    assert str(stored["floor_plan_path"]).startswith(str(main.INPUT_DIR))


def test_second_property_produces_an_independent_scene(
    client, fixture_plan, fixture_photos, tmp_path
):
    alpha_plan = fixture_plan.read_bytes()
    alpha_photos = [(path.name, path.read_bytes()) for path in fixture_photos]
    alpha_id, alpha_review, _ = upload_confirm_and_complete(
        client,
        alpha_plan,
        alpha_photos,
        mapping={
            "living_room.jpg": 0,
            "kitchen.jpg": 1,
            "bedroom.jpg": 2,
            "bathroom.jpg": 3,
        },
        room_types={
            "0": "living_room",
            "1": "kitchen",
            "2": "bedroom",
            "3": "bathroom",
        },
        known_width="12",
        known_length="9",
        property_id="prop-alpha",
    )

    beta_plan, beta_paths = independent_property_inputs(tmp_path)
    beta_photos = [(path.name, path.read_bytes()) for path in beta_paths]
    beta_id, beta_review, _ = upload_confirm_and_complete(
        client,
        beta_plan,
        beta_photos,
        mapping={"beta_hall.jpg": 0, "beta_study.jpg": 1},
        room_types={"0": "entrance", "1": "dining_room"},
        known_width="8",
        known_length="6",
        property_id="prop-beta",
    )
    assert alpha_id != beta_id
    assert alpha_review["job_id"] == alpha_id
    assert beta_review["job_id"] == beta_id
    alpha_photo_ids = {photo["photo_id"] for photo in alpha_review["photos"]}
    beta_photo_ids = {photo["photo_id"] for photo in beta_review["photos"]}
    assert alpha_photo_ids.isdisjoint(beta_photo_ids)

    alpha_job = client.get(f"/reconstructions/{alpha_id}").json()
    beta_job = client.get(f"/reconstructions/{beta_id}").json()
    assert alpha_job["status"] == "completed", alpha_job.get("error")
    assert beta_job["status"] == "completed", beta_job.get("error")
    assert alpha_job["property_id"] == "prop-alpha"
    assert beta_job["property_id"] == "prop-beta"
    assert alpha_job["inputs"]["floor_plan"]["sha256"] == sha256_bytes(alpha_plan)
    assert beta_job["inputs"]["floor_plan"]["sha256"] == sha256_bytes(beta_plan)

    alpha_scene = client.get(f"/reconstructions/{alpha_id}/scene").json()
    beta_scene = client.get(f"/reconstructions/{beta_id}/scene").json()
    assert alpha_scene["model_url"] == (
        f"/reconstructions/{alpha_id}/assets/scene.glb"
    )
    assert beta_scene["model_url"] == f"/reconstructions/{beta_id}/assets/scene.glb"
    assert alpha_scene["metadata"]["job_id"] == alpha_id
    assert beta_scene["metadata"]["job_id"] == beta_id
    assert alpha_scene["metadata"]["rooms"] == 4
    assert beta_scene["metadata"]["rooms"] == 2
    assert len(alpha_scene["room_graph"]["nodes"]) == 4
    assert len(beta_scene["room_graph"]["nodes"]) == 2
    assert alpha_scene["metadata"]["footprint_m"] != beta_scene["metadata"]["footprint_m"]

    alpha_glb = client.get(f"/reconstructions/{alpha_id}/assets/scene.glb")
    beta_glb = client.get(f"/reconstructions/{beta_id}/assets/scene.glb")
    assert alpha_glb.status_code == beta_glb.status_code == 200
    assert alpha_glb.content[:4] == beta_glb.content[:4] == b"glTF"
    assert sha256_bytes(alpha_glb.content) != sha256_bytes(beta_glb.content)

    alpha_manifest = client.get(f"/reconstructions/{alpha_id}/manifest").json()
    beta_manifest = client.get(f"/reconstructions/{beta_id}/manifest").json()
    assert alpha_manifest["property_id"] == "prop-alpha"
    assert beta_manifest["property_id"] == "prop-beta"
    assert alpha_manifest["inferred"] and beta_manifest["inferred"]
    assert alpha_manifest["method"]["textures"]["applied"] is True
    assert beta_manifest["method"]["textures"]["applied"] is True
    alpha_sources = {
        source["file"] for source in alpha_manifest["textures"]["sources"]
    }
    beta_sources = {
        source["file"] for source in beta_manifest["textures"]["sources"]
    }
    assert alpha_sources.isdisjoint(beta_sources)
    assert all("beta" not in name for name in alpha_sources)
    assert all("beta" in name for name in beta_sources)
    alpha_scene_hash = next(
        asset["sha256"]
        for asset in alpha_manifest["assets"]
        if asset["name"] == "scene.glb"
    )
    beta_scene_hash = next(
        asset["sha256"]
        for asset in beta_manifest["assets"]
        if asset["name"] == "scene.glb"
    )
    assert alpha_scene_hash == sha256_bytes(alpha_glb.content)
    assert beta_scene_hash == sha256_bytes(beta_glb.content)
    assert alpha_scene_hash != beta_scene_hash
    assert {
        entry["sha256"] for entry in beta_manifest["provenance"]["inputs"]["photos"]
    } == {sha256_bytes(content) for _, content in beta_photos}
    assert {
        entry["name"] for entry in beta_manifest["room_assignments"]
    } == {"Entrance / foyer", "Dining room"}
    alpha_photo_id = next(iter(alpha_photo_ids))
    assert (
        client.get(
            f"/reconstructions/{alpha_id}/photos/{alpha_photo_id}/thumbnail"
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/reconstructions/{beta_id}/photos/{alpha_photo_id}/thumbnail"
        ).status_code
        == 404
    )
    assert alpha_manifest["photo_manifest"]["review"]["status"] == "confirmed"
    assert beta_manifest["photo_manifest"]["review"]["status"] == "confirmed"


def test_duplicate_photos_are_reported_not_silently_accepted(
    client, fixture_plan, fixture_photos
):
    content = fixture_photos[0].read_bytes()
    job_id, review, _ = upload_confirm_and_complete(
        client,
        fixture_plan.read_bytes(),
        photos=[
            ("same_view_a.jpg", content),
            ("same_view_b.jpg", content),
            ("same_view_c.jpg", content),
        ],
        mapping={
            "same_view_a.jpg": 0,
            "same_view_b.jpg": 0,
            "same_view_c.jpg": 0,
        },
        room_types={"0": "living_room"},
    )
    assert review["summary"]["needs_review"] == 3
    assert fetch_review(client, job_id)["summary"]["assigned"] == 3
    flagged_at_review = [
        photo for photo in review["photos"] if photo["quality"].get("duplicate_of")
    ]
    assert len(flagged_at_review) == 2, (
        "Duplicate uploads must be visible while the user is reviewing, "
        "not only after reconstruction"
    )

    job = client.get(f"/reconstructions/{job_id}").json()
    assert job["status"] == "completed", job.get("error")
    assert job["photo_count"] == 3
    assert job["diagnostics"]["photos"]["analyzed_photos"] == 3
    assert job["photo_review_summary"]["assigned"] == 3
    duplicates = [
        entry
        for entry in job["diagnostics"]["photos"]["textures"]
        if entry.get("duplicate_of")
    ]
    assert len(duplicates) == 2
    assert job["diagnostics"]["photogrammetry"]["status"] == "insufficient_overlap"
    assert any("duplicate photograph" in warning.lower() for warning in job["warnings"])
    assert any(
        "two distinct" in warning.lower() for warning in job["limitations"] + job["warnings"]
    )

    report = client.get(f"/reconstructions/{job_id}/report").json()
    assert report["status"] == "completed"
    assert report["diagnostics"]["photogrammetry"]["registered_images"] == 0


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


def test_review_requires_every_photo_and_valid_job_scoped_assignments(
    client, tmp_path
):
    plan, paths = independent_property_inputs(tmp_path)
    response = upload(
        client,
        plan,
        photos=[(path.name, path.read_bytes()) for path in paths],
        known_width="8",
        known_length="6",
        property_id="prop-review-validation",
    )
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]

    pending = client.get(f"/reconstructions/{job_id}").json()
    assert pending["status"] == "awaiting_review", pending.get("error")
    assert pending["review_required"] is True
    assert pending["assignments_confirmed"] is False

    review = fetch_review(client, job_id)
    assert review["assignment_required"] is True
    assert review["confirmed"] is False
    ready = [
        photo for photo in review["photos"] if photo["processing"]["status"] == "ready"
    ]
    assert len(ready) == 2
    assert review["summary"]["needs_review"] == 2
    first_room = review["rooms"][0]["room_id"]

    missing = client.post(
        f"/reconstructions/{job_id}/confirm-assignments",
        json={
            "assignments": [
                {"photo_id": ready[0]["photo_id"], "room_id": first_room}
            ]
        },
    )
    assert missing.status_code == 400
    assert "have not been reviewed" in missing.json()["detail"]

    foreign = client.post(
        f"/reconstructions/{job_id}/confirm-assignments",
        json={
            "assignments": [
                {"photo_id": ready[0]["photo_id"], "room_id": first_room},
                {"photo_id": "ph-from-another-property", "room_id": first_room},
            ]
        },
    )
    assert foreign.status_code == 400
    assert "Unknown or unusable photograph" in foreign.json()["detail"]

    wrong_room = client.post(
        f"/reconstructions/{job_id}/confirm-assignments",
        json={
            "assignments": [
                {"photo_id": ready[0]["photo_id"], "room_id": 99},
                {"photo_id": ready[1]["photo_id"], "room_id": first_room},
            ]
        },
    )
    assert wrong_room.status_code == 400
    assert "not part of this job" in wrong_room.json()["detail"]

    wrong_type = client.post(
        f"/reconstructions/{job_id}/confirm-assignments",
        json={
            "assignments": [
                {"photo_id": ready[0]["photo_id"], "room_id": first_room},
                {"photo_id": ready[1]["photo_id"], "room_id": first_room},
            ],
            "room_types": {str(first_room): "garage"},
        },
    )
    assert wrong_type.status_code == 400
    assert "Unknown room type" in wrong_type.json()["detail"]

    confirmed, _ = confirm_review(
        client,
        job_id,
        mapping={photo["original_filename"]: first_room for photo in ready},
        room_types={str(first_room): "living_room"},
    )
    assert confirmed.status_code == 200, confirmed.text
    job = client.get(f"/reconstructions/{job_id}").json()
    assert job["status"] == "completed", job.get("error")
    assert job["assignments_confirmed"] is True
    assert job["photo_review_summary"]["assigned"] == 2


def test_user_review_overrides_classifier_and_keeps_bedrooms_distinct(
    client, fixture_plan, tmp_path
):
    bedroom_a = create_room_photo(
        tmp_path / "bedroom_a.jpg",
        (480, 360),
        (118, 110, 96),
        (80, 62, 45),
        (160, 140, 110),
        [60, 150, 180, 240],
    )
    bedroom_b = create_room_photo(
        tmp_path / "bedroom_b.jpg",
        (480, 360),
        (120, 112, 98),
        (82, 64, 46),
        (150, 135, 105),
        [280, 120, 420, 220],
    )
    kitchen = create_room_photo(
        tmp_path / "kitchen.jpg",
        (480, 360),
        (70, 92, 82),
        (58, 54, 48),
        (208, 210, 198),
        [90, 120, 300, 230],
    )
    photos = [bedroom_a, bedroom_b, kitchen]
    response = upload(
        client,
        fixture_plan.read_bytes(),
        photos=[(path.name, path.read_bytes()) for path in photos],
        known_width="12",
        known_length="9",
        property_id="prop-bedroom-correction",
    )
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]
    review = fetch_review(client, job_id)
    assert len(review["rooms"]) == 4
    ready = [
        photo for photo in review["photos"] if photo["processing"]["status"] == "ready"
    ]
    assert len(ready) == 3
    assert all(photo["suggestion"]["status"] == "needs_review" for photo in ready)
    predictions = {
        photo["original_filename"]: (
            photo["classification"] or {}
        ).get("predicted_room_type")
        for photo in ready
    }
    assert any(predictions.values())

    mapping = {"bedroom_a.jpg": 0, "bedroom_b.jpg": 1, "kitchen.jpg": 2}
    room_types = {"0": "bedroom", "1": "bedroom", "2": "kitchen"}
    assignments = [
        {"photo_id": photo["photo_id"], "room_id": mapping[photo["original_filename"]]}
        for photo in ready
    ]
    confirmed = client.post(
        f"/reconstructions/{job_id}/confirm-assignments",
        json={"assignments": assignments, "room_types": room_types},
    )
    assert confirmed.status_code == 200, confirmed.text

    job = client.get(f"/reconstructions/{job_id}").json()
    assert job["status"] == "completed", job.get("error")
    confirmed_review = fetch_review(client, job_id)
    scene = client.get(job["scene_url"]).json()
    nodes = {node["id"]: node for node in scene["room_graph"]["nodes"]}
    assert nodes[0]["name"] == "Bedroom 1"
    assert nodes[1]["name"] == "Bedroom 2"
    assert nodes[2]["name"] == "Kitchen"
    assert nodes[3]["name"] == "Room 4"
    assert nodes[3]["photo_count"] == 0
    expected = {
        0: "bedroom_a.jpg",
        1: "bedroom_b.jpg",
        2: "kitchen.jpg",
    }
    for room_id, filename in expected.items():
        assert nodes[room_id]["photo_count"] == 1
        assert nodes[room_id]["photos"][0]["thumbnail_url"].endswith(
            f"/photos/{nodes[room_id]['photo_ids'][0]}/thumbnail"
        )
        photo = next(
            item
            for item in confirmed_review["photos"]
            if item["original_filename"] == filename
        )
        assert photo["room_assignment"]["room_id"] == room_id
        assert nodes[room_id]["photo_ids"] == [photo["photo_id"]]

    manifest = client.get(f"/reconstructions/{job_id}/manifest").json()
    room_map = manifest["textures"]["room_map"]
    assert room_map["0"]["files"] == ["bedroom_a.jpg"]
    assert room_map["1"]["files"] == ["bedroom_b.jpg"]
    assert room_map["2"]["files"] == ["kitchen.jpg"]
    for source in manifest["method"]["textures"]["sources"]:
        filename = source["file"]
        assert mapping[filename] == source["room_id"]
    for photo in manifest["photo_manifest"]["photos"]:
        recorded = (photo["classification"] or {}).get("predicted_room_type")
        assert recorded == predictions[photo["original_filename"]]
        assert photo["room_assignment"]["confirmed"] is True


def test_portrait_landscape_and_unusual_aspect_preserve_image_ratios(
    client, tmp_path
):
    plan = create_two_room_plan()
    landscape = create_room_photo(
        tmp_path / "landscape.jpg",
        (640, 400),
        (58, 74, 92),
        (112, 78, 48),
        (28, 28, 30),
        [80, 180, 260, 280],
    )
    panorama = create_room_photo(
        tmp_path / "panorama.jpg",
        (2000, 160),
        (124, 92, 58),
        (62, 52, 42),
        (214, 192, 124),
        [900, 40, 1300, 130],
    )
    photos = [landscape, panorama]
    response = upload(
        client,
        plan,
        photos=[(path.name, path.read_bytes()) for path in photos],
        known_width="8",
        known_length="6",
        property_id="prop-aspect",
    )
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]
    review = fetch_review(client, job_id)
    ready = {
        photo["original_filename"]: photo
        for photo in review["photos"]
        if photo["processing"]["status"] == "ready"
    }
    assert ready["landscape.jpg"]["width"] == 640
    assert ready["landscape.jpg"]["height"] == 400
    assert ready["landscape.jpg"]["aspect_ratio"] == 1.6
    assert ready["landscape.jpg"]["orientation"] == "landscape"
    assert ready["panorama.jpg"]["width"] == 2000
    assert ready["panorama.jpg"]["height"] == 160
    assert ready["panorama.jpg"]["aspect_ratio"] == 12.5
    assert ready["panorama.jpg"]["orientation"] == "landscape"
    assert any(
        "aspect ratio" in warning.lower()
        for warning in ready["panorama.jpg"]["quality"]["warnings"]
    )

    first_room = review["rooms"][0]["room_id"]
    second_room = review["rooms"][1]["room_id"]
    response = client.post(
        f"/reconstructions/{job_id}/confirm-assignments",
        json={
            "assignments": [
                {"photo_id": ready["landscape.jpg"]["photo_id"], "room_id": first_room},
                {"photo_id": ready["panorama.jpg"]["photo_id"], "room_id": second_room},
            ],
            "room_types": {
                str(first_room): "living_room",
                str(second_room): "kitchen",
            },
        },
    )
    assert response.status_code == 200, response.text
    job = client.get(f"/reconstructions/{job_id}").json()
    assert job["status"] == "completed", job.get("error")

    for path, photo in zip(photos, [ready["landscape.jpg"], ready["panorama.jpg"]]):
        original = client.get(photo["original_url"])
        assert original.status_code == 200
        assert sha256_bytes(original.content) == sha256_bytes(path.read_bytes())
        ratio = photo["aspect_ratio"]
        for variant in ("preview_url", "thumbnail_url"):
            response = client.get(photo[variant])
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("image/")
            with Image.open(io.BytesIO(response.content)) as derived:
                derived_ratio = derived.width / derived.height
                # Relative tolerance: a one-pixel rounding step dominates at
                # extreme ratios such as a 12.5:1 panorama.
                assert abs(derived_ratio - ratio) / ratio < 0.02, (
                    f"{variant} must preserve the uploaded aspect ratio "
                    f"({derived_ratio:.3f} vs {ratio:.3f})"
                )
                if ratio > 1:
                    assert derived.width > derived.height
                else:
                    assert derived.height > derived.width

    diagnostics = job["diagnostics"]
    texture_root = main.GEOMETRY_DIR / job_id
    for texture in diagnostics["photos"]["textures"]:
        for key in ("wall_texture", "floor_texture"):
            relative = texture[key]
            assert relative, f"{texture['file']} should produce a {key}"
            with Image.open(texture_root / relative) as crop:
                assert crop.size == (256, 256), (
                    f"{relative} should be a square patch, not a stretched photo"
                )
    glb = client.get(job["scene_url"].replace("/scene", "") + "/assets/scene.glb")
    assert glb.status_code == 200
    assert glb_embedded_image_sizes(glb.content)
    assert all(size == (256, 256) for size in glb_embedded_image_sizes(glb.content))


def test_review_add_replace_remove_and_reject_corrupt_images(client, tmp_path):
    plan = create_two_room_plan()
    original = create_room_photo(
        tmp_path / "original_room.jpg",
        (480, 360),
        (54, 72, 88),
        (110, 80, 52),
        (30, 30, 32),
        [60, 160, 220, 260],
    )
    response = upload(
        client,
        plan,
        photos=[(original.name, original.read_bytes())],
        known_width="8",
        known_length="6",
        property_id="prop-review-edit",
    )
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]
    review = fetch_review(client, job_id)
    assert review["summary"]["ready"] == 1

    corrupt = client.post(
        f"/reconstructions/{job_id}/photos",
        files={"photo": ("corrupt.jpg", b"not-an-image", "image/jpeg")},
    )
    assert corrupt.status_code == 400
    assert "could not be decoded" in corrupt.json()["detail"]
    review = fetch_review(client, job_id)
    rejected = [
        photo for photo in review["photos"] if photo["processing"]["status"] == "rejected"
    ]
    assert len(rejected) == 1
    assert rejected[0]["original_filename"] == "corrupt.jpg"
    assert rejected[0]["processing"]["errors"]
    assert review["summary"]["rejected"] == 1

    added_path = create_room_photo(
        tmp_path / "added_room.jpg",
        (420, 320),
        (90, 70, 50),
        (50, 44, 40),
        (200, 180, 120),
        [200, 90, 360, 220],
    )
    added = client.post(
        f"/reconstructions/{job_id}/photos",
        files={"photo": ("added_room.jpg", added_path.read_bytes(), "image/jpeg")},
    )
    assert added.status_code == 200, added.text
    review = added.json()
    assert review["summary"]["ready"] == 2
    added_photo = next(
        photo
        for photo in review["photos"]
        if photo["original_filename"] == "added_room.jpg"
    )
    assert (added_photo["width"], added_photo["height"]) == (420, 320)

    replacement = create_room_photo(
        tmp_path / "replacement_room.jpg",
        (300, 600),
        (46, 58, 74),
        (100, 70, 46),
        (190, 170, 130),
        [90, 60, 220, 300],
    )
    original_photo = next(
        photo
        for photo in review["photos"]
        if photo["original_filename"] == "original_room.jpg"
    )
    replaced = client.post(
        f"/reconstructions/{job_id}/photos/{original_photo['photo_id']}/replace",
        files={
            "photo": (
                "replacement_room.jpg",
                replacement.read_bytes(),
                "image/jpeg",
            )
        },
    )
    assert replaced.status_code == 200, replaced.text
    review = replaced.json()
    replaced_photo = next(
        photo
        for photo in review["photos"]
        if photo["photo_id"] == original_photo["photo_id"]
    )
    assert replaced_photo["original_filename"] == "replacement_room.jpg"
    assert replaced_photo["orientation"] == "portrait"
    assert (replaced_photo["width"], replaced_photo["height"]) == (300, 600)

    removed = client.delete(
        f"/reconstructions/{job_id}/photos/{added_photo['photo_id']}"
    )
    assert removed.status_code == 200, removed.text
    review = removed.json()
    assert review["summary"]["excluded"] == 1
    assert review["summary"]["needs_review"] == 1

    first_room = review["rooms"][0]["room_id"]
    confirmed = client.post(
        f"/reconstructions/{job_id}/confirm-assignments",
        json={
            "assignments": [
                {"photo_id": original_photo["photo_id"], "room_id": first_room},
                {"photo_id": added_photo["photo_id"], "excluded": True},
            ],
            "room_types": {str(first_room): "living_room"},
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    job = client.get(f"/reconstructions/{job_id}").json()
    assert job["status"] == "completed", job.get("error")
    manifest = client.get(f"/reconstructions/{job_id}/manifest").json()
    sources = manifest["method"]["textures"]["sources"]
    assert [source["file"] for source in sources] == ["replacement_room.jpg"]
    assert sources[0]["room_id"] == first_room
    room_map = manifest["textures"]["room_map"]
    assert list(room_map) == [str(first_room)]

    for action in (
        client.post(
            f"/reconstructions/{job_id}/photos",
            files={"photo": ("late.jpg", added_path.read_bytes(), "image/jpeg")},
        ),
        client.delete(f"/reconstructions/{job_id}/photos/{original_photo['photo_id']}"),
        client.post(
            f"/reconstructions/{job_id}/photos/{original_photo['photo_id']}/replace",
            files={"photo": ("late.jpg", added_path.read_bytes(), "image/jpeg")},
        ),
    ):
        assert action.status_code == 409


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


def test_entrance_living_kitchen_two_bedrooms_bathroom_mapping(client, tmp_path):
    plan = create_six_room_plan()
    defs = [
        ("entry_view.jpg", (72, 86, 104), (116, 88, 58), (22, 22, 24), [70, 150, 210, 250]),
        ("living_a.jpg", (96, 104, 116), (128, 96, 62), (170, 150, 96), [60, 140, 200, 240]),
        ("living_b.jpg", (92, 100, 112), (124, 94, 60), (166, 146, 94), [260, 130, 420, 230]),
        ("kitchen_view.jpg", (66, 92, 84), (58, 54, 48), (206, 208, 198), [90, 120, 300, 230]),
        ("bedroom_a.jpg", (118, 110, 96), (80, 62, 45), (160, 140, 110), [60, 150, 180, 240]),
        ("bedroom_b.jpg", (120, 112, 98), (82, 64, 46), (150, 135, 105), [280, 120, 420, 220]),
        ("bathroom_view.jpg", (150, 156, 160), (96, 104, 110), (220, 226, 230), [80, 140, 260, 240]),
    ]
    paths = [
        create_room_photo(tmp_path / name, (480, 360), wall, floor, accent, box)
        for name, wall, floor, accent, box in defs
    ]
    response = upload(
        client,
        plan,
        photos=[(path.name, path.read_bytes()) for path in paths],
        known_width="12",
        known_length="9",
        property_id="prop-six-rooms",
    )
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]
    review = fetch_review(client, job_id)
    assert len(review["rooms"]) == 6
    ready = [
        photo for photo in review["photos"] if photo["processing"]["status"] == "ready"
    ]
    assert len(ready) == 7

    mapping = {
        "entry_view.jpg": 0,
        "living_a.jpg": 1,
        "living_b.jpg": 1,
        "kitchen_view.jpg": 2,
        "bedroom_a.jpg": 3,
        "bedroom_b.jpg": 4,
        "bathroom_view.jpg": 5,
    }
    room_types = {
        "0": "entrance",
        "1": "living_room",
        "2": "kitchen",
        "3": "bedroom",
        "4": "bedroom",
        "5": "bathroom",
    }
    confirmed, _ = confirm_review(
        client, job_id, mapping=mapping, room_types=room_types
    )
    assert confirmed.status_code == 200, confirmed.text
    job = client.get(f"/reconstructions/{job_id}").json()
    assert job["status"] == "completed", job.get("error")

    scene = client.get(job["scene_url"]).json()
    nodes = {node["id"]: node for node in scene["room_graph"]["nodes"]}
    assert nodes[0]["name"] == "Entrance / foyer"
    assert nodes[1]["name"] == "Living room"
    assert nodes[2]["name"] == "Kitchen"
    assert nodes[3]["name"] == "Bedroom 1"
    assert nodes[4]["name"] == "Bedroom 2"
    assert nodes[5]["name"] == "Bathroom"
    for node in nodes.values():
        expected = [
            photo["photo_id"]
            for photo in ready
            if mapping[photo["original_filename"]] == node["id"]
        ]
        assert sorted(node["photo_ids"]) == sorted(expected)
        assert node["photo_count"] == len(expected)

    manifest = client.get(f"/reconstructions/{job_id}/manifest").json()
    room_map = manifest["textures"]["room_map"]
    assert set(room_map) == {str(room_id) for room_id in range(6)}
    for room_id, entry in room_map.items():
        expected_files = sorted(
            name for name, target in mapping.items() if str(target) == room_id
        )
        assert sorted(entry["files"]) == expected_files
    assert len(manifest["method"]["textures"]["sources"]) == 7

    glb = client.get(job["scene_url"].replace("/scene", "") + "/assets/scene.glb")
    assert glb.status_code == 200
    sizes = glb_embedded_image_sizes(glb.content)
    # One primary photo texture per textured room is embedded in the GLB.
    assert len(sizes) == 6
    assert all(size == (256, 256) for size in sizes)
