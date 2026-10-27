# RealEstAI

Turn a 2D floor plan into a furnished, validated, interactive 3D walkthrough.

Upload one floor-plan image (PNG/JPEG, optional PDF when poppler is installed),
confirm the inferred room types and style, and RealEstAI builds the house,
furnishes it, proves the rooms are connected and walkable, and opens the tour.

No interior photographs, sample houses, or prebuilt scenes are used. The
original upload is kept unchanged, and the walkthrough only loads the assets
generated for the requested job.

## Repository layout

| Directory | Role |
| --- | --- |
| `floor_plan_reconstruction/` | Floor-plan reconstruction engine (FastAPI, `:8002`): parsing, structural repair, geometry, furnishing, validation, GLB export, provenance |
| `frontend/` | Next.js 16 + React 19 + Three.js app (`:3000`): marketing, upload, room-type review, tour list, walkthrough viewer |
| `backend/` | Optional FastAPI + PostgreSQL application API (`:8000`) for accounts, properties, and media metadata |
| `reconstruction/` | Legacy prototype assets; not used by the current pipeline |

The reconstruction engine is the only service required to create and view a
walkthrough. The application backend and Supabase auth are optional for local
reconstruction work.

## Pipeline

Every job records its stages and progress in the job record and in the final
manifest:

```
parsing_floor_plan → detecting_rooms_openings → validating_room_layout
→ awaiting_room_types → optimizing_openings → generating_geometry
→ placing_furniture → generating_lighting_materials
→ validating_navigation_collisions → loading_walkthrough → completed
```

* **Floor plan** — validates the upload, detects and corrects small rotations
  (up to ±20°), extracts walls, rooms, doorways and windows, verifies or
  estimates scale, and derives room polygons and areas.
* **Structural reconciliation** — before geometry is built, detected openings
  are reconciled: duplicate reports of one gap are merged, a doorway wins over
  a window on the same gap, and an implausibly narrow exterior "door" is kept
  as a window rather than cutting a new hole through the envelope.
* **Connection inference** — when two rooms share a real wall face and the
  layout is plausible, a missing doorway can be inferred. Inference only
  happens when the shared face can hold a usable opening, no window or door
  already occupies it, the connection is sensible for the room types, and the
  opening meets the minimum passage width. It never bridges the exterior
  boundary, never turns a bathroom into a thoroughfare, and never invents
  rooms or walls. Each repair is logged with its evidence.
* **Bounded repair loop** — geometry, furniture and navigation are validated,
  diagnosed, repaired and regenerated for up to 3 attempts or 120 seconds
  (`REPAIR_MAX_ATTEMPTS`, `REPAIR_TIME_BUDGET_S`). Repairs include spawn
  placement, collision alignment, doorway clearance, and furniture conflicts.
  If the scene still cannot be made walkable, the job fails honestly with
  diagnostics instead of publishing a broken walkthrough.
* **Geometry** — plan-constrained floors, ceilings, walls with real openings,
  window panes, cased doorways, collision volumes, a spawn point and a room
  connectivity graph are exported as a GLB with a scene bundle.
* **Furnishing** — room types are inferred from the plan, then a deterministic
  procedural asset library furnishes each room in the selected style. Placement
  respects walls, doorways, circulation and existing furniture; furniture is
  never allowed to change room boundaries or block a required passage.
* **Validation** — reopens the exported GLB and checks meshes, bounds,
  collision, spawn, doorway clearance, room reachability, furniture placement
  and assets. A job is marked `completed` only after these checks pass.

Room-type confirmation lets you correct an inferred label or choose a style.
It never asks you to draw doors, connect rooms, or fix the plan by hand.

If a plan genuinely cannot be interpreted (for example, no enclosed rooms
found or the image is too small), the job fails with an explanation and, where
available, optional retry preprocessing or a manual room-outline correction
path. These are recovery paths, not part of normal operation.

## Provenance and assets

The per-job manifest (`schema_version 3.0`, `workflow: floor_plan_only`)
records the property and job IDs, input filename/size/SHA-256, engine version,
stages actually run, output asset hashes, room types, furnishing, repairs,
validation results, warnings, inferred surfaces and limitations. The scene
bundle served to the viewer carries the same job identity.

## Honest limitations

* Geometry is axis-aligned. Curved or strongly angled walls, split levels and
  multi-storey plans are not modelled; a single storey is generated.
