"""Deterministic diagnose -> repair -> regenerate -> revalidate controller.

The reconstruction pipeline never publishes the first scene it happens to
build.  This controller validates the generated scene, classifies every
failure into a structured issue, applies the smallest safe repair that targets
that issue, regenerates only the affected artifacts, and then runs the full
validator again.  The loop is bounded by an attempt count and a wall-clock
budget.

Rules enforced here:

  * room boundaries and detected openings are never changed to make furniture
    fit; structural problems are repaired before meshes are generated;
  * furniture is moved first, optional furniture is removed second, and
    essential fixtures are only touched when nothing else keeps the route open;
  * a repair that cannot be tied to a diagnosed issue is not attempted;
  * validation can never be skipped or forced: every attempt ends with the
    real scene validator, and the job is only publishable when it passes.
"""
from __future__ import annotations

import copy
import inspect
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from furnishing import (
    RoomLayout,
    furniture_collision_items,
    is_essential_item,
    refresh_furnishing,
)
from house_model import (
    clear_spawn_in_room,
    normalize_house,
    resolve_spawn,
    select_spawn_room,
)

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_TIME_BUDGET_S = 120.0
MAX_VARIANT_RETRIES = 3
REQUIRED_ROOM_TYPES = {
    "living_room",
    "bedroom",
    "kitchen",
    "bathroom",
    "dining_room",
    "entrance",
}

FurnishCallback = Callable[[Dict, Dict, str, int, bool], Dict]
BuildCallback = Callable[[Dict, Dict, str], Awaitable[Dict]]
ValidateCallback = Callable[[Dict, Dict, Dict], Awaitable[Dict]]
ProgressCallback = Callable[[str, Dict], None]
LogCallback = Callable[[str], None]


