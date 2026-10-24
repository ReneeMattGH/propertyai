"""Shared fixtures for the reconstruction engine tests."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import main  # noqa: E402
from create_test_floor_plan import create_test_floor_plan  # noqa: E402
from create_test_photos import create_test_room_photo  # noqa: E402

ROOMS = ("living_room", "kitchen", "bedroom", "bathroom")


@pytest.fixture(scope="session")
def fixture_plan(tmp_path_factory) -> Path:
    target = tmp_path_factory.mktemp("fixtures") / "test_floor_plan.png"
    create_test_floor_plan(str(target))
    return target


@pytest.fixture(scope="session")
def fixture_photos(tmp_path_factory) -> list[Path]:
    directory = tmp_path_factory.mktemp("photos")
    paths = []
    for room in ROOMS:
        target = directory / f"{room}.jpg"
        create_test_room_photo(str(target), room)
        paths.append(target)
    return paths


@pytest.fixture
def cleanup_job_dirs():
    """Remove per-job input/output/diagnostic directories created by a test."""
    job_ids: list[str] = []
    yield job_ids
    for job_id in job_ids:
        shutil.rmtree(ENGINE_DIR / "input" / job_id, ignore_errors=True)
        shutil.rmtree(ENGINE_DIR / "output" / job_id, ignore_errors=True)
        shutil.rmtree(ENGINE_DIR / "geometry" / job_id, ignore_errors=True)


@pytest.fixture
def api_workspace(tmp_path, monkeypatch):
    """Isolate job state and uploads; generated assets stay in ENGINE_DIR/output."""
    import photo_manifest

    for attribute in ("JOBS_FILE",):
        monkeypatch.setattr(main, attribute, tmp_path / "jobs.json")
    for attribute in ("INPUT_DIR", "FLOOR_PLANS_DIR", "PHOTOS_DIR", "LOGS_DIR"):
        directory = tmp_path / attribute.lower()
        directory.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(main, attribute, directory)
    # The photo manifest resolves originals through its own module constant.
    monkeypatch.setattr(photo_manifest, "INPUT_DIR", main.INPUT_DIR)
    yield tmp_path
    for job in main.load_jobs().values():
        job_id = job.get("id")
        if not job_id:
            continue
        shutil.rmtree(ENGINE_DIR / "input" / job_id, ignore_errors=True)
        shutil.rmtree(ENGINE_DIR / "output" / job_id, ignore_errors=True)
        shutil.rmtree(ENGINE_DIR / "geometry" / job_id, ignore_errors=True)


@pytest.fixture
def client(api_workspace):
    from fastapi.testclient import TestClient

    return TestClient(main.app)


@pytest.fixture
def stub_pipeline(monkeypatch):
    class NullPipeline:
        async def run(self, *args, **kwargs):
            return None

        async def reconstruct(self, *args, **kwargs):
            return None

        async def prepare_floor_plan(self, *args, **kwargs):
            return None

        async def reconstruct_furnished(self, *args, **kwargs):
            return None

    monkeypatch.setattr(main, "get_pipeline", lambda: NullPipeline())
