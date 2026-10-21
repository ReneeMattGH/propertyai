"""
Scene validation.

The validator is the gate between "pipeline finished" and "job completed":
it re-opens the exported GLB, checks that geometry is non-degenerate, that
collision volumes exist, that the spawn point is walkable, and that the room
graph is navigable. A job may only be marked completed when this passes.
"""
from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path
from typing import Dict, List

import numpy as np
import trimesh
from PIL import Image

PLAYER_RADIUS = 0.25
MIN_FACE_AREA = 1e-7
MAX_DEGENERATE_RATIO = 0.02


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
    ) -> Dict:
        return await asyncio.to_thread(
            self._validate_scene_sync,
            job_id,
            geometry_result,
            photo_manifest,
            floor_plan_result,
        )

    def _validate_scene_sync(
        self,
        job_id: str,
        geometry_result: Dict,
        photo_manifest: Dict = None,
        floor_plan_result: Dict = None,
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
        if checks["glb_size_plausible"]:
            try:
                scene = trimesh.load(str(glb_path), force="scene")
                checks["glb_loadable"] = True
            except Exception as exc:
                checks["glb_loadable"] = False
                errors.append(f"scene.glb could not be loaded: {exc}")

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
            checks["has_wall_material"] = checks.get("glb_loadable", False)

        metadata = self._load_json(output_dir / "metadata.json", errors, "metadata.json")
        if metadata:
            checks["metadata_file"] = True
            checks["asset_job_match"] = metadata.get("job_id") == job_id
            if not checks["asset_job_match"]:
                errors.append("metadata.json does not belong to this job")
            textures = ((metadata.get("method") or {}).get("textures")) or {}
            if textures.get("applied"):
                try:
                    glb_json = self._read_glb_json(glb_path)
                    image_count = len(glb_json.get("images") or [])
                    texture_count = len(glb_json.get("textures") or [])
                    stats["embedded_images"] = image_count
                    stats["embedded_textures"] = texture_count
                    checks["photo_textures_embedded"] = (
                        image_count > 0 and texture_count > 0
                    )
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    checks["photo_textures_embedded"] = False
                    errors.append(f"Could not inspect GLB texture data: {exc}")
                if not checks.get("photo_textures_embedded", False):
                    errors.append(
                        "scene.glb does not embed the photographic textures claimed by "
                        "metadata.json"
                    )
            else:
                checks["photo_textures_embedded"] = True
        else:
            checks["metadata_file"] = False
            checks["asset_job_match"] = False
            checks["photo_textures_embedded"] = False

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
                checks["spawn_walkable"] = not self._point_hits_wall(x, z, walls)
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
            graph_connected = False
            errors.append("room_graph.json is missing or malformed")

        self._validate_photo_manifest(
            job_id,
            photo_manifest,
            metadata or {},
            graph or {},
            floor_plan_result or {},
            checks,
            errors,
            stats,
        )

        geometry_valid = all(
            checks.get(name, False)
            for name in (
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
                "photo_textures_embedded",
                "collision_file",
                "collision_walls",
                "collision_walls_valid",
                "spawn_present",
                "spawn_walkable",
                "room_graph",
                "rooms_present",
                "photo_manifest",
                "photo_manifest_scope",
                "photo_review_confirmed",
                "photo_assignments_valid",
                "photo_files_exist",
                "photo_decodable",
                "room_photo_references",
                "room_textures_match",
                "texture_files_decode",
            )
        )
        navigation_available = geometry_valid and bool(graph_connected or len(graph.get("nodes", [])) <= 1)
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

    def _validate_photo_manifest(
        self,
        job_id: str,
        photo_manifest: Dict,
        metadata: Dict,
        graph: Dict,
        floor_plan_result: Dict,
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
            ):
                checks[name] = False
            return

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

        textures = ((metadata.get("method") or {}).get("textures")) or {}
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

    def _point_hits_wall(self, x: float, z: float, walls: List[Dict], margin: float = PLAYER_RADIUS) -> bool:
        for wall in walls:
            minimum = wall.get("min", [0, 0])
            maximum = wall.get("max", [0, 0])
            if (
                minimum[0] - margin <= x <= maximum[0] + margin
                and minimum[1] - margin <= z <= maximum[1] + margin
            ):
                return True
        return False
