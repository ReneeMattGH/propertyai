"""Scene validation.

This module is the gate between "pipeline finished" and "job completed".  It
re-opens the exported GLB and independently verifies the architecture, the
furniture inside it, and that the player can walk from the spawn point to
every room that the floor plan connects.  A job is only marked complete when
these checks pass.

Floor-plan-only jobs do not have photographs.  The legacy photo checks are
retained for jobs created by the older photo-assisted workflow, but they are
not part of the completion gate for a floor-plan-only scene.
"""
from __future__ import annotations

import asyncio
import json
import math
import struct
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import trimesh
from PIL import Image
from shapely import contains_xy
from shapely.geometry import Point as ShapelyPoint
from shapely.geometry import Polygon as ShapelyPolygon
from shapely.geometry import box as shapely_box
from shapely.ops import unary_union

from furnishing import PLAYER_RADIUS, validate_furnishing

MIN_FACE_AREA = 1e-7
MAX_DEGENERATE_RATIO = 0.02
NAV_GRID_M = 0.1
APPROACH_M = 0.45
REGION_MARGIN_M = 0.16

REQUIRED_FURNISHED_TYPES = {
    "living_room",
    "bedroom",
    "kitchen",
    "bathroom",
    "dining_room",
    "entrance",
}
FURNISHABLE_TYPES = REQUIRED_FURNISHED_TYPES | {"balcony"}


class SceneValidationError(Exception):
    pass