* Deskewing handles plans rotated up to ±20°. Severely perspective-distorted
  scans, very low-resolution images (short side under 250 px) and heavily
  stylised plans may be rejected or misread.
* Scale is only verified when dimensions are supplied with the upload;
  otherwise it is estimated and marked as approximate.
* Very open plans without enough wall enclosure can fail with
  `no_enclosed_rooms` and require retry or manual room outlines.
* Door leaves, stairs, ramps, balconies as separate structures and fine
  architectural trim are not reconstructed.
* Windows are inferred as openings with sill/head panes, not measured glazing.
* Furniture is procedural and deterministic, not a catalog of real products.
* An inferred doorway is a best-effort structural repair validated against the
  detected walls; it is rendered and physically navigable, but it is not a
  claim about the real building.
* The walkthrough is a navigable architectural approximation, not a
  photorealistic survey.

## Prerequisites

* Node.js 20+ (tested with Node 24)
* Python 3.11
* PostgreSQL 14+ and a `realestai` database only if you use the optional
  application backend
* Optional: poppler (`pdftoppm`) for PDF floor plans

## Run locally

### 1. Reconstruction engine (`:8002`)

```bash
cd floor_plan_reconstruction
python3.11 -m venv venv
venv/bin/pip install -r requirements.txt
FRONTEND_URLS=http://localhost:3000,http://127.0.0.1:3000 \
  venv/bin/uvicorn main:app --host 127.0.0.1 --port 8002
```

Health: `curl http://localhost:8002/health` — confirm `status` is healthy and
check `pdf_floor_plans` and `furnishing` capabilities.

### 2. Frontend (`:3000`)

```bash
cd frontend
npm install
cp env.example .env.local   # set NEXT_PUBLIC_RECONSTRUCTION_URL=http://localhost:8002
npm run dev
```

Open `http://localhost:3000`, choose **New reconstruction**, upload a floor
plan, confirm the room types and style, and wait for the walkthrough. The tour
page polls the engine for progress and then loads that job's GLB.

### 3. Application backend (`:8000`, optional)

```bash
cd backend
python3.11 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env        # configure database and Supabase values
venv/bin/alembic upgrade head
venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
```

The frontend can browse property and tour pages with placeholder Supabase
credentials; real authentication requires a Supabase project.

## Controls

* Desktop: click to capture the pointer, mouse look, `W/A/S/D` or arrow keys,
  `Shift` to run, `Esc` to release.
* Mobile/touch: movement joystick, drag to look, room list in the options menu.
* Room navigation is limited to connected, walkable rooms from the generated
  plan; collision uses the exported wall, door and furniture volumes.
* Debug geometry is not shown in the buyer-facing walkthrough.

## Tests

Engine (parser, structural repair, geometry, furnishing, validation, API):

```bash
cd floor_plan_reconstruction
venv/bin/pytest -q
```

Backend:

```bash
cd backend
venv/bin/pytest -q
```

Frontend unit, lint, type check and build:

```bash
cd frontend
npm test
npm run lint
npx tsc --noEmit
npm run build
```

Browser E2E (engine on `:8002`, dev frontend on `:3000`):

```bash
cd frontend
npm run test:e2e
```

The desktop E2E uploads and rebuilds real properties through the UI, checks
the stage flow, room review, GLB identity, collision and doorway navigation.
The mobile E2E verifies the joystick and touch-look controls. The
failure-correction E2E checks that unreadable or uninterpretable plans fail
with diagnostics and recovery options rather than a fabricated scene.
`frontend/scripts/verify-repaired-walkthrough.mjs` walks a completed job
through every inferred doorway using the collision volumes and reports the
closest approach and final room.

A production smoke test can run against a built app:

```bash
FRONTEND_URL=http://127.0.0.1:3001 node frontend/scripts/smoke-production.mjs
```

(remember to add `http://127.0.0.1:3001` to `FRONTEND_URLS` for the engine).

## Test assets

The real-world parser regression plan is
`floor_plan_reconstruction/tests/fixtures/real-world/user-floor-plan-door-gaps.png`,
supplied by the project owner for testing. Its source, hash and purpose are in
`PROVENANCE.md` next to the file. Other parser fixtures are synthetic plans
generated by the test helpers and are never served as user reconstructions.
