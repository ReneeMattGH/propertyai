# RealEstAI

Turn a property's 2D floor plan and interior photographs into a validated,
interactive 3D walkthrough.

This repository contains:

| Directory | Role |
| --- | --- |
| `floor_plan_reconstruction/` | CPU reconstruction engine (FastAPI, :8002): parsing, COLMAP SfM, scene export, validation, provenance |
| `frontend/` | Next.js 16 + React 19 + Three.js viewer (:3000), upload flow, tour pages |
| `backend/` | FastAPI + PostgreSQL application API (:8000) for properties/media/jobs |
| `reconstruction/` | Legacy prototype assets; not used by the current pipeline |

The walkthrough is produced by the reconstruction engine. The backend/auth
application is not required to create or view a reconstruction.

## What the reconstruction actually does

A job follows these stages, each recorded in the job and its manifest:

```
input_validation → floor_plan_parse → photo_analysis → photogrammetry
→ geometry_build → scene_validation → manifest
```

* **Floor plan** — validates the uploaded bytes, extracts walls/rooms/doorways,
  verifies or estimates scale, and derives room areas.
* **Photographs** — validates and preprocesses each upload (EXIF orientation,
  resize, average-hash duplicate detection, sharpness scoring, unreadable-image
  rejection), then runs real COLMAP structure-from-motion (pycolmap CPU SIFT,
  exhaustive matching, incremental mapping). The job reports registered
  cameras, sparse points, reprojection error, and an honest status:
  `reconstructed`, `partial`, `insufficient_overlap`, `failed` or `unavailable`.
* **Geometry** — builds plan-constrained floors, ceilings, wall boxes with door
  openings, collision volumes, a spawn point and a room connectivity graph.
  Wall/floor textures are real crops from the uploaded photographs, UV-mapped
  into the GLB.
* **Validation** — reopens the exported GLB, checks meshes/faces/bounds, embedded
  photo textures, per-job metadata, collision, spawn, passable doorways and the
  room graph. A job is only marked `completed` after these checks pass.
* **Provenance** — the per-job manifest (`schema_version 2.0`) records the
  property ID, job ID, exact input filenames/sizes/SHA-256, stages actually run,
  engine version, output asset hashes, warnings, inferred surfaces and
  limitations.

There is **no sample-house fallback**: the viewer only loads the scene for the
requested completed job, and a completed job without valid assets is reported
as an error. The synthetic fixtures in the engine are development/test-only.

### Honest limitations

* Dense multi-view stereo requires CUDA. This environment is CPU-only, so
  `dense.status = "unavailable"` is reported; the walkthrough uses plan
  geometry with real photo textures, not a dense image-derived mesh.
* Camera poses are real, but photogrammetry geometry is not used as the layout;
  the floor plan constrains walls/rooms. Plan/photo aspect conflicts and poor
  overlap are surfaced as warnings/limitations.
* Windows, stairs, multi-floor, curved walls, door leaves, furniture and
  semantic room labels are not reconstructed. Photograph-to-room texture
  assignment is approximate; unseen surfaces (e.g. ceilings) are inferred.
* Scale is verified only when the plan provides it; otherwise it is estimated
  and flagged.

## Prerequisites

* Node.js 20+ (tested with Node 24)
* Python 3.11
* PostgreSQL 14+ and a `realestai` database if you use the application backend
* Optional: poppler (`pdftoppm`) for PDF floor plans; CUDA COLMAP for dense MVS

## Run locally

### 1. Reconstruction engine (`:8002`)

```bash
cd floor_plan_reconstruction
python3.11 -m venv venv
venv/bin/pip install -r requirements.txt
FRONTEND_URLS=http://localhost:3000,http://127.0.0.1:3000 \
  venv/bin/uvicorn main:app --host 127.0.0.1 --port 8002
```

Health: `curl http://localhost:8002/health` — confirm `photogrammetry_available:
true` and note `photogrammetry_cuda` / `dense_mvs_available`.

### 2. Frontend (`:3000`)

```bash
cd frontend
npm install
cp env.example .env.local   # set NEXT_PUBLIC_RECONSTRUCTION_URL=http://localhost:8002
npm run dev
```

Open `http://localhost:3000`, go to **New reconstruction**, upload a floor plan
image/PDF and interior photographs, and wait for the job to complete. The tour
page streams progress from the engine and then loads that job's GLB.

### 3. Application backend (`:8000`, optional)

```bash
cd backend
python3.11 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env        # configure database and Supabase values
venv/bin/alembic upgrade head
venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
```

The frontend can explore the property/tour pages with placeholder Supabase
credentials; real authentication requires a Supabase project.

## Controls

* Desktop: click to capture the pointer, mouse look, `W/A/S/D` or arrow keys,
  `Shift` to run, `Esc` to release.
* Mobile/touch: movement joystick, drag to look.
* Room navigation is limited to connected, walkable rooms from the parsed plan;
  collision uses the exported wall boxes.

## Tests

Engine (real COLMAP + API + validator, synthetic fixtures only in tests):

```bash
cd floor_plan_reconstruction
venv/bin/pytest -q
```

Frontend unit/lint/type/build:

```bash
cd frontend
npm test
npm run lint
npx tsc --noEmit
npm run build
```

Browser E2E (requires engine on `:8002` and the dev frontend on `:3000`):

```bash
cd frontend
npm run test:e2e
```

The desktop E2E uploads two different properties through the UI, verifies the
exact uploaded files by SHA-256, COLMAP registration, photo textures, per-job
GLB identity, room navigation through a doorway, duplicate-photo reporting and
the absence of demo fallback. The mobile E2E verifies the joystick and
touch-look controls. A production smoke test can be run against a built app:

```bash
FRONTEND_URL=http://127.0.0.1:3001 node frontend/scripts/smoke-production.mjs
```

(remember to add `http://127.0.0.1:3001` to `FRONTEND_URLS` for the engine).

## Test assets

The overlap photographs used by automated tests are six real photos from the
public HuggingFace dataset `alexmkwizu/colmap-testing-dataset`, downscaled to
1024 px. Attribution and provenance are in
`floor_plan_reconstruction/tests/assets/sfm/README.txt`. The floor plans used in
tests are synthetic fixtures generated by
`floor_plan_reconstruction/create_*.py` and are never shown as user data.
