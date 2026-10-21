"""
Geometry builder.

Converts parsed floor plan data (pixel space + scale) into a navigable 3D scene:

  * per-room floor and ceiling slabs (extruded from the detected room polygons)
  * thick walls with real doorway openings and lintels above them
  * door thresholds so floors are continuous through openings
  * axis-aligned collision volumes in metres (for the browser physics)
  * a room graph with doorway edges and reachability information
  * a GLB scene whose wall and floor materials are real crops from the
    uploaded photographs, embedded as textures in the exported asset

The layout is constrained by the floor plan; appearance is derived from the
photos. Sparse structure-from-motion (COLMAP) runs as a separate stage and its
results are recorded here. Dense image-derived geometry is only used when the
photogrammetry stage produced one. This is not a measured survey; callers must
surface the returned limitations to users.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import trimesh
from PIL import Image
from shapely.geometry import Point, Polygon
from trimesh.visual import TextureVisuals

FLOOR_THICKNESS = 0.06
CEILING_THICKNESS = 0.06
WALL_HEIGHT = 2.8
DOOR_HEIGHT = 2.1
WINDOW_SILL_HEIGHT = 0.9
WINDOW_HEAD_HEIGHT = 2.1
GLASS_COLOR = (168, 205, 227)
MIN_PASSABLE_DOOR_M = 0.8
MAX_PASSABLE_DOOR_M = 1.8
MIN_WALL_LENGTH_M = 0.05
TEXTURE_TILE_M = 2.4


class GeometryBuildError(Exception):
    pass


def _rgba(rgb: Tuple[int, int, int]) -> List[int]:
    return [int(rgb[0]), int(rgb[1]), int(rgb[2]), 255]


def _polygon_from_room(room: Dict) -> Optional[Polygon]:
    points = room.get("polygon_m") or []
    if len(points) < 4:
        x, z, w, d = room.get("bounds_m", [0, 0, 0, 0])
        if w <= 0 or d <= 0:
            return None
        points = [[x, z], [x + w, z], [x + w, z + d], [x, z + d]]
    polygon = Polygon(points)
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    if polygon.is_empty:
        return None
    if polygon.geom_type == "MultiPolygon":
        polygon = max(polygon.geoms, key=lambda g: g.area)
    if polygon.area <= 0.2:
        return None
    return polygon


def _extrude_flat(polygon: Polygon, bottom_y: float, height: float) -> trimesh.Trimesh:
    min_x, min_z, max_x, max_z = polygon.bounds
    area = max(1e-9, (max_x - min_x) * (max_z - min_z))
    if not polygon.interiors and polygon.area >= 0.98 * area:
        # Rectangular rooms need no triangulation engine.
        plate = trimesh.creation.box(extents=(max_x - min_x, height, max_z - min_z))
        plate.apply_translation(
            ((min_x + max_x) / 2, bottom_y + height / 2, (min_z + max_z) / 2)
        )
        return plate
    try:
        mesh = trimesh.creation.extrude_polygon(polygon, height=height)
    except ValueError as exc:
        raise GeometryBuildError(
            "Triangulating a non-rectangular room requires the 'mapbox-earcut' "
            f"package in the reconstruction environment: {exc}"
        ) from exc
    # trimesh extrudes along +Z; rotate so extrusion runs along -Y, then place it.
    mesh.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
    mesh.apply_translation([0, bottom_y + height, 0])
    return mesh


def _box(extents: Tuple[float, float, float], center: Tuple[float, float, float]) -> trimesh.Trimesh:
    mesh = trimesh.creation.box(extents=extents)
    mesh.apply_translation(center)
    return mesh


def _textured_box(
    extents: Tuple[float, float, float],
    center: Tuple[float, float, float],
    image: Image.Image,
    tile: float = TEXTURE_TILE_M,
    offset: Tuple[float, float] = (0.0, 0.0),
) -> trimesh.Trimesh:
    """Axis-aligned box with per-face UVs so photo textures repeat correctly."""
    width, height, depth = extents
    cx, cy, cz = center
    x0, x1 = cx - width / 2, cx + width / 2
    y0, y1 = cy - height / 2, cy + height / 2
    z0, z1 = cz - depth / 2, cz + depth / 2

    faces = [
        # (corners in outward-facing CCW order, u axis per corner, v axis per corner)
        ([(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)], "x", "y"),
        ([(x1, y0, z0), (x0, y0, z0), (x0, y1, z0), (x1, y1, z0)], "x", "y"),
        ([(x1, y0, z1), (x1, y0, z0), (x1, y1, z0), (x1, y1, z1)], "z", "y"),
        ([(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)], "z", "y"),
        ([(x0, y1, z1), (x1, y1, z1), (x1, y1, z0), (x0, y1, z0)], "x", "z"),
        ([(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)], "x", "z"),
    ]
    axis = {"x": 0, "y": 1, "z": 2}
    vertices: List[Tuple[float, float, float]] = []
    uv: List[Tuple[float, float]] = []
    triangles: List[Tuple[int, int, int]] = []
    for corners, u_axis, v_axis in faces:
        base = len(vertices)
        u_index, v_index = axis[u_axis], axis[v_axis]
        for corner in corners:
            vertices.append(corner)
            uv.append(
                (
                    corner[u_index] / tile + offset[0],
                    corner[v_index] / tile + offset[1],
                )
            )
        triangles.append((base, base + 1, base + 2))
        triangles.append((base, base + 2, base + 3))
    mesh = trimesh.Trimesh(
        vertices=np.asarray(vertices, dtype=np.float64),
        faces=np.asarray(triangles, dtype=np.int64),
        process=False,
    )
    mesh.visual = TextureVisuals(
        uv=np.asarray(uv, dtype=np.float64), image=image
    )
    return mesh


def _textured_polygon(
    polygon: Polygon,
    top_y: float,
    image: Image.Image,
    tile: float = TEXTURE_TILE_M,
) -> trimesh.Trimesh:
    """Planar room floor with UVs derived from plan coordinates."""
    try:
        points, faces = trimesh.creation.triangulate_polygon(polygon)
    except Exception:
        min_x, min_z, max_x, max_z = polygon.bounds
        points = np.asarray(
            [[min_x, min_z], [max_x, min_z], [max_x, max_z], [min_x, max_z]],
            dtype=np.float64,
        )
        faces = np.asarray([[0, 1, 2], [0, 2, 3]], dtype=np.int64)
    vertices = np.column_stack(
        [points[:, 0], np.full(len(points), top_y), points[:, 1]]
    )
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    if float(np.sum(mesh.face_normals[:, 1])) < 0:
        mesh.invert()
    uv = np.column_stack([points[:, 0] / tile, points[:, 1] / tile])
    mesh.visual = TextureVisuals(uv=uv, image=image)
    return mesh


def _merge_textured(meshes: List[trimesh.Trimesh], image: Image.Image) -> trimesh.Trimesh:
    vertices = np.concatenate([mesh.vertices for mesh in meshes], axis=0)
    uv = np.concatenate([np.asarray(mesh.visual.uv) for mesh in meshes], axis=0)
    faces = []
    offset = 0
    for mesh in meshes:
        faces.append(np.asarray(mesh.faces) + offset)
        offset += len(mesh.vertices)
    merged = trimesh.Trimesh(
        vertices=vertices,
        faces=np.concatenate(faces, axis=0),
        process=False,
    )
    merged.visual = TextureVisuals(uv=uv, image=image)
    return merged


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(output_dir: Path, manifest: Dict) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    tmp_path = output_dir / "manifest.json.tmp"
    tmp_path.write_text(json.dumps(manifest, indent=2, default=str))
    tmp_path.replace(manifest_path)
    return manifest_path


ASSET_MEDIA_TYPES = {
    ".glb": "model/gltf-binary",
    ".json": "application/json",
    ".ply": "model/ply",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def build_asset_entries(output_dir: Path, names: List[str]) -> List[Dict]:
    assets = []
    for name in names:
        path = output_dir / name
        if not path.exists():
            continue
        assets.append(
            {
                "name": name,
                "type": ASSET_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return assets


class GeometryBuilder:
    """Build 3D geometry from floor plan data."""

    def check_open3d(self) -> bool:
        try:
            import open3d  # noqa: F401

            return True
        except ImportError:
            return False

    async def build_geometry(
        self,
        job_id: str,
        floor_plan_result: Dict,
        photo_result: Dict,
        num_floors: int,
        known_width: Optional[float] = None,
        known_length: Optional[float] = None,
        photogrammetry_result: Optional[Dict] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
        photo_manifest: Optional[Dict] = None,
        room_metadata: Optional[Dict[int, Dict]] = None,
        use_photo_floor_textures: bool = False,
    ) -> Dict:
        return await asyncio.to_thread(
            self._build_geometry_sync,
            job_id,
            floor_plan_result,
            photo_result,
            num_floors,
            known_width,
            known_length,
            photogrammetry_result,
            progress_callback,
            photo_manifest,
            room_metadata,
            use_photo_floor_textures,
        )

    def _build_geometry_sync(
        self,
        job_id: str,
        floor_plan_result: Dict,
        photo_result: Dict,
        num_floors: int,
        known_width: Optional[float],
        known_length: Optional[float],
        photogrammetry_result: Optional[Dict] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
        photo_manifest: Optional[Dict] = None,
        room_metadata: Optional[Dict[int, Dict]] = None,
        use_photo_floor_textures: bool = False,
    ) -> Dict:
        def emit(stage: str):
            if progress_callback is None:
                return
            try:
                progress_callback(stage)
            except Exception:
                pass

        emit("building_geometry")
        if floor_plan_result.get("status") != "completed":
            raise GeometryBuildError(
                floor_plan_result.get("error", "Floor plan did not parse successfully")
            )
        rooms = floor_plan_result.get("rooms", [])
        walls = floor_plan_result.get("wall_segments", [])
        doorways = floor_plan_result.get("doorways", [])
        windows = floor_plan_result.get("windows", [])
        if not rooms:
            raise GeometryBuildError("No rooms available to build geometry")
        if not walls:
            raise GeometryBuildError("No walls available to build geometry")

        photogrammetry = photogrammetry_result or {}
        room_metadata = room_metadata or {}
        emit("texturing")
        texture_sets = self._load_texture_sets(
            job_id, photo_manifest, use_photo_floor_textures
        )
        room_textures = self._assign_room_textures(rooms, texture_sets)

        warnings: List[str] = []
        limitations: List[str] = []
        if num_floors and num_floors > 1:
            limitations.append(
                f"Multi-floor reconstruction is not implemented; only the ground floor "
                f"({num_floors} floors were requested) is generated."
            )
        if not floor_plan_result.get("scale_verified"):
            limitations.append(
                "Dimensions are approximate: scale was estimated, not measured."
            )
        limitations.append(
            "Only axis-aligned walls detected in the floor plan are reconstructed."
        )
        limitations.append(
            "Door leaves, stairs and furniture are not reconstructed. Windows are "
            "rendered as openings with inferred sill/head heights and tinted glazing."
        )
        limitations.extend(
            self._photogrammetry_limitations(photogrammetry, bool(texture_sets))
        )
        if use_photo_floor_textures:
            limitations.append(
                "Photo floor textures were explicitly enabled for this job; floor "
                "photographs are tiled and are not a measured floor finish."
            )
        else:
            limitations.append(
                "Floors and ceilings use neutral or photo-sampled colours; complete room "
                "photographs are not mapped onto floor or ceiling surfaces."
            )

        palette = photo_result.get("palette", {}) if photo_result else {}
        wall_color = tuple(palette.get("wall_rgb") or (228, 224, 216))
        floor_color = tuple(palette.get("floor_rgb") or (188, 169, 145))
        ceiling_color = (246, 246, 244)

        geometry = self._build_meshes(
            rooms,
            walls,
            doorways,
            wall_color,
            floor_color,
            ceiling_color,
            room_textures,
            windows,
            use_photo_floor_textures=use_photo_floor_textures,
        )
        collision = self._build_collision(
            rooms, walls, doorways, floor_plan_result, windows
        )
        collision = self._apply_collision_metadata(collision, room_metadata)
        room_graph = self._build_room_graph(
            rooms,
            doorways,
            collision["spawn"],
            room_metadata,
            job_id,
        )

        window_summary = geometry["summary"]
        unmatched_windows = (
            window_summary["windows_requested"] - window_summary["windows_rendered"]
        )
        if unmatched_windows > 0:
            warnings.append(
                f"{unmatched_windows} window opening(s) could not be matched to a "
                "detected wall segment and were not rendered as glazing."
            )

        for room in rooms:
            if not any(
                room["id"] in (edge["from"], edge["to"]) for edge in room_graph["edges"]
            ):
                warnings.append(
                    f"Room {room['id'] + 1} has no detected doorway; it cannot be reached."
                )
        if len(rooms) > 1 and not room_graph["connected"]:
            warnings.append(
                "Not all rooms are connected by detected doorways. Some areas cannot be "
                "reached in the walkthrough."
            )
        warnings.extend(photogrammetry.get("warnings", []))
        warnings.extend(self._photogrammetry_alignment_warnings(photogrammetry))

        output_dir = Path(__file__).parent / "output" / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        glb_path = output_dir / "scene.glb"
        emit("exporting")
        self._export_glb(glb_path, geometry)

        collision_path = output_dir / "collision.json"
        collision_path.write_text(json.dumps(collision, indent=2))
        graph_path = output_dir / "room_graph.json"
        graph_path.write_text(json.dumps(room_graph, indent=2))

        photogrammetry_assets = self._copy_photogrammetry_assets(
            job_id, photogrammetry, output_dir
        )
        texture_summary = self._texture_summary(
            texture_sets, room_textures, room_metadata, use_photo_floor_textures
        )
        sfm_summary = {
            "status": photogrammetry.get("status", "not_run"),
            "engine": photogrammetry.get("engine"),
            "input_images": photogrammetry.get("input_images", 0),
            "registered_images": photogrammetry.get("registered_images", 0),
            "registration_ratio": photogrammetry.get("registration_ratio", 0.0),
            "sparse_points": photogrammetry.get("sparse_points", 0),
            "mean_reprojection_error_px": photogrammetry.get("mean_reprojection_error_px"),
            "matched_pairs": photogrammetry.get("matched_pairs", 0),
            "inlier_matches": photogrammetry.get("inlier_matches", 0),
            "dense": photogrammetry.get("dense"),
            "alignment": photogrammetry.get("alignment"),
            "point_cloud_asset": (
                "sparse_points.ply"
                if "sparse_points.ply" in photogrammetry_assets
                else None
            ),
            "used_for_geometry": False,
        }

        metadata = {
            "job_id": job_id,
            "format": "glb",
            "vertices": int(geometry["vertex_count"]),
            "faces": int(geometry["face_count"]),
            "rooms": len(rooms),
            "num_floors": 1,
            "floor_height": WALL_HEIGHT,
            "doorways_included": len(doorways),
            "windows_included": len(windows),
            "collision_available": bool(collision["walls"]),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "method": {
                "layout": "floor-plan wall vectorisation + room segmentation "
                "(structural constraint)",
                "appearance": (
                    "room-confirmed wall texture crops from the uploaded photographs, "
                    "embedded in scene.glb; floors and ceilings are not photo-mapped"
                    if texture_sets
                    else "neutral materials with colours sampled from the uploaded photographs"
                ),
                "textures": texture_summary,
                "image_reconstruction": {
                    "sfm": sfm_summary,
                    "dense_geometry_used": False,
                },
                "photogrammetry_used_for_geometry": False,
            },
            "inferred": [
                "Ceiling height is fixed at 2.8 m and ceilings are not visible in the "
                "uploaded photographs.",
                "Photographs are mapped only to the rooms the user confirmed; rooms "
                "without a confirmed photograph use neutral materials.",
                "Window sill and head heights are inferred at 0.9 m and 2.1 m; glazing "
                "is rendered as tinted panes rather than measured window geometry.",
                "Wall texture crops repeat across large surfaces; they are appearance "
                "samples, not a photographic survey of every wall.",
            ],
            "room_assignments": [
                {
                    "room_id": room_id,
                    "name": metadata.get("name"),
                    "room_type": metadata.get("room_type"),
                    "photo_ids": metadata.get("photo_ids", []),
                }
                for room_id, metadata in sorted(room_metadata.items())
            ],
            "scale": {
                "pixels_per_meter": floor_plan_result.get("scale_pixels_per_meter"),
                "source": floor_plan_result.get("scale_source"),
                "verified": floor_plan_result.get("scale_verified", False),
            },
            "footprint_m": floor_plan_result.get("footprint_m"),
        }
        (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))

        assets = build_asset_entries(
            output_dir,
            ["scene.glb", "collision.json", "room_graph.json", "metadata.json"]
            + photogrammetry_assets,
        )
        self._check_geometry(geometry, collision, room_graph)

        return {
            "status": "completed",
            "vertices_count": geometry["vertex_count"],
            "faces_count": geometry["face_count"],
            "rooms_count": len(rooms),
            "collision_walls": len(collision["walls"]),
            "room_graph_edges": len(room_graph["edges"]),
            "output_dir": str(output_dir),
            "assets": assets,
            "primary_format": "glb",
            "warnings": warnings,
            "limitations": limitations,
            "metadata": metadata,
            "geometry": geometry["summary"],
            "photogrammetry": sfm_summary,
            "textures": texture_summary,
            "photogrammetry_assets": photogrammetry_assets,
        }

    # ------------------------------------------------------------------
    # Photo textures and photogrammetry provenance
    # ------------------------------------------------------------------
    def _load_texture_sets(
        self,
        job_id: str,
        photo_manifest: Optional[Dict],
        use_photo_floor_textures: bool,
    ) -> List[Dict]:
        """Load only textures from photos the user assigned to real rooms."""
        from photo_manifest import usable_photos

        if not photo_manifest:
            return []
        root = Path(__file__).parent / "geometry" / job_id
        texture_sets: List[Dict] = []
        for photo in usable_photos(photo_manifest):
            assignment = photo.get("room_assignment") or {}
            room_id = assignment.get("room_id")
            if room_id is None or assignment.get("excluded"):
                continue
            textures = photo.get("textures") or {}
            wall_rel = textures.get("wall_texture")
            floor_rel = textures.get("floor_texture") if use_photo_floor_textures else None
            wall_image = None
            floor_image = None
            try:
                if wall_rel:
                    wall_image = Image.open(root / wall_rel).convert("RGB")
            except (OSError, ValueError):
                wall_image = None
            try:
                if floor_rel:
                    floor_image = Image.open(root / floor_rel).convert("RGB")
            except (OSError, ValueError):
                floor_image = None
            if wall_image is None and floor_image is None:
                continue
            if wall_image is None:
                wall_image = floor_image
            if floor_image is None:
                floor_image = wall_image
            classification = photo.get("classification") or {}
            texture_sets.append(
                {
                    "index": len(texture_sets),
                    "photo_id": photo["photo_id"],
                    "room_id": int(room_id),
                    "source_file": photo["original"].get("original_filename"),
                    "stored_file": photo["original"].get("stored_filename"),
                    "predicted_room_type": classification.get("predicted_room_type"),
                    "classification_confidence": classification.get("confidence"),
                    "wall_image": wall_image,
                    "floor_image": floor_image,
                    "wall_texture": wall_rel,
                    "floor_texture": floor_rel,
                    "wall_rgb": textures.get("wall_rgb"),
                    "floor_rgb": textures.get("floor_rgb"),
                }
            )
        return texture_sets

    def _assign_room_textures(self, rooms: List[Dict], texture_sets: List[Dict]) -> Dict:
        """Choose one primary wall texture per room from that room's own photos."""
        room_ids = {int(room["id"]) for room in rooms}
        grouped: Dict[int, List[Dict]] = {}
        for texture in texture_sets:
            room_id = int(texture["room_id"])
            if room_id not in room_ids:
                continue
            grouped.setdefault(room_id, []).append(texture)
        primaries = {}
        for room_id, textures in grouped.items():
            primaries[room_id] = max(
                textures,
                key=lambda texture: texture.get("classification_confidence") or 0.0,
            )
        return primaries

    def _photogrammetry_alignment_warnings(self, photogrammetry: Dict) -> List[str]:
        alignment = photogrammetry.get("alignment") or {}
        aspect_delta = alignment.get("aspect_delta")
        if (
            photogrammetry.get("status") in {"reconstructed", "partial"}
            and aspect_delta is not None
            and float(aspect_delta) > 0.5
        ):
            return [
                "Photograph camera spread differs from the floor-plan footprint by "
                f"{float(aspect_delta):.0%} in aspect ratio. The photographs and the plan "
                "may not describe the same property; verify the plan matches the uploaded "
                "photographs."
            ]
        return []

    def _photogrammetry_limitations(self, photogrammetry: Dict, textures_applied: bool) -> List[str]:
        status = photogrammetry.get("status", "not_run")
        registered = int(photogrammetry.get("registered_images") or 0)
        inputs = int(photogrammetry.get("input_images") or 0)
        points = int(photogrammetry.get("sparse_points") or 0)
        dense = photogrammetry.get("dense") or {}
        limitations: List[str] = []
        if status == "reconstructed":
            dense_note = (
                "Dense multi-view stereo was not available, so surfaces are plan "
                "geometry with photo textures rather than a dense image-derived mesh."
                if dense.get("status") != "completed"
                else "Dense multi-view stereo output exists but the walkthrough still "
                "uses the validated plan geometry."
            )
            limitations.append(
                f"COLMAP estimated camera poses for {registered} of {inputs} photographs "
                f"({points} sparse points). {dense_note}"
            )
        elif status == "partial":
            limitations.append(
                f"COLMAP registered only {registered} of {inputs} photographs; camera "
                "coverage is partial, so image-derived geometry was not used."
            )
        elif status == "insufficient_overlap":
            limitations.append(
                "The uploaded photographs did not overlap enough for COLMAP to estimate "
                "camera poses. Layout comes from the floor plan; appearance uses the "
                "photo texture crops only."
            )
        elif status in {"unavailable", "failed"}:
            limitation = (
                "Photogrammetry could not run in this environment; layout comes from the "
                "floor plan and appearance uses the photo texture crops only."
            )
            errors = photogrammetry.get("errors") or []
            if errors:
                limitation += f" Engine message: {errors[0]}"
            limitations.append(limitation)
        if textures_applied:
            limitations.append(
                "Wall textures are real crops from photographs the user assigned to each "
                "room. Ceilings and rooms without a confirmed photograph use neutral materials."
            )
        else:
            limitations.append(
                "No confirmed photo texture crops could be applied; surfaces use neutral "
                "materials with colours sampled from the uploaded photographs."
            )
        limitations.append(
            "Photographs were assigned to rooms through classification plus explicit user "
            "confirmation; the pipeline never assigns photos across rooms by upload order."
        )
        return limitations

    def _texture_summary(
        self,
        texture_sets: List[Dict],
        room_textures: Dict,
        room_metadata: Dict[int, Dict],
        use_photo_floor_textures: bool,
    ) -> Dict:
        grouped: Dict[int, List[Dict]] = {}
        for texture in texture_sets:
            grouped.setdefault(int(texture["room_id"]), []).append(texture)
        return {
            "applied": bool(texture_sets),
            "count": len(texture_sets),
            "tile_m": TEXTURE_TILE_M,
            "floor_textures_used": bool(use_photo_floor_textures),
            "sources": [
                {
                    "photo_id": texture["photo_id"],
                    "file": texture["source_file"],
                    "room_id": texture["room_id"],
                    "predicted_room_type": texture["predicted_room_type"],
                    "wall_texture": texture["wall_texture"],
                    "floor_texture": texture["floor_texture"],
                }
                for texture in texture_sets
            ],
            "room_map": {
                str(room_id): {
                    "name": (room_metadata.get(room_id) or {}).get("name"),
                    "room_type": (room_metadata.get(room_id) or {}).get("room_type"),
                    "primary_photo_id": (room_textures.get(room_id) or {}).get("photo_id"),
                    "photo_ids": [texture["photo_id"] for texture in textures],
                    "files": [texture["source_file"] for texture in textures],
                }
                for room_id, textures in sorted(grouped.items())
            },
            "assignment": (
                "Room textures come only from photographs the user explicitly confirmed "
                "for that room. Unassigned rooms keep neutral materials."
            ),
        }

    def _copy_photogrammetry_assets(
        self, job_id: str, photogrammetry: Dict, output_dir: Path
    ) -> List[str]:
        if not photogrammetry:
            return []
        workspace = Path(__file__).parent / "geometry" / job_id / "photogrammetry"
        candidates = [
            "photogrammetry.json",
            "cameras.json",
            "sparse_points.ply",
            "dense_points.ply",
            "dense_mesh.ply",
        ]
        copied = []
        for name in candidates:
            source = workspace / name
            if not source.exists():
                continue
            shutil.copyfile(source, output_dir / name)
            copied.append(name)
        return copied

    # ------------------------------------------------------------------
    # Mesh construction
    # ------------------------------------------------------------------
    def _build_meshes(
        self,
        rooms: List[Dict],
        walls: List[Dict],
        doorways: List[Dict],
        wall_color,
        floor_color,
        ceiling_color,
        room_textures: Optional[Dict] = None,
        windows: Optional[List[Dict]] = None,
        use_photo_floor_textures: bool = False,
    ) -> Dict:
        room_textures = room_textures or {}
        windows = windows or []
        floor_meshes: List[trimesh.Trimesh] = []
        ceiling_meshes: List[trimesh.Trimesh] = []
        wall_boxes: List[trimesh.Trimesh] = []
        threshold_boxes: List[trimesh.Trimesh] = []
        glass_boxes: List[trimesh.Trimesh] = []
        textured_floors: Dict[int, Dict] = {}
        textured_walls: Dict[int, Dict] = {}
        room_polygons: List[Tuple[int, Polygon]] = []
        textured_wall_boxes = 0
        rendered_windows = 0

        for room in rooms:
            polygon = _polygon_from_room(room)
            if polygon is None:
                continue
            try:
                floor_meshes.append(_extrude_flat(polygon, -FLOOR_THICKNESS, FLOOR_THICKNESS))
                ceiling_meshes.append(
                    _extrude_flat(polygon, WALL_HEIGHT - CEILING_THICKNESS, CEILING_THICKNESS)
                )
            except Exception:
                # Degenerate contours can defeat triangulation; fall back to the room bounds.
                x, z, w, d = room["bounds_m"]
                fallback = Polygon([[x, z], [x + w, z], [x + w, z + d], [x, z + d]])
                floor_meshes.append(_extrude_flat(fallback, -FLOOR_THICKNESS, FLOOR_THICKNESS))
                ceiling_meshes.append(
                    _extrude_flat(fallback, WALL_HEIGHT - CEILING_THICKNESS, CEILING_THICKNESS)
                )
                polygon = fallback
            room_polygons.append((int(room["id"]), polygon))
            texture = room_textures.get(room.get("id"))
            if texture and use_photo_floor_textures and texture.get("floor_image") is not None:
                try:
                    plane = _textured_polygon(
                        polygon, 0.002, texture["floor_image"], TEXTURE_TILE_M
                    )
                    group = textured_floors.setdefault(
                        texture["index"],
                        {"image": texture["floor_image"], "meshes": []},
                    )
                    group["meshes"].append(plane)
                except Exception:
                    pass

        doors_by_orientation = {
            "horizontal": [d for d in doorways if d.get("orientation") == "horizontal"],
            "vertical": [d for d in doorways if d.get("orientation") == "vertical"],
        }
        windows_by_orientation = {
            "horizontal": [w for w in windows if w.get("orientation") == "horizontal"],
            "vertical": [w for w in windows if w.get("orientation") == "vertical"],
        }
        thickness_by_orientation = {"horizontal": 0.12, "vertical": 0.12}
        for orientation in thickness_by_orientation:
            values = [
                float(w.get("thickness_m", 0.12))
                for w in walls
                if w.get("orientation") == orientation
            ]
            if values:
                thickness_by_orientation[orientation] = float(np.median(values))
        door_geometry_widths: Dict[int, float] = {}
        for door in doorways:
            width = float(door.get("width_m", 0.9))
            door_geometry_widths[door["id"]] = min(
                MAX_PASSABLE_DOOR_M, max(MIN_PASSABLE_DOOR_M, width)
            )

        wall_index = 0
        for orientation in ("horizontal", "vertical"):
            run_axis = 0 if orientation == "horizontal" else 1
            line_axis = 1 if orientation == "horizontal" else 0
            for wall in [w for w in walls if w.get("orientation") == orientation]:
                start = wall["start_m"]
                end = wall["end_m"]
                run_start = min(start[run_axis], end[run_axis])
                run_end = max(start[run_axis], end[run_axis])
                line_pos = start[line_axis]
                thickness = float(wall.get("thickness_m", 0.12))
                segments = [(run_start, run_end)]
                for door in doors_by_orientation[orientation]:
                    door_pos = door["center_m"][line_axis]
                    if abs(door_pos - line_pos) > thickness + 0.2:
                        continue
                    door_run = door["center_m"][run_axis]
                    half = door_geometry_widths[door["id"]] / 2
                    if not (run_start - half <= door_run <= run_end + half):
                        continue
                    segments = self._subtract_interval(segments, door_run - half, door_run + half)
                for window in windows_by_orientation[orientation]:
                    window_pos = window["center_m"][line_axis]
                    if abs(window_pos - line_pos) > thickness + 0.25:
                        continue
                    window_run = window["center_m"][run_axis]
                    half = float(window.get("width_m", 1.0)) / 2
                    if half < 0.1:
                        continue
                    if not (run_start - half <= window_run <= run_end + half):
                        continue
                    segments = self._subtract_interval(segments, window_run - half, window_run + half)
                for seg_start, seg_end in segments:
                    length = seg_end - seg_start
                    if length < MIN_WALL_LENGTH_M:
                        continue
                    center_run = (seg_start + seg_end) / 2
                    extents = (
                        (length, WALL_HEIGHT, thickness)
                        if orientation == "horizontal"
                        else (thickness, WALL_HEIGHT, length)
                    )
                    center = (
                        (center_run, WALL_HEIGHT / 2, line_pos)
                        if orientation == "horizontal"
                        else (line_pos, WALL_HEIGHT / 2, center_run)
                    )
                    wall_boxes.append(_box(extents, center))
                    surface_x = center_run if orientation == "horizontal" else line_pos
                    surface_z = line_pos if orientation == "horizontal" else center_run
                    for sign, texture in self._adjacent_room_textures(
                        surface_x,
                        surface_z,
                        orientation,
                        thickness,
                        room_polygons,
                        room_textures,
                    ):
                        skin_center = list(center)
                        if orientation == "horizontal":
                            skin_center[2] = line_pos + sign * (thickness / 2 + 0.008)
                            skin_extents = (length, WALL_HEIGHT, 0.012)
                        else:
                            skin_center[0] = line_pos + sign * (thickness / 2 + 0.008)
                            skin_extents = (0.012, WALL_HEIGHT, length)
                        box = _textured_box(
                            skin_extents,
                            tuple(skin_center),
                            texture["wall_image"],
                            TEXTURE_TILE_M,
                            offset=((wall_index * 0.37) % 1.0, 0.0),
                        )
                        group = textured_walls.setdefault(
                            texture["index"],
                            {"image": texture["wall_image"], "meshes": []},
                        )
                        group["meshes"].append(box)
                        textured_wall_boxes += 1
                wall_index += 1

        for window in windows:
            orientation = window.get("orientation")
            if orientation not in ("horizontal", "vertical"):
                continue
            run_axis = 0 if orientation == "horizontal" else 1
            line_axis = 1 if orientation == "horizontal" else 0
            window_run, line_pos = window["center_m"][run_axis], window["center_m"][line_axis]
            width = max(0.2, float(window.get("width_m", 1.0)))
            half = width / 2
            matching_walls = [
                wall
                for wall in walls
                if wall.get("orientation") == orientation
                and abs(wall["start_m"][line_axis] - line_pos)
                <= float(wall.get("thickness_m", 0.12)) + 0.3
            ]
            if not matching_walls:
                continue
            extents_run = [
                (
                    min(wall["start_m"][run_axis], wall["end_m"][run_axis]),
                    max(wall["start_m"][run_axis], wall["end_m"][run_axis]),
                )
                for wall in matching_walls
            ]
            if window_run - half > max(end for _, end in extents_run) + 0.2:
                continue
            if window_run + half < min(start for start, _ in extents_run) - 0.2:
                continue
            thickness = float(
                np.median([float(wall.get("thickness_m", 0.12)) for wall in matching_walls])
            )
            sill = float(window.get("sill_m", WINDOW_SILL_HEIGHT))
            head = float(window.get("head_m", WINDOW_HEAD_HEIGHT))
            sill = min(max(sill, 0.1), WALL_HEIGHT - 1.0)
            head = min(max(head, sill + 0.3), WALL_HEIGHT - 0.05)
            pane_height = head - sill
            lower_height = sill
            upper_height = WALL_HEIGHT - head
            adjacent_textures = self._adjacent_room_textures(
                window_run, line_pos, orientation, thickness, room_polygons, room_textures
            )
            panes = []
            if lower_height > MIN_WALL_LENGTH_M:
                panes.append(
                    (
                        (
                            (width, lower_height, thickness)
                            if orientation == "horizontal"
                            else (thickness, lower_height, width)
                        ),
                        (
                            (window_run, lower_height / 2, line_pos)
                            if orientation == "horizontal"
                            else (line_pos, lower_height / 2, window_run)
                        ),
                    )
                )
            if upper_height > MIN_WALL_LENGTH_M:
                panes.append(
                    (
                        (
                            (width, upper_height, thickness)
                            if orientation == "horizontal"
                            else (thickness, upper_height, width)
                        ),
                        (
                            (window_run, head + upper_height / 2, line_pos)
                            if orientation == "horizontal"
                            else (line_pos, head + upper_height / 2, window_run)
                        ),
                    )
                )
            for pane_extents, pane_center in panes:
                wall_boxes.append(_box(pane_extents, pane_center))
                for sign, texture in adjacent_textures:
                    skin_center = list(pane_center)
                    if orientation == "horizontal":
                        skin_center[2] = line_pos + sign * (thickness / 2 + 0.008)
                        skin_extents = (width, pane_extents[1], 0.012)
                    else:
                        skin_center[0] = line_pos + sign * (thickness / 2 + 0.008)
                        skin_extents = (0.012, pane_extents[1], width)
                    box = _textured_box(
                        skin_extents,
                        tuple(skin_center),
                        texture["wall_image"],
                        TEXTURE_TILE_M,
                        offset=((rendered_windows * 0.37) % 1.0, 0.0),
                    )
                    group = textured_walls.setdefault(
                        texture["index"],
                        {"image": texture["wall_image"], "meshes": []},
                    )
                    group["meshes"].append(box)
                    textured_wall_boxes += 1
            pane_thickness = max(0.02, min(0.06, thickness * 0.4))
            glass_extents = (
                (width, pane_height, pane_thickness)
                if orientation == "horizontal"
                else (pane_thickness, pane_height, width)
            )
            glass_center = (
                (window_run, (sill + head) / 2, line_pos)
                if orientation == "horizontal"
                else (line_pos, (sill + head) / 2, window_run)
            )
            glass_boxes.append(_box(glass_extents, glass_center))
            rendered_windows += 1

        for door in doorways:
            orientation = door.get("orientation")
            if orientation not in ("horizontal", "vertical"):
                continue
            run_axis = 0 if orientation == "horizontal" else 1
            line_axis = 1 if orientation == "horizontal" else 0
            width = door_geometry_widths[door["id"]]
            center_run = door["center_m"][run_axis]
            line_pos = door["center_m"][line_axis]
            thickness = thickness_by_orientation[orientation]
            header_height = WALL_HEIGHT - DOOR_HEIGHT
            adjacent_textures = self._adjacent_room_textures(
                center_run, line_pos, orientation, thickness, room_polygons, room_textures
            )
            if orientation == "horizontal":
                header_extents = (width, header_height, thickness)
                header_center = (center_run, DOOR_HEIGHT + header_height / 2, line_pos)
                threshold = _box(
                    (width, 0.07, thickness + 0.12),
                    (center_run, -0.033, line_pos),
                )
            else:
                header_extents = (thickness, header_height, width)
                header_center = (line_pos, DOOR_HEIGHT + header_height / 2, center_run)
                threshold = _box(
                    (thickness + 0.12, 0.07, width),
                    (line_pos, -0.033, center_run),
                )
            wall_boxes.append(_box(header_extents, header_center))
            for sign, texture in adjacent_textures:
                skin_center = list(header_center)
                if orientation == "horizontal":
                    skin_center[2] = line_pos + sign * (thickness / 2 + 0.008)
                    skin_extents = (width, header_height, 0.012)
                else:
                    skin_center[0] = line_pos + sign * (thickness / 2 + 0.008)
                    skin_extents = (0.012, header_height, width)
                header = _textured_box(
                    skin_extents,
                    tuple(skin_center),
                    texture["wall_image"],
                    TEXTURE_TILE_M,
                )
                group = textured_walls.setdefault(
                    texture["index"],
                    {"image": texture["wall_image"], "meshes": []},
                )
                group["meshes"].append(header)
                textured_wall_boxes += 1
            threshold_boxes.append(threshold)

        if not floor_meshes:
            raise GeometryBuildError("Could not build any floor geometry from the detected rooms")
        if not wall_boxes and not textured_walls:
            raise GeometryBuildError("Could not build any wall geometry")

        scene = trimesh.Scene()
        floors = self._merge_with_color(floor_meshes + threshold_boxes, floor_color)
        scene.add_geometry(floors, node_name="Floors", geom_name="Floors")
        ceilings = self._merge_with_color(ceiling_meshes, ceiling_color)
        scene.add_geometry(ceilings, node_name="Ceilings", geom_name="Ceilings")
        if wall_boxes:
            walls_mesh = self._merge_with_color(wall_boxes, wall_color)
            scene.add_geometry(walls_mesh, node_name="Walls", geom_name="Walls")
        if glass_boxes:
            windows_mesh = self._merge_with_color(glass_boxes, GLASS_COLOR)
            scene.add_geometry(windows_mesh, node_name="Windows", geom_name="Windows")
        for index, group in textured_walls.items():
            walls_mesh = _merge_textured(group["meshes"], group["image"])
            name = f"Walls_photo_{index:02d}"
            scene.add_geometry(walls_mesh, node_name=name, geom_name=name)
        for index, group in textured_floors.items():
            floor_mesh = _merge_textured(group["meshes"], group["image"])
            name = f"Floors_photo_{index:02d}"
            scene.add_geometry(floor_mesh, node_name=name, geom_name=name)

        vertex_count = sum(len(mesh.vertices) for mesh in scene.geometry.values())
        face_count = sum(len(mesh.faces) for mesh in scene.geometry.values())
        bounds = scene.bounds
        return {
            "scene": scene,
            "vertex_count": int(vertex_count),
            "face_count": int(face_count),
            "summary": {
                "wall_boxes": len(wall_boxes) + textured_wall_boxes,
                "textured_wall_boxes": textured_wall_boxes,
                "textured_floor_rooms": sum(
                    len(group["meshes"]) for group in textured_floors.values()
                ),
                "floors": len(floor_meshes),
                "ceilings": len(ceiling_meshes),
                "windows_rendered": rendered_windows,
                "windows_requested": len(windows),
                "glass_panes": len(glass_boxes),
                "bounds": bounds.tolist() if bounds is not None else None,
            },
        }

    def _adjacent_room_textures(
        self,
        point_x: float,
        point_z: float,
        orientation: str,
        thickness: float,
        room_polygons: List[Tuple[int, Polygon]],
        room_textures: Dict,
    ) -> List[Tuple[int, Dict]]:
        """Return textures of the rooms physically adjacent to a wall surface."""
        if not room_textures or not room_polygons:
            return []
        offset = thickness / 2 + 0.08
        adjacent: List[Tuple[int, Dict]] = []
        for sign in (-1, 1):
            if orientation == "horizontal":
                probe = Point(point_x, point_z + sign * offset)
            else:
                probe = Point(point_x + sign * offset, point_z)
            for room_id, polygon in room_polygons:
                texture = room_textures.get(room_id)
                if texture and polygon.buffer(0.03).contains(probe):
                    adjacent.append((sign, texture))
                    break
        return adjacent

    def _merge_with_color(
        self, meshes: List[trimesh.Trimesh], rgb: Tuple[int, int, int]
    ) -> trimesh.Trimesh:
        merged = trimesh.util.concatenate(meshes)
        merged.visual.face_colors = _rgba(rgb)
        return merged

    def _subtract_interval(
        self, segments: List[Tuple[float, float]], cut_start: float, cut_end: float
    ) -> List[Tuple[float, float]]:
        result: List[Tuple[float, float]] = []
        for start, end in segments:
            if cut_end <= start or cut_start >= end:
                result.append((start, end))
                continue
            if cut_start > start:
                result.append((start, cut_start))
            if cut_end < end:
                result.append((cut_end, end))
        return result

    # ------------------------------------------------------------------
    # Collision and graph
    # ------------------------------------------------------------------
    def _build_collision(
        self,
        rooms: List[Dict],
        walls: List[Dict],
        doorways: List[Dict],
        floor_plan_result: Dict,
        windows: Optional[List[Dict]] = None,
    ) -> Dict:
        windows = windows or []
        thickness_scale = 1.0
        doors_by_orientation = {"horizontal": [], "vertical": []}
        for door in doorways:
            orientation = door.get("orientation")
            if orientation in doors_by_orientation:
                width = float(door.get("width_m", 0.9))
                doors_by_orientation[orientation].append(
                    {
                        "id": door["id"],
                        "position": door["center_m"],
                        "orientation": orientation,
                        "width_m": round(min(MAX_PASSABLE_DOOR_M, max(MIN_PASSABLE_DOOR_M, width)), 2),
                        "detected_width_m": round(width, 2),
                        "rooms": door.get("rooms", [None, None]),
                        "exterior": door.get("exterior", False),
                    }
                )

        collision_walls = []
        for wall in walls:
            orientation = wall.get("orientation")
            run_axis = 0 if orientation == "horizontal" else 1
            line_axis = 1 if orientation == "horizontal" else 0
            run_start = min(wall["start_m"][run_axis], wall["end_m"][run_axis])
            run_end = max(wall["start_m"][run_axis], wall["end_m"][run_axis])
            line_pos = wall["start_m"][line_axis]
            thickness = float(wall.get("thickness_m", 0.12)) * thickness_scale
            segments = [(run_start, run_end)]
            for door in doors_by_orientation.get(orientation, []):
                if abs(door["position"][line_axis] - line_pos) > thickness + 0.2:
                    continue
                door_run = door["position"][run_axis]
                segments = self._subtract_interval(
                    segments, door_run - door["width_m"] / 2, door_run + door["width_m"] / 2
                )
            for seg_start, seg_end in segments:
                if seg_end - seg_start < MIN_WALL_LENGTH_M:
                    continue
                if orientation == "horizontal":
                    box = {
                        "min": [seg_start, line_pos - thickness / 2],
                        "max": [seg_end, line_pos + thickness / 2],
                    }
                else:
                    box = {
                        "min": [line_pos - thickness / 2, seg_start],
                        "max": [line_pos + thickness / 2, seg_end],
                    }
                box["exterior"] = bool(wall.get("exterior", False))
                collision_walls.append(box)

        for window in windows:
            orientation = window.get("orientation")
            if orientation not in ("horizontal", "vertical"):
                continue
            run_axis = 0 if orientation == "horizontal" else 1
            line_axis = 1 if orientation == "horizontal" else 0
            run_center, line_pos = (
                window["center_m"][run_axis],
                window["center_m"][line_axis],
            )
            width = float(window.get("width_m", 1.0))
            matching = [
                float(wall.get("thickness_m", 0.12))
                for wall in walls
                if wall.get("orientation") == orientation
                and abs(wall["start_m"][line_axis] - line_pos)
                <= float(wall.get("thickness_m", 0.12)) + 0.3
            ]
            thickness = float(np.median(matching)) if matching else 0.12
            if orientation == "horizontal":
                box = {
                    "min": [run_center - width / 2, line_pos - thickness / 2],
                    "max": [run_center + width / 2, line_pos + thickness / 2],
                }
            else:
                box = {
                    "min": [line_pos - thickness / 2, run_center - width / 2],
                    "max": [line_pos + thickness / 2, run_center + width / 2],
                }
            box["exterior"] = True
            box["window"] = True
            collision_walls.append(box)

        spawn_room = max(rooms, key=lambda r: r.get("area_m2", 0))
        spawn_x, spawn_z = spawn_room.get("spawn_m") or spawn_room.get("center_m", [1.0, 1.0])
        yaw = 0.0
        room_doors = [
            d
            for orientation in doors_by_orientation
            for d in doors_by_orientation[orientation]
            if spawn_room["id"] in (d["rooms"][0], d["rooms"][1])
        ]
        if room_doors:
            door_x, door_z = room_doors[0]["position"]
            yaw = float(np.arctan2(-(door_x - spawn_x), -(door_z - spawn_z)))
        if self._point_hits_wall(spawn_x, spawn_z, collision_walls):
            raise GeometryBuildError("Spawn point intersects a wall; reconstruction aborted")
        return {
            "units": "meters",
            "walls": collision_walls,
            "doors": doors_by_orientation["horizontal"] + doors_by_orientation["vertical"],
            "rooms": [
                {
                    "id": room["id"],
                    "bounds": room["bounds_m"],
                    "center": room["center_m"],
                    "area_m2": room.get("area_m2"),
                }
                for room in rooms
            ],
            "spawn": {"position": [round(spawn_x, 3), 0.0, round(spawn_z, 3)], "yaw": round(yaw, 4)},
            "footprint": floor_plan_result.get("footprint_m"),
            "wall_height": WALL_HEIGHT,
            "door_height": DOOR_HEIGHT,
        }

    def _point_hits_wall(self, x: float, z: float, walls: List[Dict], margin: float = 0.3) -> bool:
        for wall in walls:
            if (
                wall["min"][0] - margin <= x <= wall["max"][0] + margin
                and wall["min"][1] - margin <= z <= wall["max"][1] + margin
            ):
                return True
        return False

    def _apply_collision_metadata(
        self, collision: Dict, room_metadata: Dict[int, Dict]
    ) -> Dict:
        for room in collision.get("rooms", []):
            metadata = room_metadata.get(int(room["id"])) or {}
            room["name"] = metadata.get("name") or f"Room {int(room['id']) + 1}"
            room["room_type"] = metadata.get("room_type")
            room["photo_count"] = metadata.get("photo_count", 0)
        return collision

    def _build_room_graph(
        self,
        rooms: List[Dict],
        doorways: List[Dict],
        spawn: Dict,
        room_metadata: Optional[Dict[int, Dict]] = None,
        job_id: Optional[str] = None,
    ) -> Dict:
        room_metadata = room_metadata or {}
        nodes = []
        for room in rooms:
            room_id = int(room["id"])
            metadata = room_metadata.get(room_id) or {}
            photo_ids = metadata.get("photo_ids") or []
            nodes.append(
                {
                    "id": room_id,
                    "name": metadata.get("name") or f"Room {room_id + 1}",
                    "room_type": metadata.get("room_type"),
                    "center": room["center_m"],
                    "bounds": room["bounds_m"],
                    "area_m2": room.get("area_m2"),
                    "polygon": room.get("polygon_m", []),
                    "photo_ids": photo_ids,
                    "photo_count": len(photo_ids),
                    "photos": [
                        {
                            "photo_id": photo_id,
                            "thumbnail_url": (
                                f"/reconstructions/{job_id}/photos/{photo_id}/thumbnail"
                                if job_id
                                else None
                            ),
                        }
                        for photo_id in photo_ids
                    ],
                }
            )
        edges = []
        adjacency = {room["id"]: set() for room in rooms}
        for door in doorways:
            a, b = door.get("rooms", [None, None])
            if a is None or b is None:
                continue
            edges.append(
                {
                    "door_id": door["id"],
                    "from": a,
                    "to": b,
                    "position": door["center_m"],
                    "width_m": round(float(door.get("width_m", 0.9)), 2),
                }
            )
            adjacency[a].add(b)
            adjacency[b].add(a)

        spawn_room = max(rooms, key=lambda r: r.get("area_m2", 0))
        visited = {spawn_room["id"]}
        frontier = [spawn_room["id"]]
        while frontier:
            current = frontier.pop()
            for neighbour in adjacency[current]:
                if neighbour not in visited:
                    visited.add(neighbour)
                    frontier.append(neighbour)
        connected = len(visited) == len(rooms)
        return {
            "nodes": nodes,
            "edges": edges,
            "spawn_room": spawn_room["id"],
            "reachable_rooms": sorted(visited),
            "connected": connected,
            "navigation_available": connected and len(rooms) > 0,
        }

    # ------------------------------------------------------------------
    # Validation and export
    # ------------------------------------------------------------------
    def _check_geometry(self, geometry: Dict, collision: Dict, room_graph: Dict):
        scene = geometry["scene"]
        if not scene.geometry:
            raise GeometryBuildError("Generated scene contains no geometry")
        if geometry["face_count"] <= 0:
            raise GeometryBuildError("Generated scene contains no faces")
        if not collision["walls"]:
            raise GeometryBuildError("Generated scene contains no collision walls")
        bounds = scene.bounds
        if bounds is None or not np.isfinite(bounds).all():
            raise GeometryBuildError("Generated scene has invalid bounds")
        span = bounds[1] - bounds[0]
        if span[0] <= 0.5 or span[2] <= 0.5 or span[1] <= 1.0:
            raise GeometryBuildError("Generated scene dimensions are implausible")

    def _export_glb(self, path: Path, geometry: Dict):
        try:
            geometry["scene"].export(str(path), file_type="glb")
        except Exception as exc:
            raise GeometryBuildError(f"GLB export failed: {exc}") from exc
        if not path.exists() or path.stat().st_size < 1000:
            raise GeometryBuildError("GLB export produced an empty or truncated file")
