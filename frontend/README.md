# Crestion Labs Studio

The production side of Crestion Labs. Clients submit a floor plan on the portal;
this site receives it, builds the walkthrough with the local reconstruction
engine, and sends it back.

## Running it

Two processes, in this order:

```bash
# 1. the engine (from ../floor_plan_reconstruction)
venv/bin/uvicorn main:app --port 8002

# 2. this site
npm install
npm run dev
```

The studio reads `NEXT_PUBLIC_RECONSTRUCTION_URL` (default `http://localhost:8002`)
and talks to nothing else. Everything — job store, GLB output, diagnostics — lives
on the engine, so this site has no database of its own.

## Environment

Copy `env.example` to `.env.local`.

| Variable | Required | Purpose |
| --- | --- | --- |
| `NEXT_PUBLIC_RECONSTRUCTION_URL` | yes | Engine base URL |
| `NEXT_PUBLIC_SUPABASE_URL` | no | Enables sign-in; without it the studio runs unsigned-in |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | no | Same as above |

The engine side needs `CRESTION_PORTAL_URL` and `PRODUCTION_BACKEND_SECRET` to
deliver finished worlds back to the portal — those are set on the engine, not here.

## Routes

| Route | What it does |
| --- | --- |
| `/` | Product overview |
| `/requests` | Client submissions from the portal; start a build, review room types, send the world back |
| `/create-tour` | Upload a plan directly and drive the pipeline by hand |
| `/dashboard` | Jobs on this engine, with live status and retries |
| `/tours` | Every stored walkthrough, including failed attempts |
| `/tour/[id]` | First-person viewer: lighting, furniture, ceiling, quality, room jump, fullscreen |
| `/auth/signin`, `/auth/signup` | Optional Supabase accounts |

## The bridge

`portal submission → /requests → build → Send to client → portal Deliverables`

Submissions arrive over the engine's `/intake/requests` endpoints, signed with
`PRODUCTION_BACKEND_SECRET`. Sending a finished world posts to the portal's
`/api/production/deliver`, which stores `scene.glb` and `collision.json`, mints a
share slug, and returns the shareable link and embed code the client sees.

## Notes

- Authentication is a gate, not a dependency. Every reconstruction tool works
  without Supabase configured; the sign-in screens say so rather than breaking.
- `middleware.ts` does not validate sessions. This site is meant for a studio
  machine or an authenticated network, not the public internet.