class SceneValidator:
    """Validate the exported scene on disk."""

    async def validate_scene(
        self,
        job_id: str,
        geometry_result: Dict,
        photo_manifest: Dict = None,
        floor_plan_result: Dict = None,
        furnishing: Dict = None,
    ) -> Dict:
        return await asyncio.to_thread(
            self._validate_scene_sync,
            job_id,
            geometry_result,
            photo_manifest,
            floor_plan_result,
            furnishing,
        )

    def _validate_scene_sync(
        self,
        job_id: str,
        geometry_result: Dict,
        photo_manifest: Dict = None,
        floor_plan_result: Dict = None,
        furnishing: Dict = None,
    ) -> Dict:
        output_dir = Path(geometry_result.get("output_dir", ""))
        glb_path = output_dir / "scene.glb"
        collision_path = output_dir / "collision.json"
        graph_path = output_dir / "room_graph.json"

        checks: Dict[str, bool] = {}
        errors: List[str] = []
        warnings: List[str] = []
        stats: Dict = {}

        checks["glb_exists"] = glb_path.exists()
        if not checks["glb_exists"]:
            errors.append("Exported scene.glb is missing")
            return self._finish(job_id, output_dir, checks, errors, warnings, stats, None, None)

        checks["glb_size_plausible"] = glb_path.stat().st_size > 1024
        if not checks["glb_size_plausible"]:
            errors.append("Exported scene.glb is smaller than 1 KB (truncated or empty)")

        scene = None
        glb_json: Dict = {}
        if checks["glb_size_plausible"]:
            try:
                scene = trimesh.load(str(glb_path), force="scene")
                checks["glb_loadable"] = True
            except Exception as exc:
                checks["glb_loadable"] = False
                errors.append(f"scene.glb could not be loaded: {exc}")
            try:
                glb_json = self._read_glb_json(glb_path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"scene.glb structure could not be inspected: {exc}")

        if scene is not None:
            meshes = list(scene.geometry.values())
            checks["has_meshes"] = len(meshes) > 0
            total_faces = sum(len(mesh.faces) for mesh in meshes)
            total_vertices = sum(len(mesh.vertices) for mesh in meshes)
            stats["mesh_count"] = len(meshes)
            stats["faces"] = int(total_faces)
            stats["vertices"] = int(total_vertices)
            checks["has_faces"] = total_faces > 0
            if not checks["has_faces"]:
                errors.append("Exported scene contains no faces")
            bounds = scene.bounds
            checks["finite_bounds"] = bool(bounds is not None and np.isfinite(bounds).all())
            if checks["finite_bounds"]:
                span = bounds[1] - bounds[0]
                stats["bounds"] = [[round(float(v), 3) for v in row] for row in bounds]
                stats["span"] = [round(float(v), 3) for v in span]
                checks["plausible_size"] = bool(span[0] > 1.0 and span[2] > 1.0 and span[1] > 2.0)
                if not checks["plausible_size"]:
                    errors.append(
                        f"Scene dimensions are implausible (span {span.round(2).tolist()})"
                    )
            degenerate = 0
            total_area = 0.0
            for mesh in meshes:
                if len(mesh.faces) == 0:
                    continue
                areas = mesh.area_faces
                degenerate += int(np.count_nonzero(areas < MIN_FACE_AREA))
                total_area += float(mesh.area)
            stats["degenerate_faces"] = degenerate
            stats["surface_area_m2"] = round(total_area, 2)
            ratio = degenerate / max(1, total_faces)
            checks["no_degenerate_faces"] = ratio <= MAX_DEGENERATE_RATIO
            if not checks["no_degenerate_faces"]:
                errors.append(
                    f"{degenerate} degenerate faces detected ({ratio:.1%} of geometry)"
                )

        metadata = self._load_json(output_dir / "metadata.json", errors, "metadata.json")
        if metadata:
            checks["metadata_file"] = True
            checks["asset_job_match"] = metadata.get("job_id") == job_id
            if not checks["asset_job_match"]:
                errors.append("metadata.json does not belong to this job")
        else:
            checks["metadata_file"] = False
            checks["asset_job_match"] = False

        collision = self._load_json(collision_path, errors, "collision.json")
        if collision:
            checks["collision_file"] = True
            walls = collision.get("walls", [])
            checks["collision_walls"] = len(walls) >= 4
            stats["collision_walls"] = len(walls)
            if len(walls) < 4:
                errors.append(
                    "Collision geometry has fewer than four wall volumes; indoor navigation "
                    "would be impossible"
                )
            spawn = collision.get("spawn", {}).get("position")
            checks["spawn_present"] = bool(spawn and len(spawn) == 3)
            if checks["spawn_present"]:
                x, _, z = spawn
                checks["spawn_walkable"] = not self._point_hits_boxes(
                    x, z, walls, PLAYER_RADIUS
                )
                stats["spawn"] = spawn
                if not checks["spawn_walkable"]:
                    errors.append("Spawn point intersects a collision wall")
            doors = collision.get("doors", [])
            stats["doors"] = len(doors)
            passable = [d for d in doors if float(d.get("width_m", 0)) >= 0.5]
            checks["passable_doorways"] = len(passable) > 0 or len(doors) == 0
            if doors and not passable:
                errors.append("Every detected doorway is too narrow to walk through")
            for wall in walls:
                minimum, maximum = wall.get("min"), wall.get("max")
                if (
                    not minimum
                    or not maximum
                    or not all(np.isfinite(minimum + maximum))
                    or minimum[0] >= maximum[0]
                    or minimum[1] >= maximum[1]
                ):
                    checks["collision_walls_valid"] = False
                    errors.append("Collision geometry contains an invalid wall volume")
                    break
            else:
                checks["collision_walls_valid"] = True
        else:
            checks["collision_file"] = False
            errors.append("collision.json is missing or malformed")

        graph = self._load_json(graph_path, errors, "room_graph.json")
        graph_connected = False
        if graph:
            checks["room_graph"] = True
            nodes = graph.get("nodes", [])
            edges = graph.get("edges", [])
            stats["rooms"] = len(nodes)
            stats["doorway_edges"] = len(edges)
            checks["rooms_present"] = len(nodes) > 0
            if not nodes:
                errors.append("Room graph contains no rooms")
            graph_connected = bool(graph.get("connected", False))
            checks["rooms_connected"] = graph_connected or len(nodes) <= 1
            if len(nodes) > 1 and not graph_connected:
                warnings.append(
                    "Some rooms are not connected by detected doorways and cannot be reached."
                )
        else:
            checks["room_graph"] = False
            errors.append("room_graph.json is missing or malformed")

        rooms = self._scene_rooms(floor_plan_result or {}, graph or {})
        doorways = self._scene_doorways(floor_plan_result or {}, collision or {})
        windows = self._scene_windows(floor_plan_result or {})

        furniture = self._validate_furniture(
            output_dir,
            geometry_result,
            furnishing,
            rooms,
            doorways,
            windows,
            collision or {},
            graph or {},
            metadata or {},
            glb_json,
            checks,
            errors,
            warnings,
            stats,
        )

        navigation = self._validate_navigation(
            collision or {},
            graph or {},
            rooms,
            furniture,
            checks,
            errors,
            warnings,
            stats,
        )

        uses_photos = self._uses_photo_workflow(photo_manifest, metadata or {}, graph or {})
        if uses_photos:
            self._validate_photo_manifest(
                job_id,
                photo_manifest,
                metadata or {},
                graph or {},
                floor_plan_result or {},
                output_dir,
                checks,
                errors,
                stats,
            )
        else:
            checks["photo_free_workflow"] = True

        required = [
            "glb_exists",
            "glb_size_plausible",
            "glb_loadable",
            "has_meshes",
            "has_faces",
            "finite_bounds",
            "plausible_size",
            "no_degenerate_faces",
            "metadata_file",
            "asset_job_match",
            "collision_file",
            "collision_walls",
            "collision_walls_valid",
            "spawn_present",
            "spawn_walkable",
            "room_graph",
            "rooms_present",
            "spawn_clearance",
            "navigation_from_spawn",
            "doorways_reachable",
            "rooms_walkable",
        ]
        if uses_photos:
            required += [
                "photo_manifest",
                "photo_manifest_scope",
                "photo_review_confirmed",
                "photo_assignments_valid",
                "photo_files_exist",
                "photo_decodable",
                "room_photo_references",
                "room_textures_match",
                "texture_files_decode",
                "photo_textures_embedded",
            ]
        else:
            required.append("photo_free_workflow")
        if furniture is not None:
            required += [
                "furniture_file",
                "furniture_room_scope",
                "furniture_items_present",
                "furniture_placement_valid",
                "furniture_glb_nodes",
                "furniture_collision_match",
            ]

        geometry_valid = all(checks.get(name, False) for name in required)
        navigation_available = bool(
            geometry_valid
            and navigation.get("all_connected_rooms_reachable")
            and (graph_connected or len(graph.get("nodes", [])) <= 1)
        )
        return self._finish(
            job_id,
            output_dir,
            checks,
            errors,
            warnings,
            stats,
            geometry_valid,
            navigation_available,
        )

    # ------------------------------------------------------------------
    # Furniture
    # ------------------------------------------------------------------
    def _validate_furniture(
        self,
        output_dir: Path,
        geometry_result: Dict,
        furnishing: Optional[Dict],
        rooms: List[Dict],
        doorways: List[Dict],
        windows: List[Dict],
        collision: Dict,
        graph: Dict,
        metadata: Dict,
        glb_json: Dict,
        checks: Dict,
        errors: List[str],
        warnings: List[str],
        stats: Dict,
    ) -> Optional[Dict]:
        path = output_dir / "furniture.json"
        payload = furnishing
        if payload is None and path.exists():
            payload = self._load_json(path, errors, "furniture.json")
        expected = bool(payload) or bool(geometry_result.get("furniture_items"))
        if not expected:
            return None

        if not payload:
            checks["furniture_file"] = False
            errors.append("Furniture layout is missing; the scene cannot be complete.")
            return None
        checks["furniture_file"] = True

        items = list(payload.get("items") or [])
        stats["furniture_items"] = len(items)
        graph_rooms = {int(node.get("id")): node for node in graph.get("nodes", [])}
        scope_ok = True
        for item in items:
            try:
                room_id = int(item.get("room_id"))
            except (TypeError, ValueError):
                scope_ok = False
                errors.append(f"Furniture item {item.get('id')!r} has no valid room id")
                continue
            if room_id not in graph_rooms:
                scope_ok = False
                errors.append(
                    f"Furniture item {item.get('id')!r} references unknown room {room_id}"
                )
            if item.get("room_type") == "unknown":
                scope_ok = False
                errors.append(
                    f"Furniture item {item.get('id')!r} was placed in an unspecified room"
                )
        checks["furniture_room_scope"] = scope_ok

        if rooms:
            validation = validate_furnishing(payload, rooms, doorways, windows)
        else:
            validation = {
                "valid": False,
                "issues": ["Room outlines are missing, so furniture containment cannot be verified."],
                "navigation": {},
                "checked_items": len(items),
                "checked_rooms": 0,
            }
        checks["furniture_placement_valid"] = bool(validation.get("valid"))
        stats["furniture_validation"] = {
            "valid": bool(validation.get("valid")),
            "issues": validation.get("issues", []),
            "checked_items": validation.get("checked_items"),
            "checked_rooms": validation.get("checked_rooms"),
        }
        for issue in validation.get("issues", [])[:20]:
            errors.append(f"Furniture validation: {issue}")

        required_rooms = {
            int(node.get("id"))
            for node in graph.get("nodes", [])
            if str(node.get("room_type") or "unknown") in REQUIRED_FURNISHED_TYPES
        }
        furnished_rooms = {
            int(entry.get("room_id"))
            for entry in payload.get("rooms", [])
            if entry.get("status") in ("furnished", "partially_furnished")
        }
        missing = sorted(required_rooms - furnished_rooms)
        small_missing = []
        for room_id in missing:
            area = float(graph_rooms.get(room_id, {}).get("area_m2") or 0.0)
            if area < 3.0:
                small_missing.append(room_id)
            else:
                errors.append(
                    f"Room {room_id} is a {graph_rooms.get(room_id, {}).get('room_type')} "
                    "but received no furniture."
                )
        for room_id in small_missing:
            warnings.append(
                f"Room {room_id} is too small for standard furniture and was left clear."
            )
        missing_required = set(missing) - set(small_missing)
        checks["furniture_items_present"] = (not required_rooms and not items) or (
            bool(items) and not missing_required
        )
        if required_rooms and not items:
            errors.append("Furniture layout contains no items.")

        glb_items, glb_ok = self._glb_furniture_items(glb_json)
        item_ids = {str(item.get("id")) for item in items}
        missing_nodes = sorted(item_ids - glb_items)
        orphan_nodes = sorted(glb_items - item_ids)
        checks["furniture_glb_nodes"] = bool(glb_ok) and not missing_nodes and not orphan_nodes
        stats["furniture_glb"] = {
            "item_ids": len(item_ids),
            "glb_item_ids": len(glb_items),
            "missing": len(missing_nodes),
            "orphans": len(orphan_nodes),
            "materials": len(glb_json.get("materials") or []),
        }
        if not glb_ok:
            errors.append("scene.glb node table could not be read for furniture verification")
        if missing_nodes:
            errors.append(
                "Furniture is missing from scene.glb: " + ", ".join(missing_nodes[:10])
            )
        if orphan_nodes:
            errors.append(
                "scene.glb contains furniture nodes with no placement record: "
                + ", ".join(orphan_nodes[:10])
            )

        collision_match, collision_count = self._check_furniture_collision(
            items, collision.get("furniture") or []
        )
        checks["furniture_collision_match"] = collision_match
        stats["furniture_collision_items"] = collision_count
        if not collision_match:
            errors.append(
                "collision.json furniture volumes do not match the placed furniture "
                "(missing, extra, or altered bounds)"
            )

        stats["furniture"] = {
            "style": payload.get("style"),
            "variant": payload.get("variant"),
            "summary": payload.get("summary"),
            "validation": validation.get("navigation"),
        }
        return {
            "payload": payload,
            "items": items,
            "validation": validation,
            "rooms": rooms,
            "doorways": doorways,
        }

    def _glb_furniture_items(self, glb_json: Dict) -> Tuple[set, bool]:
        nodes = glb_json.get("nodes")
        if not isinstance(nodes, list):
            return set(), False
        found = set()
        for node in nodes:
            name = str(node.get("name") or "")
            if not name.startswith("Furniture_"):
                continue
            item_id = name[len("Furniture_") :].split("__", 1)[0]
            if item_id:
                found.add(item_id)
        return found, True

    def _check_furniture_collision(
        self, items: List[Dict], collision_items: List[Dict]
    ) -> Tuple[bool, int]:
        expected = {
            str(item.get("id")): item
            for item in items
            if item.get("collision") and item.get("collision_bounds")
        }
        found = {}
        for entry in collision_items:
            item_id = str(entry.get("item_id"))
            if item_id in found:
                return False, len(collision_items)
            found[item_id] = entry
        if set(expected) != set(found):
            return False, len(collision_items)
        for item_id, item in expected.items():
            entry = found[item_id]
            bounds = item["collision_bounds"]
            minimum = entry.get("min")
            maximum = entry.get("max")
            if (
                not minimum
                or not maximum
                or len(minimum) != 2
                or len(maximum) != 2
                or not all(np.isfinite(minimum + maximum))
            ):
                return False, len(collision_items)
            if any(
                abs(float(a) - float(b)) > 0.015
                for a, b in zip(minimum + maximum, bounds["min"] + bounds["max"])
            ):
                return False, len(collision_items)
            if int(entry.get("room_id", -1)) != int(item.get("room_id", -2)):
                return False, len(collision_items)
        return True, len(collision_items)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    def _validate_navigation(
        self,
        collision: Dict,
        graph: Dict,
        rooms: List[Dict],
        furniture: Optional[Dict],
        checks: Dict,
        errors: List[str],
        warnings: List[str],
        stats: Dict,
    ) -> Dict:
        walls = list(collision.get("walls") or [])
        doors = list(collision.get("doors") or [])
        furniture_boxes = list(collision.get("furniture") or [])
        spawn = (collision.get("spawn") or {}).get("position")
        polygons = {
            int(room["id"]): self._polygon(room)
            for room in rooms
            if room.get("id") is not None
        }
        polygons = {key: value for key, value in polygons.items() if value is not None}

        if not walls or not spawn:
            checks["spawn_clearance"] = False
            checks["navigation_from_spawn"] = False
            checks["doorways_reachable"] = False
            checks["rooms_walkable"] = False
            errors.append("Spawn point or collision walls are missing; navigation cannot be verified")
            return {"all_connected_rooms_reachable": False}

        x, _, z = spawn
        spawn_blocked = self._point_hits_boxes(x, z, walls, PLAYER_RADIUS) or self._point_hits_boxes(
            x, z, furniture_boxes, PLAYER_RADIUS
        )
        checks["spawn_clearance"] = not spawn_blocked
        if spawn_blocked:
            errors.append("Spawn point is inside a wall or piece of furniture")

        grid = self._build_navigation_grid(walls, furniture_boxes, polygons, doors)
        if grid is None:
            checks["navigation_from_spawn"] = False
            checks["doorways_reachable"] = False
            checks["rooms_walkable"] = False
            errors.append("Navigation grid could not be built for this floor plan")
            return {"all_connected_rooms_reachable": False}

        xs, zs, free = grid
        start = self._nearest_free(free, xs, zs, (x, z), max_distance=0.4)
        if start is None:
            checks["navigation_from_spawn"] = False
            checks["doorways_reachable"] = False
            checks["rooms_walkable"] = False
            errors.append("Spawn point has no walkable space around it")
            return {"all_connected_rooms_reachable": False}

        visited = np.zeros(free.shape, dtype=bool)
        queue = deque([start])
        visited[start] = True
        while queue:
            row, col = queue.popleft()
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nr, nc = row + dr, col + dc
                if 0 <= nr < free.shape[0] and 0 <= nc < free.shape[1]:
                    if free[nr, nc] and not visited[nr, nc]:
                        visited[nr, nc] = True
                        queue.append((nr, nc))

        reachable_cells = int(np.count_nonzero(visited))
        checks["navigation_from_spawn"] = reachable_cells > 0
        if reachable_cells == 0:
            errors.append("No walkable cells are reachable from the spawn point")

        grid_x, grid_z = np.meshgrid(xs, zs)
        room_stats: Dict[str, Dict] = {}
        for room_id, polygon in polygons.items():
            inside = contains_xy(polygon, grid_x, grid_z)
            reachable = int(np.count_nonzero(visited & inside))
            usable = int(np.count_nonzero(free & inside))
            room_stats[str(room_id)] = {
                "reachable_cells": reachable,
                "reachable_area_m2": round(reachable * NAV_GRID_M * NAV_GRID_M, 2),
                "free_cells": usable,
            }

        connected_ids = self._connected_room_ids(graph, set(polygons))
        unreachable_rooms = []
        for room_id in sorted(connected_ids):
            info = room_stats.get(str(room_id))
            if info is None:
                continue
            if info["reachable_cells"] == 0:
                unreachable_rooms.append(room_id)
                errors.append(
                    f"Room {room_id} cannot be reached from the spawn point; furniture or "
                    "wall geometry blocks every route."
                )
        for room_id, polygon in polygons.items():
            if room_id not in connected_ids and room_stats[str(room_id)]["reachable_cells"] == 0:
                warnings.append(
                    f"Room {room_id} has no connected doorway and cannot be reached."
                )
        checks["rooms_walkable"] = not unreachable_rooms
        checks["all_connected_rooms_reachable"] = not unreachable_rooms

        door_results: Dict[str, Dict] = {}
        unreachable_doors = []
        for door in doors:
            approach_points = self._door_approach_points(door, polygons)
            if not approach_points:
                continue
            results = []
            for room_id, point in approach_points:
                index = self._nearest_free(free, xs, zs, point, max_distance=0.4)
                reachable = bool(index is not None and visited[index])
                results.append(
                    {
                        "room_id": room_id,
                        "point": [round(point[0], 3), round(point[1], 3)],
                        "reachable": reachable,
                    }
                )
                if index is None:
                    errors.append(
                        f"Doorway {door.get('id')} has no walkable space on the room {room_id} side."
                    )
                elif not reachable:
                    errors.append(
                        f"Doorway {door.get('id')} cannot be reached from the spawn point on the "
                        f"room {room_id} side."
                    )
            door_results[str(door.get("id"))] = {"sides": results}
            if any(not side["reachable"] for side in results):
                unreachable_doors.append(door.get("id"))
        checks["doorways_reachable"] = not unreachable_doors
        if not doors:
            warnings.append(
                "The floor plan contains no walkable doorways; every room is an enclosed cell."
            )

        stats["navigation"] = {
            "reachable_area_m2": round(reachable_cells * NAV_GRID_M * NAV_GRID_M, 2),
            "grid_m": NAV_GRID_M,
            "player_radius_m": PLAYER_RADIUS,
            "rooms": room_stats,
            "doors": door_results,
            "connected_rooms": sorted(connected_ids),
            "unreachable_rooms": unreachable_rooms,
            "unreachable_doors": unreachable_doors,
        }
        return {
            "all_connected_rooms_reachable": checks["rooms_walkable"]
            and checks["doorways_reachable"],
            "grid": grid,
            "reachable": visited,
            "rooms": room_stats,
        }

    def _build_navigation_grid(
        self,
        walls: List[Dict],
        furniture: List[Dict],
        polygons: Dict[int, ShapelyPolygon],
        doors: Optional[List[Dict]] = None,
    ):
        if polygons:
            walkable_regions = [polygon.buffer(REGION_MARGIN_M) for polygon in polygons.values()]
            walkable_regions.extend(self._door_connector_patches(doors or []))
            region = unary_union(walkable_regions)
        else:
            all_boxes = walls + furniture
            if not all_boxes:
                return None
            min_x = min(float(entry["min"][0]) for entry in all_boxes) - 0.5
            min_z = min(float(entry["min"][1]) for entry in all_boxes) - 0.5
            max_x = max(float(entry["max"][0]) for entry in all_boxes) + 0.5
            max_z = max(float(entry["max"][1]) for entry in all_boxes) + 0.5
            from shapely.geometry import box as shapely_box

            region = shapely_box(min_x, min_z, max_x, max_z)

        min_x, min_z, max_x, max_z = region.bounds
        if not all(np.isfinite([min_x, min_z, max_x, max_z])):
            return None
        xs = np.arange(min_x + NAV_GRID_M / 2, max_x, NAV_GRID_M)
        zs = np.arange(min_z + NAV_GRID_M / 2, max_z, NAV_GRID_M)
        if xs.size == 0 or zs.size == 0 or xs.size * zs.size > 2_500_000:
            return None
        grid_x, grid_z = np.meshgrid(xs, zs)
        free = contains_xy(region, grid_x, grid_z)
        for entry in walls:
            free &= ~self._box_mask(grid_x, grid_z, entry, PLAYER_RADIUS)
        for entry in furniture:
            free &= ~self._box_mask(grid_x, grid_z, entry, PLAYER_RADIUS)
        return xs, zs, free

    @staticmethod
    def _door_connector_patches(doors: List[Dict]) -> List[ShapelyPolygon]:
        """Walkable strips that bridge the wall band at interior doorways.

        Room polygons stop at the wall faces, so their buffers do not always
        meet across a thick wall.  Without a connector the grid has no cells in
        the doorway and every room behind it looks unreachable.
        """
        patches: List[ShapelyPolygon] = []
        for door in doors:
            adjacent = [room for room in (door.get("rooms") or []) if room is not None]
            if door.get("exterior") or len(set(adjacent)) < 2:
                continue
            center = door.get("position") or door.get("center_m") or [0.0, 0.0]
            width = float(door.get("width_m") or door.get("detected_width_m") or 0.0)
            if not width or width <= 0:
                continue
            try:
                cx, cz = float(center[0]), float(center[1])
            except (TypeError, ValueError, IndexError):
                continue
            half_span = max(0.3, width / 2.0)
            half_depth = max(0.55, REGION_MARGIN_M + 0.4)
            if str(door.get("orientation") or "horizontal") == "horizontal":
                patch = shapely_box(cx - half_span, cz - half_depth, cx + half_span, cz + half_depth)
            else:
                patch = shapely_box(cx - half_depth, cz - half_span, cx + half_depth, cz + half_span)
            if not patch.is_empty:
                patches.append(patch)
        return patches

    @staticmethod
    def _box_mask(grid_x: np.ndarray, grid_z: np.ndarray, entry: Dict, margin: float):
        minimum = entry.get("min") or [0.0, 0.0]
        maximum = entry.get("max") or [0.0, 0.0]
        return (
            (grid_x >= float(minimum[0]) - margin)
            & (grid_x <= float(maximum[0]) + margin)
            & (grid_z >= float(minimum[1]) - margin)
            & (grid_z <= float(maximum[1]) + margin)
        )

    @staticmethod
    def _nearest_free(
        free: np.ndarray,
        xs: np.ndarray,
        zs: np.ndarray,
        point: Sequence[float],
        max_distance: float = 0.35,
    ):
        col = int(np.argmin(np.abs(xs - float(point[0]))))
        row = int(np.argmin(np.abs(zs - float(point[1]))))
        if 0 <= row < free.shape[0] and 0 <= col < free.shape[1] and free[row, col]:
            return row, col
        best = None
        best_distance = float("inf")
        radius = int(math.ceil(max_distance / NAV_GRID_M))
        for dr in range(-radius, radius + 1):
            for dc in range(-radius, radius + 1):
                nr, nc = row + dr, col + dc
                if 0 <= nr < free.shape[0] and 0 <= nc < free.shape[1] and free[nr, nc]:
                    distance = math.hypot(xs[nc] - point[0], zs[nr] - point[1])
                    if distance < best_distance:
                        best_distance = distance
                        best = (nr, nc)
        return best if best_distance <= max_distance else None

    @staticmethod
    def _door_approach_points(
        door: Dict, polygons: Dict[int, ShapelyPolygon]
    ) -> List[Tuple[int, Tuple[float, float]]]:
        center = door.get("position") or door.get("center_m") or [0.0, 0.0]
        orientation = str(door.get("orientation") or "horizontal")
        if orientation == "horizontal":
            offsets = ((0.0, APPROACH_M), (0.0, -APPROACH_M))
        else:
            offsets = ((APPROACH_M, 0.0), (-APPROACH_M, 0.0))
        adjacent = [int(room) for room in (door.get("rooms") or []) if room is not None]
        points: List[Tuple[int, Tuple[float, float]]] = []
        for room_id in adjacent:
            polygon = polygons.get(room_id)
            picked = None
            for distance in (APPROACH_M, 0.35, 0.25):
                for dx, dz in offsets:
                    scale = distance / APPROACH_M
                    point = (float(center[0]) + dx * scale, float(center[1]) + dz * scale)
                    if polygon is None or polygon.buffer(0.05).covers(
                        ShapelyPoint(point[0], point[1])
                    ):
                        picked = point
                        break
                if picked:
                    break
            if picked:
                points.append((room_id, picked))
        return points

    @staticmethod
    def _connected_room_ids(graph: Dict, known: set) -> set:
        nodes = [int(node.get("id")) for node in graph.get("nodes", [])]
        degree = {room_id: 0 for room_id in nodes}
        for edge in graph.get("edges", []):
            for key in ("from", "to"):
                value = edge.get(key)
                if value is None:
                    continue
                try:
                    room_id = int(value)
                except (TypeError, ValueError):
                    continue
                if room_id in degree:
                    degree[room_id] += 1
        if graph.get("connected"):
            return {room_id for room_id in nodes if room_id in known}
        return {room_id for room_id, count in degree.items() if count > 0 and room_id in known}

    # ------------------------------------------------------------------
    # Scene room data
    # ------------------------------------------------------------------
    @staticmethod
    def _polygon(room: Dict) -> Optional[ShapelyPolygon]:
        points = room.get("polygon_m") or room.get("polygon") or []
        if len(points) < 3:
            bounds = room.get("bounds_m") or room.get("bounds")
            if not bounds or len(bounds) < 4:
                return None
            x, z, w, d = bounds
            points = [[x, z], [x + w, z], [x + w, z + d], [x, z + d]]
        polygon = ShapelyPolygon([(float(px), float(pz)) for px, pz in points])
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        return None if polygon.is_empty else polygon

    def _scene_rooms(self, floor_plan_result: Dict, graph: Dict) -> List[Dict]:
        rooms = list(floor_plan_result.get("rooms") or [])
        if rooms:
            return rooms
        for node in graph.get("nodes", []):
            bounds = node.get("bounds") or [0, 0, 0, 0]
            rooms.append(
                {
                    "id": int(node.get("id")),
                    "polygon_m": node.get("polygon") or node.get("polygon_m"),
                    "bounds_m": bounds,
                    "center_m": node.get("center"),
                    "area_m2": node.get("area_m2"),
                    "room_type": node.get("room_type"),
                }
            )
        return rooms

    @staticmethod
    def _scene_doorways(floor_plan_result: Dict, collision: Dict) -> List[Dict]:
        doorways = list(floor_plan_result.get("doorways") or [])
        if doorways:
            return doorways
        result = []
        for door in collision.get("doors", []):
            result.append(
                {
                    "id": door.get("id"),
                    "center_m": door.get("position"),
                    "width_m": door.get("width_m"),
                    "orientation": door.get("orientation"),
                    "rooms": door.get("rooms") or [],
                    "kind": "door",
                }
            )
        return result

    @staticmethod
    def _scene_windows(floor_plan_result: Dict) -> List[Dict]:
        return list(floor_plan_result.get("windows") or [])

    # ------------------------------------------------------------------
    # Legacy photo workflow
    # ------------------------------------------------------------------
    @staticmethod
    def _uses_photo_workflow(
        photo_manifest: Optional[Dict], metadata: Dict, graph: Dict
    ) -> bool:
        if photo_manifest is not None:
            return True
        textures = ((metadata.get("method") or {}).get("textures")) or {}
        if textures.get("applied"):
            return True
        return any(node.get("photo_ids") for node in graph.get("nodes", []))

    def _validate_photo_manifest(
        self,
        job_id: str,
        photo_manifest: Dict,
        metadata: Dict,
        graph: Dict,
        floor_plan_result: Dict,
        output_dir: Path,
        checks: Dict,
        errors: List[str],
        stats: Dict,
    ):
        from photo_manifest import load_photo_manifest, photo_variant_path, usable_photos

        if photo_manifest is None:
            try:
                photo_manifest = load_photo_manifest(job_id)
            except (OSError, json.JSONDecodeError):
                photo_manifest = None
        checks["photo_manifest"] = bool(photo_manifest)
        if not photo_manifest:
            errors.append("photo_manifest.json is missing; photo assignments cannot be verified")
            for name in (
                "photo_manifest_scope",
                "photo_review_confirmed",
                "photo_assignments_valid",
                "photo_files_exist",
                "photo_decodable",
                "room_photo_references",
                "room_textures_match",
                "texture_files_decode",
                "photo_textures_embedded",
            ):
                checks[name] = False
            return

        textures = ((metadata.get("method") or {}).get("textures")) or {}
        if textures.get("applied"):
            try:
                glb_json = self._read_glb_json(output_dir / "scene.glb")
            except (OSError, ValueError, json.JSONDecodeError):
                glb_json = {}
            image_count = len(glb_json.get("images") or [])
            texture_count = len(glb_json.get("textures") or [])
            stats["embedded_images"] = image_count
            stats["embedded_textures"] = texture_count
            checks["photo_textures_embedded"] = image_count > 0 and texture_count > 0
            if not checks["photo_textures_embedded"]:
                errors.append(
                    "scene.glb does not embed the photographic textures claimed by metadata.json"
                )
        else:
            checks["photo_textures_embedded"] = True

        photos = photo_manifest.get("photos", [])
        checks["photo_manifest_scope"] = photo_manifest.get("job_id") == job_id and all(
            photo.get("job_id") == job_id for photo in photos
        )
        if not checks["photo_manifest_scope"]:
            errors.append("photo_manifest.json contains photographs from another job")

        checks["photo_review_confirmed"] = (
            (photo_manifest.get("review") or {}).get("status") == "confirmed"
        )
        if not checks["photo_review_confirmed"]:
            errors.append("Photo assignments have not been confirmed by the user")

        usable = usable_photos(photo_manifest)
        manifest_ids = {photo["photo_id"] for photo in usable}
        plan_room_ids = {
            int(room["id"]) for room in (floor_plan_result.get("rooms") or [])
        }
        graph_ids = {int(node.get("id")) for node in graph.get("nodes", [])}
        room_ids = graph_ids or plan_room_ids
        stats["manifest_photos"] = len(photos)
        stats["usable_photos"] = len(usable)

        assigned_ids = set()
        assignments_valid = True
        for photo in usable:
            assignment = photo.get("room_assignment") or {}
            if assignment.get("excluded"):
                continue
            room_id = assignment.get("room_id")
            if (
                not assignment.get("confirmed")
                or room_id is None
                or int(room_id) not in room_ids
            ):
                assignments_valid = False
                errors.append(
                    f"Photograph {photo['photo_id']} is not confirmed for a room in this job"
                )
                continue
            assigned_ids.add(photo["photo_id"])
        if not usable:
            assignments_valid = False
            errors.append("No usable photographs remain after review")
        checks["photo_assignments_valid"] = assignments_valid
        stats["assigned_photos"] = len(assigned_ids)

        files_exist = True
        decodable = True
        for photo in usable:
            for variant in ("original", "optimized", "thumbnail"):
                try:
                    path = photo_variant_path(job_id, photo, variant)
                except (FileNotFoundError, ValueError, KeyError):
                    files_exist = False
                    errors.append(
                        f"Photograph {photo['photo_id']} has no {variant} file"
                    )
                    continue
                if not path.is_file():
                    files_exist = False
                    errors.append(
                        f"Photograph {photo['photo_id']} {variant} file is missing"
                    )
                    continue
                if variant != "original":
                    try:
                        with Image.open(path) as image:
                            image.verify()
                    except Exception as exc:  # noqa: BLE001
                        decodable = False
                        errors.append(
                            f"Photograph {photo['photo_id']} {variant} cannot be decoded: {exc}"
                        )
        checks["photo_files_exist"] = files_exist
        checks["photo_decodable"] = decodable

        references_valid = True
        for node in graph.get("nodes", []):
            node_room_id = int(node.get("id"))
            for photo_id in node.get("photo_ids") or []:
                photo = next(
                    (item for item in usable if item["photo_id"] == photo_id), None
                )
                if photo is None:
                    references_valid = False
                    errors.append(
                        f"Room {node_room_id} references unknown photograph {photo_id}"
                    )
                    continue
                assigned_room = (photo.get("room_assignment") or {}).get("room_id")
                if assigned_room is None or int(assigned_room) != node_room_id:
                    references_valid = False
                    errors.append(
                        f"Room {node_room_id} references photograph {photo_id} that is "
                        "assigned to a different room"
                    )
        checks["room_photo_references"] = references_valid

        texture_map = textures.get("room_map") or {}
        textures_match = True
        for raw_room_id, entry in texture_map.items():
            try:
                room_id = int(raw_room_id)
            except (TypeError, ValueError):
                textures_match = False
                errors.append(f"Texture room key {raw_room_id!r} is not a room id")
                continue
            if room_id not in room_ids:
                textures_match = False
                errors.append(f"Texture mapping references unknown room {room_id}")
                continue
            referenced = set(entry.get("photo_ids") or [])
            if referenced and not referenced <= manifest_ids:
                textures_match = False
                errors.append(
                    f"Room {room_id} texture references photographs outside the manifest"
                )
            for photo_id in referenced:
                photo = next(
                    (item for item in usable if item["photo_id"] == photo_id), None
                )
                assigned_room = (photo.get("room_assignment") or {}).get("room_id")
                if (
                    photo is None
                    or assigned_room is None
                    or int(assigned_room) != room_id
                ):
                    textures_match = False
                    errors.append(
                        f"Room {room_id} texture comes from a photograph assigned elsewhere"
                    )
        if textures.get("applied") and assigned_ids and not texture_map:
            textures_match = False
            errors.append("Photographic textures are claimed without a room-photo mapping")
        checks["room_textures_match"] = textures_match

        texture_files_decode = True
        for photo in usable:
            photo_textures = photo.get("textures") or {}
            for key in ("wall_texture", "floor_texture"):
                relative = photo_textures.get(key)
                if not relative:
                    continue
                path = (Path(__file__).parent / "geometry" / job_id / relative).resolve()
                root = (Path(__file__).parent / "geometry" / job_id).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    texture_files_decode = False
                    errors.append(
                        f"Texture file for photograph {photo['photo_id']} is missing"
                    )
                    continue
                try:
                    with Image.open(path) as image:
                        image.verify()
                except Exception as exc:  # noqa: BLE001
                    texture_files_decode = False
                    errors.append(
                        f"Texture file for photograph {photo['photo_id']} cannot be "
                        f"decoded: {exc}"
                    )
        checks["texture_files_decode"] = texture_files_decode

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _finish(
        self,
        job_id: str,
        output_dir: Path,
        checks: Dict,
        errors: List[str],
        warnings: List[str],
        stats: Dict,
        geometry_valid,
        navigation_available,
    ) -> Dict:
        checks = {key: bool(value) for key, value in checks.items()}
        geometry_valid = bool(geometry_valid) and not errors
        if geometry_valid is False and not errors:
            errors.append("Scene validation failed")
        result = {
            "status": "completed" if geometry_valid else "failed",
            "geometry_valid": geometry_valid,
            "navigation_available": bool(navigation_available) if geometry_valid else False,
            "scale_verified": None,  # filled from floor plan metadata by the caller
            "checks": checks,
            "errors": errors,
            "warnings": warnings,
            "stats": stats,
        }
        if output_dir and str(output_dir):
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "validation_report.json").write_text(json.dumps(result, indent=2))
        return result

    def _read_glb_json(self, path: Path) -> Dict:
        with open(path, "rb") as handle:
            header = handle.read(12)
            if len(header) < 12 or header[:4] != b"glTF":
                raise ValueError("not a binary glTF file")
            chunk_header = handle.read(8)
            if len(chunk_header) < 8:
                raise ValueError("truncated GLB header")
            chunk_length, chunk_type = struct.unpack("<II", chunk_header)
            if chunk_type != 0x4E4F534A:
                raise ValueError("first GLB chunk is not JSON")
            return json.loads(handle.read(chunk_length))

    def _load_json(self, path: Path, errors: List[str], label: str) -> Dict:
        if not path.exists():
            errors.append(f"{label} is missing")
            return {}
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            errors.append(f"{label} is malformed: {exc}")
            return {}

    @staticmethod
    def _point_hits_boxes(
        x: float, z: float, boxes: List[Dict], margin: float
    ) -> bool:
        for entry in boxes:
            minimum = entry.get("min", [0, 0])
            maximum = entry.get("max", [0, 0])
            if (
                minimum[0] - margin <= x <= maximum[0] + margin
                and minimum[1] - margin <= z <= maximum[1] + margin
            ):
                return True
        return False
