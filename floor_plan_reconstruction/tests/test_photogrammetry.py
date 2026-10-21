"""Real photogrammetry-stage tests.

The overlapping photographs under ``tests/assets/sfm`` are a reduced set of the
public COLMAP test dataset (see the README there). These tests exercise the
actual pycolmap pipeline; nothing is mocked or replayed from a cached model.
"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from photogrammetry import PhotogrammetryEngine

SFM_ASSETS = Path(__file__).parent / "assets" / "sfm"


@pytest.fixture(scope="session")
def sfm_photos() -> list[Path]:
    paths = sorted(SFM_ASSETS.glob("overlap_*.jpg"))
    assert len(paths) == 6, "the overlap fixture should contain six photographs"
    return paths


def run_reconstruction(tmp_path: Path, job_id: str, photo_paths: list[Path]):
    engine = PhotogrammetryEngine(workspace_root=tmp_path)
    return asyncio.run(
        engine.reconstruct(
            job_id,
            [str(path) for path in photo_paths],
            {"footprint_m": {"width": 12.0, "length": 9.0}},
        )
    )


def test_real_overlapping_photos_produce_camera_poses_and_sparse_points(
    tmp_path, sfm_photos
):
    job_id = "pytest-sfm-overlap"
    result = run_reconstruction(tmp_path, job_id, sfm_photos)

    assert result["status"] in {"reconstructed", "partial"}, result
    assert result["stages"]["preprocess"] is True
    assert result["stages"]["feature_extraction"] is True
    assert result["stages"]["feature_matching"] is True
    assert result["stages"]["mapping"] is True
    assert result["registered_images"] >= 4
    assert result["sparse_points"] >= 100
    assert result["matched_pairs"] > 0
    assert result["inlier_matches"] > 0
    assert result["mean_reprojection_error_px"] is not None
    assert result["mean_reprojection_error_px"] < 2.0
    assert result["engine"]["name"] == "COLMAP"
    assert result["engine"]["version"]

    uploaded = {path.name for path in sfm_photos}
    assert {entry["file"] for entry in result["images"]} == uploaded
    assert result["duplicate_images"] == []
    assert result["rejected_images"] == []

    assert len(result["cameras"]) == result["registered_images"]
    for camera in result["cameras"]:
        assert len(camera["rotation_quaternion_xyzw"]) == 4
        assert len(camera["translation"]) == 3
        assert len(camera["center"]) == 3
        assert camera["num_points3d"] > 0
        assert camera["camera_model"]

    workspace = tmp_path / job_id / "photogrammetry"
    ply_path = workspace / "sparse_points.ply"
    assert ply_path.exists()
    assert ply_path.read_bytes()[:3] == b"ply"
    cameras_path = workspace / "cameras.json"
    assert len(json.loads(cameras_path.read_text())) == result["registered_images"]
    saved = json.loads((workspace / "photogrammetry.json").read_text())
    assert saved["status"] == result["status"]
    assert saved["sparse_points"] == result["sparse_points"]

    if not result["engine"].get("cuda_available"):
        assert result["dense"]["status"] == "unavailable"
        assert "CUDA" in result["dense"]["reason"]
        assert result["stages"]["dense_stereo"] is False


def test_non_overlapping_photos_report_insufficient_overlap(
    tmp_path, fixture_photos
):
    result = run_reconstruction(tmp_path, "pytest-sfm-flat", list(fixture_photos))

    assert result["status"] == "insufficient_overlap"
    assert result["stages"]["preprocess"] is True
    assert result["stages"]["feature_extraction"] is True
    assert result["registered_images"] < 2
    assert result["cameras"] == []
    assert any(
        "could not register" in warning.lower() or "overlap" in warning.lower()
        for warning in result["warnings"]
    )
    assert not (tmp_path / "pytest-sfm-flat" / "photogrammetry" / "sparse_points.ply").exists()


def test_duplicate_photos_are_excluded_and_reported(tmp_path, fixture_photos):
    source = fixture_photos[0]
    first = tmp_path / "same_view_a.jpg"
    second = tmp_path / "same_view_b.jpg"
    shutil.copyfile(source, first)
    shutil.copyfile(source, second)

    result = run_reconstruction(tmp_path, "pytest-sfm-duplicates", [first, second])

    assert result["status"] == "insufficient_overlap"
    assert len(result["duplicate_images"]) == 1
    assert result["duplicate_images"][0]["duplicate_of"] == first.name
    assert [entry["file"] for entry in result["images"]] == [first.name]
    assert any("duplicate" in warning.lower() for warning in result["warnings"])
    assert any(
        "two distinct" in warning.lower() for warning in result["warnings"]
    )


def test_unreadable_photo_is_rejected_with_the_real_cause(tmp_path, fixture_photos):
    good = tmp_path / "usable.jpg"
    broken = tmp_path / "broken.jpg"
    shutil.copyfile(fixture_photos[0], good)
    broken.write_bytes(b"this is not a jpeg")

    result = run_reconstruction(tmp_path, "pytest-sfm-broken", [good, broken])

    assert result["status"] == "insufficient_overlap"
    assert len(result["rejected_images"]) == 1
    assert result["rejected_images"][0]["file"] == broken.name
    assert result["rejected_images"][0]["error"]
    assert any("could not be used" in warning for warning in result["warnings"])
