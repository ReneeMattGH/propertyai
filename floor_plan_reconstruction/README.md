# RealEstAI reconstruction engine

FastAPI service that turns an uploaded 2D floor plan plus interior photographs
into a validated, walkable GLB scene. It runs on CPU with no external
photogrammetry service or credentials.

## What is real (and what is not)

Implemented and verified:

* upload validation: extension/type, readable bytes, size, image dimensions;
* floor-plan parsing: normalisation, denoising, Otsu/adaptive wall candidates,
  polarity handling for dark-on-light and light-on-dark plans, structural line
  extraction, doorway-gap scanning with controlled adaptive sealing, staged
  room segmentation that never discards a room because of a door-sized gap,
  doorway/window classification, scale from supplied dimensions (or a stated
  estimate), and per-room derived-area reporting;
* automatic failure diagnostics per job: normalised plan, wall mask, sealing
  barrier, barrier overlay, detected walls/rooms/openings, segmentation
  attempts and `parse_failure.json`, exposed through the diagnostics API;
* failed jobs can be retried with preprocessing alternatives
  (`wall_polarity: auto|dark|light`, `sealing: auto|minimal|aggressive`) or
  corrected with user-drawn axis-aligned room rectangles plus doorways; the
  original upload is retained and re-used;
* real structure-from-motion with COLMAP (`pycolmap`, CPU SIFT features,
  exhaustive matching, incremental mapping) over the uploaded photographs:
  processed images, duplicate detection (average hash), sharpness scores,
  unreadable-image rejection, registered cameras, sparse point cloud
  (`sparse_points.ply`), camera poses (`cameras.json`), reprojection error,
  and honest `unavailable | failed | insufficient_overlap | partial |
  reconstructed` statuses;
* dense multi-view stereo only when the COLMAP build has CUDA. This
  environment is CPU-only, so `dense.status = "unavailable"` is reported and
  the job limitation says so;
* per-photograph texture crops (wall and floor) embedded in the GLB with UV
  mapping, so appearance comes from the actual uploads;
* watertight floors/ceilings, plan-constrained wall boxes with door openings,
  window openings with inferred sill/head heights and tinted glazing, collision
  AABBs (including window barriers), a walkable spawn point, and a room
  connectivity graph;
* GLB export that is re-opened and validated (loadable mesh, embedded photo
  textures, metadata job match, collision, spawn, doorways, room graph) before
  a job can complete;
* per-job provenance (`schema_version 2.0`): property ID, job ID, exact input
  filenames/sizes/SHA-256, stage records, engine/version, output asset hashes,
  warnings, inferred surfaces and limitations.

Not implemented:

* dense image-derived meshes (no CUDA in this environment) and NeRF/Gaussian
  splatting;
* photogrammetry geometry as the walkthrough layout: camera estimation is real
  and reported, but layout remains constrained by the floor plan because there
  is no dense mesh to replace it;
* windows beyond what the parser can identify (rendered windows have inferred
  heights), stairs, multi-floor reconstruction, curved/oblique walls, door
  leaves, furniture, and semantic room labels;
* manual correction beyond axis-aligned rectangular room outlines;
* metric scale from photographs alone; scale comes from the plan dimensions or
  a stated estimate and is flagged when not verified;
* exact photograph-to-room assignment; it is approximate (order/filename
  hints), and unseen surfaces are marked as inferred.

There is no sample-house fallback in the reconstruction or viewer path. The
synthetic fixtures below exist only for development and tests.

## Run

```bash
cd floor_plan_reconstruction
python3.11 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/uvicorn main:app --host 127.0.0.1 --port 8002
```

Environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `FRONTEND_URLS` | `http://localhost:3000,http://127.0.0.1:3000` | Comma-separated CORS origins (add alternate dev/prod ports) |