class SceneRepairEngine:
    def __init__(
        self,
        max_attempts: Optional[int] = None,
        time_budget_s: Optional[float] = None,
    ):
        self.max_attempts = int(
            max_attempts
            if max_attempts is not None
            else os.environ.get("REPAIR_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS)
        )
        self.time_budget_s = float(
            time_budget_s
            if time_budget_s is not None
            else os.environ.get("REPAIR_TIME_BUDGET_S", DEFAULT_TIME_BUDGET_S)
        )
        self._relocated: set = set()

    async def run(
        self,
        *,
        job_id: str,
        plan: Dict,
        room_metadata: Dict[int, Dict],
        style: str,
        variant: int,
        furnish: FurnishCallback,
        build: BuildCallback,
        validate: ValidateCallback,
        progress: Optional[ProgressCallback] = None,
        log: Optional[LogCallback] = None,
        abort: Optional[Callable[[], bool]] = None,
    ) -> Dict:
        started = time.monotonic()
        log_entries: List[Dict] = []
        attempts: List[Dict] = []

        def emit(event: str, payload: Optional[Dict] = None):
            if progress is not None:
                try:
                    progress(event, payload or {})
                except Exception:  # noqa: BLE001 - progress must never break repairs
                    pass

        def note(message: str):
            if log is not None:
                try:
                    log(message)
                except Exception:  # noqa: BLE001
                    pass

        def elapsed() -> float:
            return time.monotonic() - started

        def budget_left() -> bool:
            return elapsed() < self.time_budget_s

        def record_attempt(phase: str, validation: Optional[Dict], diagnosis=None):
            entry = {
                "attempt": len(attempts),
                "phase": phase,
                "geometry_valid": bool((validation or {}).get("geometry_valid")),
                "navigation_available": bool(
                    (validation or {}).get("navigation_available")
                ),
                "errors": list((validation or {}).get("errors") or []),
                "checks": (validation or {}).get("checks") or {},
                "issues": ((validation or {}).get("stats") or {}).get("issues") or [],
                "diagnosis": diagnosis,
                "repairs": [],
                "elapsed_s": round(elapsed(), 3),
            }
            navigation = ((validation or {}).get("stats") or {}).get("navigation") or {}
            entry["navigation"] = {
                "unreachable_rooms": navigation.get("unreachable_rooms") or [],
                "unreachable_doors": navigation.get("unreachable_doors") or [],
                "reachable_area_m2": navigation.get("reachable_area_m2"),
            }
            attempts.append(entry)
            return entry

        normalization = normalize_house(plan, room_metadata=room_metadata)
        plan = normalization["plan"]
        model = normalization["model"]
        normalization_record = {
            "valid": normalization.get("valid"),
            "errors": normalization.get("errors") or [],
            "repairs": normalization.get("repairs") or [],
            "warnings": normalization.get("warnings") or [],
            "ambiguities": normalization.get("ambiguities") or [],
        }
        for repair in normalization_record["repairs"]:
            log_entries.append(
                {
                    **repair,
                    "stage": "normalize_house",
                    "source": "structure",
                    "result": "applied before meshes were generated",
                }
            )
        note(
            "House model normalized: "
            f"{len(normalization_record['repairs'])} structural repair(s), "
            f"{len(normalization_record['ambiguities'])} ambiguity record(s)"
        )
        if not normalization.get("valid"):
            record = record_attempt("structural_failure", None)
            record["repairs"] = [
                {
                    "code": "structural_rejection",
                    "detail": "; ".join(normalization.get("errors") or []),
                    "result": "rejected before geometry generation",
                }
            ]
            return self._result(
                ok=False,
                plan=plan,
                model=model,
                furnishing={},
                geometry=None,
                validation=None,
                attempts=attempts,
                repair_log=self._log(
                    job_id,
                    attempts,
                    log_entries,
                    normalization_record,
                    started,
                    status="failed",
                    error_code="structural_ambiguity",
                ),
                error_code="structural_ambiguity",
                message="The floor plan cannot be reconstructed reliably: "
                + "; ".join((normalization.get("errors") or [])[:5]),
                progress=emit,
            )

        current_variant = int(variant)
        furnishing: Dict = {}
        furnishing_error: Optional[str] = None
        try:
            furnishing = await self._call_furnish(
                furnish, plan, room_metadata, style, current_variant, False, abort
            )
        except Exception as exc:  # noqa: BLE001 - furnishing rejection is a diagnosis
            if abort is not None and abort():
                raise
            furnishing_error = str(exc)
            note(f"Strict furnishing rejected: {furnishing_error}")
            try:
                furnishing = await self._call_furnish(
                    furnish, plan, room_metadata, style, current_variant, True, abort
                )
                log_entries.append(
                    {
                        "code": "furniture_layout_relaxed",
                        "detail": (
                            "The strict furniture layout was rejected because it blocked a "
                            "walking route; optional pieces were removed until the room was "
                            "walkable."
                        ),
                        "result": "applied",
                        "stage": "furnishing",
                        "source": "furniture",
                    }
                )
            except Exception as exc2:  # noqa: BLE001
                record = record_attempt("furnishing_failure", None)
                record["repairs"].append(
                    {
                        "code": "furnishing_failed",
                        "detail": f"{exc}; {exc2}",
                        "result": "no valid furniture layout could be produced",
                    }
                )
                return self._result(
                    ok=False,
                    plan=plan,
                    model=model,
                    furnishing={},
                    geometry=None,
                    validation=None,
                    attempts=attempts,
                    repair_log=self._log(
                        job_id,
                        attempts,
                        log_entries,
                        normalization_record,
                        started,
                        status="failed",
                        error_code="furnishing_failed",
                    ),
                    error_code="furnishing_failed",
                    message=f"Furniture placement failed: {exc2}",
                    progress=emit,
                )

        emit("repair_build", {"attempt": 0})
        geometry, build_error = await self._build(
            build, plan, furnishing, style, abort
        )
        if geometry is None:
            record = record_attempt("build_failure", None)
            record["errors"] = [build_error or "scene build failed"]
            conservative = self._conservative_furnishing(plan, furnishing)
            if conservative is not None and budget_left():
                furnishing = conservative
                log_entries.append(
                    {
                        "code": "conservative_rebuild",
                        "detail": (
                            "The scene could not be built with the planned furniture; the "
                            "conservative layout was rebuilt and revalidated."
                        ),
                        "result": "applied",
                        "stage": "build",
                        "source": "furniture",
                    }
                )
                geometry, build_error = await self._build(
                    build, plan, furnishing, style, abort
                )
        if geometry is None:
            return self._result(
                ok=False,
                plan=plan,
                model=model,
                furnishing=furnishing,
                geometry=None,
                validation=None,
                attempts=attempts,
                repair_log=self._log(
                    job_id,
                    attempts,
                    log_entries,
                    normalization_record,
                    started,
                    status="failed",
                    error_code="geometry_build_failed",
                ),
                error_code="geometry_build_failed",
                message=build_error or "Scene generation failed",
                progress=emit,
            )

        plan = geometry.get("floor_plan") or plan
        model = geometry.get("house_model") or model

        validation = await validate(geometry, plan, furnishing)
        record = record_attempt("initial_validation", validation)
        emit(
            "repair_validate",
            {
                "attempt": 0,
                "geometry_valid": validation.get("geometry_valid"),
            },
        )

        attempt_count = 0
        replan_count = 0
        conservative_done = False
        while (
            not validation.get("geometry_valid")
            and attempt_count < self.max_attempts
            and budget_left()
        ):
            diagnosis = self._diagnose(validation, plan, furnishing)
            record["diagnosis"] = diagnosis
            if not diagnosis["actions"]:
                note("No targeted repair is available for the remaining validation errors")
                break
            changes: List[Dict] = []
            rebuild = False
            emit(
                "repair_attempt",
                {"attempt": attempt_count + 1, "actions": diagnosis["actions"]},
            )

            if "structure" in diagnosis["actions"]:
                renorm = normalize_house(plan, room_metadata=room_metadata)
                if renorm.get("repairs"):
                    plan = renorm["plan"]
                    model = renorm["model"]
                    for repair in renorm["repairs"]:
                        changes.append(
                            {
                                **repair,
                                "stage": "repair_loop",
                                "source": "structure",
                                "result": "applied; geometry will be rebuilt",
                            }
                        )
                    rebuild = True
                else:
                    model = renorm["model"]

            if "spawn" in diagnosis["actions"]:
                changed, repair = self._repair_spawn(
                    plan, furnishing, geometry
                )
                if changed:
                    changes.append(repair)

            if "colliders" in diagnosis["actions"]:
                changed, repairs = self._repair_colliders(
                    plan, furnishing, geometry
                )
                if changed:
                    changes.extend(repairs)

            if "collision_artifacts" in diagnosis["actions"]:
                changed, repairs = self._refresh_collision(
                    plan, furnishing, geometry
                )
                if changed:
                    changes.extend(repairs)

            if "graph" in diagnosis["actions"]:
                changed, repair = self._refresh_graph(
                    plan, furnishing, geometry, room_metadata, job_id
                )
                if changed:
                    changes.append(repair)

            if "furniture" in diagnosis["actions"]:
                moved, removed, repairs = self._repair_furniture(
                    plan, furnishing, diagnosis
                )
                changes.extend(repairs)
                if moved or removed:
                    rebuild = True

            already_rearranged = any(
                change.get("code") in {"furniture_moved", "furniture_removed"}
                for change in changes
            )
            if (
                "replan" in diagnosis["actions"]
                and replan_count < MAX_VARIANT_RETRIES
                and not already_rearranged
            ):
                replan_count += 1
                next_variant = (current_variant + replan_count) % (
                    MAX_VARIANT_RETRIES + 1
                )
                try:
                    candidate = await self._call_furnish(
                        furnish, plan, room_metadata, style, next_variant, True, abort
                    )
                    furnishing = candidate
                    current_variant = next_variant
                    changes.append(
                        {
                            "code": "furniture_replanned",
                            "detail": (
                                "Furniture was re-planned with layout variant "
                                f"{next_variant} so required pieces fit while keeping routes open."
                            ),
                            "result": "applied; geometry will be rebuilt",
                            "stage": "repair_loop",
                            "source": "furniture",
                        }
                    )
                    rebuild = True
                except Exception as exc:  # noqa: BLE001
                    if abort is not None and abort():
                        raise
                    changes.append(
                        {
                            "code": "furniture_replan_rejected",
                            "detail": str(exc),
                            "result": "skipped; kept the current layout",
                            "stage": "repair_loop",
                            "source": "furniture",
                        }
                    )

            if not changes:
                note("Diagnosed issues have no safe repair that changes the scene")
                break

            log_entries.extend(changes)
            record["repairs"] = changes
            attempt_count += 1
            emit(
                "repair_regenerate",
                {
                    "attempt": attempt_count,
                    "rebuild": rebuild,
                },
            )
            if not rebuild:
                # Artifacts were rewritten in place; the validator re-reads them.
                await self._check_abort(abort)
                validation = await validate(geometry, plan, furnishing)
            else:
                geometry, build_error = await self._build(
                    build, plan, furnishing, style, abort
                )
                if geometry is None:
                    record["errors"].append(build_error or "rebuild failed")
                    break
                plan = geometry.get("floor_plan") or plan
                model = geometry.get("house_model") or model
                validation = await validate(geometry, plan, furnishing)
            record = record_attempt(f"repair_attempt_{attempt_count}", validation)
            emit(
                "repair_validate",
                {
                    "attempt": attempt_count,
                    "geometry_valid": validation.get("geometry_valid"),
                },
            )
            note(
                "Repair attempt "
                f"{attempt_count}: {len(changes)} change(s); scene valid="
                f"{validation.get('geometry_valid')}"
            )

        if not validation.get("geometry_valid") and budget_left():
            conservative = self._conservative_furnishing(plan, furnishing)
            if conservative is not None:
                furnishing = conservative
                conservative_done = True
                log_entries.append(
                    {
                        "code": "conservative_fallback",
                        "detail": (
                            "Repairs did not converge; all non-essential furniture was removed "
                            "while keeping one piece in each required room, then the scene was "
                            "rebuilt and fully revalidated."
                        ),
                        "result": "applied; geometry rebuilt for validation",
                        "stage": "conservative_fallback",
                        "source": "furniture",
                    }
                )
                emit("repair_conservative", {})
                geometry, build_error = await self._build(
                    build, plan, furnishing, style, abort
                )
                if geometry is not None:
                    plan = geometry.get("floor_plan") or plan
                    model = geometry.get("house_model") or model
                    validation = await validate(geometry, plan, furnishing)
                    record = record_attempt("conservative_fallback", validation)
                else:
                    record["errors"].append(build_error or "conservative rebuild failed")

        valid = bool(validation.get("geometry_valid"))
        if not valid:
            unresolved = [
                issue.get("message") or issue.get("code")
                for issue in (
                    (validation.get("stats") or {}).get("issues") or []
                )
            ] or list(validation.get("errors") or [])
            return self._result(
                ok=False,
                plan=plan,
                model=model,
                furnishing=furnishing,
                geometry=geometry,
                validation=validation,
                attempts=attempts,
                repair_log=self._log(
                    job_id,
                    attempts,
                    log_entries,
                    normalization_record,
                    started,
                    status="failed",
                    error_code="scene_validation_failed",
                    unresolved=unresolved,
                    conservative=conservative_done,
                ),
                error_code="scene_validation_failed",
                message="Scene validation failed after automatic repair: "
                + "; ".join(unresolved[:5]),
                progress=emit,
            )

        return self._result(
            ok=True,
            plan=plan,
            model=model,
            furnishing=furnishing,
            geometry=geometry,
            validation=validation,
            attempts=attempts,
            repair_log=self._log(
                job_id,
                attempts,
                log_entries,
                normalization_record,
                started,
                status="passed" if attempt_count == 0 else "repaired",
                conservative=conservative_done,
            ),
            error_code=None,
            message=None,
            progress=emit,
        )

    # ------------------------------------------------------------------
    # Diagnosis
    # ------------------------------------------------------------------
    def _diagnose(
        self, validation: Dict, plan: Dict, furnishing: Dict
    ) -> Dict:
        stats = validation.get("stats") or {}
        checks = validation.get("checks") or {}
        issues = list(stats.get("issues") or [])
        errors = list(validation.get("errors") or [])
        actions: List[str] = []
        rooms: set = set()
        item_ids: set = set()
        door_ids: set = set()
        messages: List[str] = []

        def add(action: str, message: str):
            if action not in actions:
                actions.append(action)
            messages.append(message)

        codes = {issue.get("code") for issue in issues}
        if (
            "spawn_invalid" in codes
            or "spawn_missing" in codes
            or checks.get("spawn_clearance") is False
            or checks.get("spawn_walkable") is False
        ):
            add("spawn", "The spawn point is not walkable.")
        if "furniture_collision_mismatch" in codes:
            add("collision_artifacts", "Collision furniture volumes are stale.")
        if "furniture_collider_inflated" in codes or "furniture_collider_missing" in codes:
            add("colliders", "Furniture collision volumes do not match their footprints.")
        if "collision_doors_stale" in codes:
            add("collision_artifacts", "Doorway collision openings are stale.")
        if "navigation_graph_stale" in codes:
            add("graph", "The room graph is stale relative to the plan.")
        if "doorway_blocked_by_wall" in codes:
            add("structure", "Wall geometry covers a doorway opening.")
        if "furniture_in_doorway" in codes:
            add("furniture", "Furniture occupies a doorway clearance zone.")
        route_issues = [
            issue
            for issue in issues
            if issue.get("code")
            in {
                "room_unreachable",
                "room_navigation_target_unreachable",
                "doorway_side_unreachable",
                "doorway_side_no_space",
            }
        ]
        if route_issues:
            if any(issue.get("cause") != "structure" for issue in route_issues):
                add("furniture", "A room or doorway side has no walking route from the spawn.")
            if any(issue.get("cause") == "structure" for issue in route_issues):
                add(
                    "structure",
                    "The wall and doorway geometry leaves no walkable corridor to a room.",
                )
        if checks.get("furniture_items_present") is False:
            add("replan", "A required room has no furniture.")
        if checks.get("furniture_placement_valid") is False:
            add("furniture", "Furniture placement violates the floor plan constraints.")

        for issue in issues:
            room_id = issue.get("room_id")
            if room_id is not None:
                try:
                    rooms.add(int(room_id))
                except (TypeError, ValueError):
                    pass
            if issue.get("item_id"):
                item_ids.add(str(issue["item_id"]))
            for item_id in issue.get("item_ids") or []:
                if item_id:
                    item_ids.add(str(item_id))
            if issue.get("door_id") is not None:
                door_ids.add(int(issue["door_id"]))

        if not issues:
            for error in errors:
                lower = error.lower()
                if "spawn" in lower:
                    add("spawn", error)
                elif "collider" in lower or "footprint" in lower:
                    add("colliders", error)
                elif "doorway" in lower and "collision" in lower:
                    add("collision_artifacts", error)
                elif "room_graph" in lower or "graph" in lower:
                    add("graph", error)
                elif "furniture" in lower or "room" in lower and "walkable" in lower:
                    add("furniture", error)
                elif "wall" in lower and "doorway" in lower:
                    add("structure", error)
                elif "received no furniture" in lower:
                    add("replan", error)

        # Only the rooms named by an issue are targeted; the validator names
        # the actual obstructing items, so a failure never removes furniture
        # from unrelated rooms.
        return {
            "actions": actions,
            "codes": sorted(code for code in codes if code),
            "rooms": sorted(rooms),
            "item_ids": sorted(item_ids),
            "doorway_item_ids": sorted(
                str(issue.get("item_id"))
                for issue in issues
                if issue.get("code") == "furniture_in_doorway" and issue.get("item_id")
            ),
            "door_ids": sorted(door_ids),
            "messages": messages[:10],
        }

    # ------------------------------------------------------------------
    # Repairs
    # ------------------------------------------------------------------
    def _repair_spawn(
        self, plan: Dict, furnishing: Dict, geometry: Dict
    ) -> (bool, Dict):
        output_dir = Path(geometry.get("output_dir") or "")
        collision = self._load_json(output_dir / "collision.json")
        graph = self._load_json(output_dir / "room_graph.json")
        rooms = plan.get("rooms") or []
        doorways = plan.get("doorways") or []
        if not rooms or not collision:
            return False, {}
        walls = collision.get("walls") or []
        furniture = collision.get("furniture") or []
        rooms_by_id = {int(room["id"]): room for room in rooms}
        requested = resolve_spawn(rooms, doorways)
        candidates: List[Dict] = []
        if requested and requested.get("room_id") in rooms_by_id:
            candidates.append(rooms_by_id[int(requested["room_id"])])
        for room in sorted(rooms, key=lambda item: -float(item.get("area_m2") or 0.0)):
            if room not in candidates:
                candidates.append(room)
        chosen = None
        point = None
        for room in candidates:
            point = clear_spawn_in_room(room, walls, furniture)
            if point is not None:
                chosen = room
                break
        if chosen is None or point is None:
            return False, {}
        previous = (collision.get("spawn") or {}).get("position")
        room_doors = [
            door
            for door in doorways
            if int(chosen["id"])
            in [int(value) for value in (door.get("rooms") or []) if value is not None]
        ]
        yaw = 0.0
        if room_doors:
            center = room_doors[0].get("center_m") or [point[0], point[1]]
            yaw = float(
                math.atan2(-(center[0] - point[0]), -(center[1] - point[1]))
            )
        collision["spawn"] = {
            "position": [round(point[0], 3), 0.0, round(point[1], 3)],
            "yaw": round(yaw, 4),
            "room_id": int(chosen["id"]),
        }
        self._write_json(output_dir / "collision.json", collision)
        if graph:
            graph["spawn_room"] = int(chosen["id"])
            self._refresh_reachability(graph)
            self._write_json(output_dir / "room_graph.json", graph)
        chosen["spawn_m"] = [round(point[0], 3), round(point[1], 3)]
        repair = {
            "code": "spawn_relocated",
            "detail": (
                "The spawn point was moved inside a walkable part of room "
                f"{chosen['id']}, clear of wall and furniture colliders."
            ),
            "before": previous,
            "after": [round(point[0], 3), 0.0, round(point[1], 3)],
            "object_ids": [int(chosen["id"])],
            "result": "applied; collision spawn and room graph updated",
            "stage": "repair_loop",
            "source": "spawn",
        }
        return True, repair

    def _repair_colliders(
        self, plan: Dict, furnishing: Dict, geometry: Dict
    ) -> (bool, List[Dict]):
        repairs = []
        for item in furnishing.get("items") or []:
            if not item.get("collision"):
                continue
            footprint = item.get("footprint")
            bounds = item.get("collision_bounds")
            if not footprint:
                continue
            if not bounds or any(
                abs(float(a) - float(b)) > 0.005
                for a, b in zip(
                    list(bounds.get("min") or []) + list(bounds.get("max") or []),
                    list(footprint.get("min") or []) + list(footprint.get("max") or []),
                )
            ):
                item["collision_bounds"] = copy.deepcopy(footprint)
                repairs.append(
                    {
                        "code": "furniture_collider_restored",
                        "detail": (
                            f"Collider for {item['id']} was reset to the item's actual "
                            "footprint."
                        ),
                        "object_ids": [item["id"]],
                        "result": "applied",
                        "stage": "repair_loop",
                        "source": "collision",
                    }
                )
        if not repairs:
            return False, []
        self._write_furnishing(geometry, furnishing)
        self._refresh_collision(plan, furnishing, geometry)
        return True, repairs

    def _refresh_collision(
        self, plan: Dict, furnishing: Dict, geometry: Dict
    ) -> (bool, List[Dict]):
        output_dir = Path(geometry.get("output_dir") or "")
        path = output_dir / "collision.json"
        collision = self._load_json(path)
        if not collision:
            return False, []
        expected = self._collision_doors(plan)
        collision["doors"] = expected
        collision["furniture"] = furniture_collision_items(furnishing)
        spawn = collision.get("spawn") or {}
        if not spawn.get("position"):
            resolved = resolve_spawn(plan.get("rooms") or [], plan.get("doorways") or [])
            if resolved:
                collision["spawn"] = {
                    "position": [resolved["position"][0], 0.0, resolved["position"][1]],
                    "yaw": 0.0,
                    "room_id": resolved["room_id"],
                }
        self._write_json(path, collision)
        self._write_furnishing(geometry, furnishing)
        return True, [
            {
                "code": "collision_artifacts_refreshed",
                "detail": (
                    "Doorway openings and furniture collision volumes were rebuilt from "
                    "the normalized plan and current placement records."
                ),
                "result": "applied",
                "stage": "repair_loop",
                "source": "collision",
            }
        ]

    def _refresh_graph(
        self,
        plan: Dict,
        furnishing: Dict,
        geometry: Dict,
        room_metadata: Dict[int, Dict],
        job_id: str,
    ) -> (bool, Dict):
        output_dir = Path(geometry.get("output_dir") or "")
        collision = self._load_json(output_dir / "collision.json") or {}
        existing = self._load_json(output_dir / "room_graph.json") or {}
        graph = self._rebuild_graph(
            plan, furnishing, collision, room_metadata, job_id, existing
        )
        self._write_json(output_dir / "room_graph.json", graph)
        return True, {
            "code": "navigation_graph_rebuilt",
            "detail": (
                "The room graph was rebuilt from the normalized rooms, doorways and "
                "spawn point."
            ),
            "result": "applied",
            "stage": "repair_loop",
            "source": "navigation",
        }

    def _repair_furniture(
        self, plan: Dict, furnishing: Dict, diagnosis: Dict
    ) -> (int, int, List[Dict]):
        rooms = plan.get("rooms") or []
        doorways = plan.get("doorways") or []
        windows = plan.get("windows") or []
        rooms_by_id = {int(room["id"]): room for room in rooms}
        items = list(furnishing.get("items") or [])
        flagged_items = set(diagnosis.get("item_ids") or [])
        doorway_items = set(diagnosis.get("doorway_item_ids") or [])
        repairs: List[Dict] = []
        moved = 0
        removed = 0

        for item in list(items):
            item_id = str(item.get("id"))
            if item_id not in flagged_items:
                continue
            children = [
                child
                for child in items
                if child.get("parent_item_id") == item.get("id")
            ]
            if not children and item_id not in self._relocated:
                relocated = self._relocate_item(
                    plan, furnishing, item, rooms_by_id, doorways, windows
                )
                if relocated:
                    self._relocated.add(item_id)
                    moved += 1
                    repairs.append(
                        {
                            "code": "furniture_moved",
                            "detail": (
                                f"{item.get('label')} was moved to a free wall position "
                                "clear of the doorway and walking route."
                            ),
                            "object_ids": [item.get("id")],
                            "result": "applied; geometry will be rebuilt",
                            "stage": "repair_loop",
                            "source": "furniture",
                        }
                    )
                    continue
            if is_essential_item(item) and item_id not in doorway_items:
                continue
            victims = _remove_family(items, item)
            if victims:
                for victim in victims:
                    self._relocated.discard(str(victim.get("id")))
                removed += len(victims)
                repairs.append(
                    {
                        "code": "furniture_removed",
                        "detail": (
                            f"{item.get('label')} was removed because it blocked a doorway "
                            "or the walking route."
                        ),
                        "object_ids": [victim.get("id") for victim in victims],
                        "result": "applied; geometry will be rebuilt",
                        "stage": "repair_loop",
                        "source": "furniture",
                    }
                )

        if moved or removed:
            furnishing["items"] = items
            refresh_furnishing(furnishing, rooms)
        return moved, removed, repairs

    def _relocate_item(
        self,
        plan: Dict,
        furnishing: Dict,
        item: Dict,
        rooms_by_id: Dict[int, Dict],
        doorways: Sequence[Dict],
        windows: Sequence[Dict],
    ) -> bool:
        room = rooms_by_id.get(int(item.get("room_id", -1)))
        if room is None:
            return False
        spawn_room = select_spawn_room(plan.get("rooms") or [], doorways)
        layout = RoomLayout(
            room,
            item.get("room_type") or "unknown",
            item.get("room_name") or f"Room {room['id']}",
            doorways,
            0,
            int(spawn_room["id"]) if spawn_room else None,
            furnishing.get("style") or "modern",
            windows,
        )
        layout.items = [
            copy.deepcopy(other)
            for other in furnishing.get("items") or []
            if int(other.get("room_id", -1)) == int(room["id"])
            and other.get("id") != item.get("id")
            and other.get("parent_item_id") != item.get("id")
        ]
        candidate, _ = layout.place_on_wall(
            item["asset_id"], label=item.get("label"), gap=0.06
        )
        if candidate is None:
            candidate, _ = layout.place_center(
                item["asset_id"], label=item.get("label")
            )
        if candidate is None:
            return False
        for key in (
            "position",
            "rotation_deg",
            "rotation",
            "size_m",
            "height_m",
            "footprint",
            "bbox",
            "collision_bounds",
            "placement",
            "wall",
        ):
            item[key] = candidate.get(key)
        return True

    def _conservative_furnishing(
        self, plan: Dict, furnishing: Dict
    ) -> Optional[Dict]:
        items = list(furnishing.get("items") or [])
        if not items:
            return None
        rooms = plan.get("rooms") or []
        entries = {
            int(entry.get("room_id", -1)): entry
            for entry in furnishing.get("rooms") or []
        }
        kept: List[Dict] = []
        changed = False
        for room in rooms:
            room_id = int(room["id"])
            room_items = [
                item for item in items if int(item.get("room_id", -1)) == room_id
            ]
            if not room_items:
                continue
            essentials = [
                item
                for item in room_items
                if is_essential_item(item) and item.get("collision")
            ]
            if essentials:
                kept.extend(essentials)
                changed = changed or len(essentials) != len(room_items)
                continue
            required = str(
                (entries.get(room_id) or {}).get("room_type") or "unknown"
            ) in REQUIRED_ROOM_TYPES
            if required:
                collision_items = [
                    item for item in room_items if item.get("collision")
                ]
                pool = collision_items or room_items
                kept.append(
                    min(
                        pool,
                        key=lambda item: _footprint_area(item),
                    )
                )
                changed = changed or len(kept) != len(items)
            else:
                changed = True
        kept_ids = {str(item.get("id")) for item in kept}
        kept = [
            item
            for item in kept
            if not item.get("parent_item_id")
            or str(item.get("parent_item_id")) in kept_ids
        ]
        if not changed and len(kept) == len(items):
            return None
        conservative = copy.deepcopy(furnishing)
        conservative["items"] = kept
        refresh_furnishing(conservative, rooms)
        conservative.setdefault("warnings", []).append(
            "Conservative repair layout: non-essential furniture was removed to keep "
            "every doorway and walking route clear."
        )
        return conservative

    # ------------------------------------------------------------------
    # Graph and artifact helpers
    # ------------------------------------------------------------------
    def _rebuild_graph(
        self,
        plan: Dict,
        furnishing: Dict,
        collision: Dict,
        room_metadata: Dict[int, Dict],
        job_id: str,
        existing: Dict,
    ) -> Dict:
        rooms = plan.get("rooms") or []
        doorways = plan.get("doorways") or []
        existing_nodes = {
            int(node.get("id")): node for node in existing.get("nodes") or []
        }
        furnishing_rooms = {
            int(entry.get("room_id", -1)): entry
            for entry in furnishing.get("rooms") or []
        }
        nodes = []
        adjacency: Dict[int, set] = {}
        for room in rooms:
            room_id = int(room["id"])
            metadata = room_metadata.get(room_id) or {}
            previous = existing_nodes.get(room_id) or {}
            entry = furnishing_rooms.get(room_id) or {}
            furniture = entry.get("furniture") or []
            nodes.append(
                {
                    "id": room_id,
                    "name": metadata.get("name")
                    or previous.get("name")
                    or f"Room {room_id + 1}",
                    "room_type": metadata.get("room_type")
                    or previous.get("room_type"),
                    "center": room.get("center_m"),
                    "bounds": room.get("bounds_m"),
                    "area_m2": room.get("area_m2"),
                    "polygon": room.get("polygon_m") or [],
                    "furniture": furniture,
                    "furniture_count": len(furniture),
                    "furnishing_status": entry.get("status"),
                    "photo_ids": previous.get("photo_ids") or [],
                    "photo_count": len(previous.get("photo_ids") or []),
                    "photos": previous.get("photos") or [],
                }
            )
            adjacency[room_id] = set()
        edges = []
        for door in doorways:
            pair = [
                None if value is None else int(value)
                for value in (door.get("rooms") or [None, None])[:2]
            ]
            if len(pair) < 2 or pair[0] is None or pair[1] is None or pair[0] == pair[1]:
                continue
            edges.append(
                {
                    "door_id": door.get("id"),
                    "from": pair[0],
                    "to": pair[1],
                    "position": door.get("center_m"),
                    "width_m": round(float(door.get("width_m") or 0.9), 2),
                }
            )
            adjacency.setdefault(pair[0], set()).add(pair[1])
            adjacency.setdefault(pair[1], set()).add(pair[0])
        spawn_room = (collision.get("spawn") or {}).get("room_id")
        if spawn_room is None:
            chosen = select_spawn_room(rooms, doorways)
            spawn_room = int(chosen["id"]) if chosen else None
        spawn_room = int(spawn_room) if spawn_room is not None else None
        reachable = {spawn_room} if spawn_room is not None else set()
        frontier = [spawn_room] if spawn_room is not None else []
        while frontier:
            current = frontier.pop()
            for neighbour in adjacency.get(current, ()):
                if neighbour not in reachable:
                    reachable.add(neighbour)
                    frontier.append(neighbour)
        connected = bool(rooms) and len(reachable) == len(rooms)
        return {
            "nodes": nodes,
            "edges": edges,
            "spawn_room": spawn_room,
            "reachable_rooms": sorted(value for value in reachable if value is not None),
            "connected": connected,
            "navigation_available": connected and len(rooms) > 0,
        }

    @staticmethod
    def _refresh_reachability(graph: Dict) -> None:
        adjacency: Dict[int, set] = {}
        for edge in graph.get("edges") or []:
            try:
                first, second = int(edge["from"]), int(edge["to"])
            except (KeyError, TypeError, ValueError):
                continue
            adjacency.setdefault(first, set()).add(second)
            adjacency.setdefault(second, set()).add(first)
        start = graph.get("spawn_room")
        if start is None:
            graph["reachable_rooms"] = []
            graph["connected"] = False
            graph["navigation_available"] = False
            return
        start = int(start)
        reachable = {start}
        frontier = [start]
        while frontier:
            current = frontier.pop()
            for neighbour in adjacency.get(current, ()):
                if neighbour not in reachable:
                    reachable.add(neighbour)
                    frontier.append(neighbour)
        nodes = {int(node["id"]) for node in graph.get("nodes") or []}
        graph["reachable_rooms"] = sorted(reachable)
        graph["connected"] = bool(nodes) and reachable == nodes
        graph["navigation_available"] = graph["connected"] and bool(nodes)

    @staticmethod
    def _collision_doors(plan: Dict) -> List[Dict]:
        result: List[Dict] = []
        for orientation in ("horizontal", "vertical"):
            for door in plan.get("doorways") or []:
                if door.get("orientation") != orientation:
                    continue
                detected = float(
                    door.get("detected_width_m") or door.get("width_m") or 0.9
                )
                width = float(door.get("effective_width_m") or door.get("width_m") or 0.9)
                result.append(
                    {
                        "id": door.get("id"),
                        "position": door.get("center_m"),
                        "orientation": orientation,
                        "width_m": round(min(1.8, max(0.5, width)), 2),
                        "detected_width_m": round(detected, 2),
                        "rooms": door.get("rooms", [None, None]),
                        "exterior": bool(door.get("exterior", False)),
                    }
                )
        return result

    @staticmethod
    def _write_json(path: Path, payload: Dict) -> None:
        if not str(path):
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2))

    @staticmethod
    def _load_json(path: Path) -> Dict:
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_furnishing(self, geometry: Dict, furnishing: Dict) -> None:
        output_dir = Path(geometry.get("output_dir") or "")
        if not str(output_dir):
            return
        self._write_json(output_dir / "furniture.json", furnishing)

    @staticmethod
    async def _check_abort(abort: Optional[Callable[[], bool]]) -> None:
        if abort is None:
            return
        try:
            cancelled = bool(abort())
        except Exception:  # noqa: BLE001 - an abort probe must not break the loop
            cancelled = False
        if cancelled:
            raise RuntimeError("Repair loop aborted by the pipeline")

    @staticmethod
    async def _call_furnish(
        furnish: FurnishCallback,
        plan: Dict,
        room_metadata: Dict[int, Dict],
        style: str,
        variant: int,
        allow_partial: bool,
        abort: Optional[Callable[[], bool]] = None,
    ) -> Dict:
        result = furnish(plan, room_metadata, style, variant, allow_partial)
        if inspect.isawaitable(result):
            result = await result
        await SceneRepairEngine._check_abort(abort)
        return result

    async def _build(
        self,
        build: BuildCallback,
        plan: Dict,
        furnishing: Dict,
        style: str,
        abort: Optional[Callable[[], bool]] = None,
    ) -> (Optional[Dict], Optional[str]):
        await self._check_abort(abort)
        try:
            return await build(plan, furnishing, style), None
        except Exception as exc:  # noqa: BLE001 - build rejection is diagnosed
            if abort is not None and abort():
                raise
            return None, str(exc)

    # ------------------------------------------------------------------
    # Result and log
    # ------------------------------------------------------------------
    def _log(
        self,
        job_id: str,
        attempts: List[Dict],
        repairs: List[Dict],
        normalization: Dict,
        started: float,
        *,
        status: str,
        error_code: Optional[str] = None,
        unresolved: Optional[List[str]] = None,
        conservative: bool = False,
    ) -> Dict:
        final = attempts[-1] if attempts else {}
        return {
            "schema_version": "1.0",
            "job_id": job_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "error_code": error_code,
            "max_attempts": self.max_attempts,
            "time_budget_s": self.time_budget_s,
            "elapsed_s": round(time.monotonic() - started, 3),
            "conservative_fallback": conservative,
            "normalization": normalization,
            "attempts": attempts,
            "repairs": repairs,
            "unresolved": unresolved or [],
            "final": {
                "geometry_valid": bool(final.get("geometry_valid")),
                "navigation_available": bool(final.get("navigation_available")),
                "checks": final.get("checks") or {},
                "errors": final.get("errors") or [],
                "issues": final.get("issues") or [],
            },
        }

    @staticmethod
    def _result(
        *,
        ok: bool,
        plan: Dict,
        model: Dict,
        furnishing: Dict,
        geometry: Optional[Dict],
        validation: Optional[Dict],
        attempts: List[Dict],
        repair_log: Dict,
        error_code: Optional[str],
        message: Optional[str],
        progress: ProgressCallback,
    ) -> Dict:
        try:
            progress(
                "repair_finished",
                {"ok": ok, "error_code": error_code},
            )
        except Exception:  # noqa: BLE001
            pass
        return {
            "ok": ok,
            "plan": plan,
            "house_model": model,
            "furnishing": furnishing,
            "geometry": geometry,
            "validation": validation,
            "attempts": attempts,
            "repair_log": repair_log,
            "error_code": error_code,
            "message": message,
        }


def _remove_family(items: List[Dict], target: Dict) -> List[Dict]:
    if target not in items:
        return []
    removed = [target]
    items.remove(target)
    pending = [target.get("id")]
    while pending:
        parent = pending.pop()
        for child in list(items):
            if child.get("parent_item_id") == parent:
                items.remove(child)
                removed.append(child)
                pending.append(child.get("id"))
    return removed


def _footprint_area(item: Dict) -> float:
    footprint = item.get("footprint") or {}
    minimum = footprint.get("min") or [0.0, 0.0]
    maximum = footprint.get("max") or [0.0, 0.0]
    return max(0.0, float(maximum[0]) - float(minimum[0])) * max(
        0.0, float(maximum[1]) - float(minimum[1])
    )