Health check: `curl http://localhost:8002/health`. The response reports
OpenCV, trimesh, pycolmap version, `photogrammetry_available`,
`photogrammetry_cuda` and `dense_mvs_available`. PDF floor plans additionally
need `pdf2image` and poppler (`pdftoppm` on PATH).

## API

| Method | Path | Meaning |
| --- | --- | --- |
| POST | `/reconstructions` | multipart: `project_name`, `floor_plan`, `photos[]`, `num_floors`, optional `known_width`/`known_length` |
| GET | `/reconstructions` | list jobs (summary) |
| GET | `/reconstructions/{id}` | status, progress, diagnostics, warnings, limitations |
| POST | `/reconstructions/{id}/cancel` | request cancellation |
| POST | `/reconstructions/{id}/retry` | rerun a failed, cancelled or partially navigable completed job from stored inputs; optional JSON `{"preprocessing": {"wall_polarity": "...", "sealing": "..."}}` |
| POST | `/reconstructions/{id}/manual-plan` | rerun a failed, cancelled or partially navigable completed job with user room rectangles and doorways (axis-aligned, image-pixel coordinates) |
| GET | `/reconstructions/{id}/floor-plan` | original uploaded plan (PNG/JPEG/PDF) for preview and correction |
| GET | `/reconstructions/{id}/diagnostics` | failure stage/error code, preprocessing options, parser diagnostics, saved artifacts, correction support |
| GET | `/reconstructions/{id}/diagnostics/{artifact}` | path-safe detection artifact (`walls_mask.png`, `barrier_overlay.png`, `rooms_detected.png`, `parse_failure.json`, ...) |
| GET | `/reconstructions/{id}/scene` | GLB URL, collision volumes, room graph, metadata, manifest (completed only) |
| GET | `/reconstructions/{id}/manifest` | provenance manifest: inputs, stages, outputs and SHA-256 hashes |
| GET | `/reconstructions/{id}/assets/{file}` | path-safe per-job asset download |
| GET | `/reconstructions/{id}/report` | validation report and diagnostics |
| GET | `/reconstructions/{id}/logs` | human-readable stage log |

Job stages: `queued → preprocessing → detecting_rooms → validating_layout →
analyzing_photos → reconstructing_images → building_geometry → texturing →
exporting → validating_assets → completed | failed`. Progress values come from
stage completion, not wall-clock guesses; a failure keeps progress below the
failed stage and reports `failed_stage` plus a stable `error_code`.

Stage records written to the manifest remain `input_validation →
floor_plan_parse → photo_analysis → photogrammetry → geometry_build →
scene_validation → manifest`. A job is only marked `completed` after the
exported GLB, embedded textures, per-job metadata, collision data and room
graph pass `scene_validator.py`; otherwise it ends in `validation_failed` with
the failed checks.

## Fixtures

The synthetic fixtures are development/test-only and are never presented as a
user's property:

```bash
venv/bin/python create_test_floor_plan.py    # input/test_floor_plan.png (12x9 m)
venv/bin/python create_test_photos.py        # input/test_*.jpg
venv/bin/python create_e2e_properties.py     # two-room plan + palette photos
```

The real SfM test photographs live in `tests/assets/sfm/` (six overlapping
photos, downscaled, with attribution in that directory's README).

## Tests

```bash
venv/bin/pytest -q
```

Covers the parser (clean/thick/thin walls, door gaps, light-on-dark plans,
low contrast and noise, honest failures), the real-world regression plan
(`tests/fixtures/real-world/`), window geometry and collision, manual outline
correction, geometry/GLB validation, real COLMAP reconstruction,
duplicate/rejected/insufficient-overlap reporting, and the upload → failure →
diagnostics → correction → scene API flow.

The browser E2E tests live in `frontend/scripts/` and require this engine on
port 8002 plus the Next.js frontend on port 3000:

```bash
cd ../frontend
npm run test:e2e
```

`test:e2e` covers two real uploads with different floor plans and photos,
desktop and mobile navigation, duplicate-photo reporting, and the failure UI
with manual room-outline correction.
