"""
Photogrammetry stage.

Runs real structure-from-motion over the photographs uploaded for one job using
COLMAP through pycolmap. The stage produces camera poses, a sparse point cloud,
match statistics, and an honest coverage assessment. Dense multi-view stereo is
attempted only when the installed COLMAP build has CUDA support; otherwise the
stage reports that dense reconstruction was not run.

Uploaded images are the only inputs to this module. No demo or cached model is
read, and a failed/insufficient reconstruction is reported as such.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image, ImageOps

ENGINE_NAME = "COLMAP"
MAX_SFM_IMAGES = 40
MAX_IMAGE_DIMENSION = 1600
MIN_REGISTERED_IMAGES = 2
MIN_SPARSE_POINTS = 100
MIN_REGISTRATION_RATIO = 0.5
DUPLICATE_HAMMING_DISTANCE = 2
WORKER_TIMEOUT_SECONDS = 2400


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _difference_hash(gray: np.ndarray) -> int:
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = small[:, 1:] > small[:, :-1]
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bit)
    return value


def _hamming(a: int, b: int) -> int:
    return int(a ^ b).bit_count()


def _prepare_image(source: Path, target: Path) -> Tuple[int, int]:
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        width, height = image.size
        scale = min(1.0, MAX_IMAGE_DIMENSION / max(1, width, height))
        if scale < 1.0:
            image = image.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                Image.LANCZOS,
            )
        image.save(target, "JPEG", quality=92, optimize=True)
        return image.size


def _pose_payload(image) -> Dict:
    pose = image.cam_from_world()
    quaternion = pose.rotation.quat
    translation = pose.translation
    center = image.projection_center()
    direction = image.viewing_direction()
    matrix = image.camera.calibration_matrix()
    return {
        "file": image.name,
        "camera_id": int(image.camera_id),
        "camera_model": image.camera.model_name,
        "width": int(image.camera.width),
        "height": int(image.camera.height),
        "intrinsics": [[float(value) for value in row] for row in matrix],
        "rotation_quaternion_xyzw": [float(value) for value in quaternion],
        "translation": [float(value) for value in translation],
        "center": [round(float(value), 6) for value in center],
        "viewing_direction": [round(float(value), 6) for value in direction],
        "num_points3d": int(image.num_points3D),
    }


def _match_count(database_path: Path) -> Dict:
    if not database_path.exists():
        return {"verified_pairs": 0, "inlier_matches": 0}
    try:
        with sqlite3.connect(database_path) as connection:
            row = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(rows), 0) FROM two_view_geometries "
                "WHERE rows > 0"
            ).fetchone()
        return {"verified_pairs": int(row[0]), "inlier_matches": int(row[1])}
    except sqlite3.Error:
        return {"verified_pairs": 0, "inlier_matches": 0}


class PhotogrammetryEngine:
    """COLMAP SfM over the current job's uploaded photographs."""

    engine_name = ENGINE_NAME

    def __init__(self, workspace_root: Optional[Path] = None):
        self.workspace_root = workspace_root or (Path(__file__).parent / "geometry")

    async def reconstruct(
        self, job_id: str, photo_paths: List[str], floor_plan_result: Optional[Dict] = None
    ) -> Dict:
        return await asyncio.to_thread(
            self._reconstruct_isolated, job_id, photo_paths, floor_plan_result or {}
        )

    def _reconstruct_isolated(
        self, job_id: str, photo_paths: List[str], floor_plan_result: Dict
    ) -> Dict:
        """Run COLMAP in a child process.

        torch (used by the room classifier) and pycolmap each ship their own
        ``libomp.dylib``. Initializing both OpenMP runtimes in one process aborts
        with "OMP: Error #15", so pycolmap is never imported in the API process.
        Process isolation also keeps a COLMAP crash from taking down the service.
        """
        workspace = self.workspace_root / job_id / "photogrammetry"
        workspace.mkdir(parents=True, exist_ok=True)
        spec_path = workspace / "photogrammetry_job.json"
        spec_path.write_text(
            json.dumps(
                {
                    "job_id": job_id,
                    "photo_paths": [str(path) for path in photo_paths],
                    "floor_plan_result": floor_plan_result,
                    "workspace_root": str(self.workspace_root),
                },
                default=str,
            )
        )
        result_path = workspace / "photogrammetry.json"
        if result_path.exists():
            result_path.unlink()
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            str(spec_path),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=str(Path(__file__).resolve().parent),
                capture_output=True,
                text=True,
                timeout=WORKER_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            result = self._worker_failure(
                job_id,
                f"COLMAP did not finish within {WORKER_TIMEOUT_SECONDS} seconds.",
            )
            self._save(workspace, result)
            return result
        if result_path.is_file():
            return json.loads(result_path.read_text())
        detail = (completed.stderr or completed.stdout or "").strip()
        result = self._worker_failure(
            job_id,
            "The COLMAP worker exited without producing a result"
            + (f": {detail[-800:]}" if detail else "."),
        )
        self._save(workspace, result)
        return result

    def _worker_failure(self, job_id: str, message: str) -> Dict:
        return {
            "status": "failed",
            "engine": {"name": ENGINE_NAME, "version": None, "binding": "pycolmap"},
            "input_images": 0,
            "stages": {},
            "registered_images": 0,
            "sparse_points": 0,
            "registration_ratio": 0.0,
            "images": [],
            "cameras": [],
            "dense": {"status": "not_attempted", "reason": message},
            "alignment": {},
            "warnings": [],
            "errors": [message],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": 0.0,
        }

    def _reconstruct_sync(
        self, job_id: str, photo_paths: List[str], floor_plan_result: Dict
    ) -> Dict:
        started = time.time()
        workspace = self.workspace_root / job_id / "photogrammetry"
        images_dir = workspace / "images"
        sparse_dir = workspace / "sparse"
        dense_dir = workspace / "dense"
        for directory in (images_dir, sparse_dir):
            directory.mkdir(parents=True, exist_ok=True)

        result: Dict = {
            "status": "not_run",
            "engine": {"name": ENGINE_NAME, "version": None, "binding": "pycolmap"},
            "input_images": len(photo_paths),
            "stages": {
                "preprocess": False,
                "feature_extraction": False,
                "feature_matching": False,
                "mapping": False,
                "dense_stereo": False,
                "fusion": False,
            },
            "registered_images": 0,
            "sparse_points": 0,
            "registration_ratio": 0.0,
            "mean_reprojection_error_px": None,
            "mean_track_length": None,
            "matched_pairs": 0,
            "inlier_matches": 0,
            "images": [],
            "cameras": [],
            "sparse_point_cloud": None,
            "dense_point_cloud": None,
            "dense_mesh": None,
            "dense": {
                "status": "not_attempted",
                "reason": "Dense multi-view stereo is attempted only after a valid sparse reconstruction.",
            },
            "alignment": {},
            "warnings": [],
            "errors": [],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": 0.0,
        }

        try:
            import pycolmap
        except ImportError as exc:
            result["status"] = "unavailable"
            result["errors"].append(f"pycolmap is not installed: {exc}")
            result["duration_seconds"] = round(time.time() - started, 2)
            self._save(workspace, result)
            return result

        result["engine"]["version"] = getattr(pycolmap, "__version__", "unknown")
        result["engine"]["cuda_available"] = bool(getattr(pycolmap, "has_cuda", False))

        try:
            prepared, duplicates, rejected = self._prepare_photos(photo_paths, images_dir)
        except Exception as exc:  # noqa: BLE001 - report the real cause
            result["status"] = "failed"
            result["errors"].append(f"Photo preprocessing failed: {exc}")
            result["duration_seconds"] = round(time.time() - started, 2)
            self._save(workspace, result)
            return result

        result["stages"]["preprocess"] = bool(prepared)
        result["duplicate_images"] = duplicates
        result["rejected_images"] = rejected
        for item in prepared:
            item["sha256"] = _sha256(images_dir / item["sfm_file"])
        result["images"] = [
            {key: value for key, value in item.items() if key != "sfm_path"}
            for item in prepared
        ]

        if len(prepared) > MAX_SFM_IMAGES:
            step = len(prepared) / MAX_SFM_IMAGES
            prepared = [prepared[int(index * step)] for index in range(MAX_SFM_IMAGES)]
            result["warnings"].append(
                f"Only {MAX_SFM_IMAGES} of the uploaded photographs were used for "
                f"structure-from-motion to bound runtime."
            )
        if duplicates:
            result["warnings"].append(
                f"{len(duplicates)} duplicate photograph(s) were excluded from camera "
                "estimation."
            )
        if rejected:
            result["warnings"].append(
                f"{len(rejected)} photograph(s) could not be used for camera estimation."
            )
        blurry = [item for item in prepared if item["blur_score"] < 45.0]
        if blurry:
            result["warnings"].append(
                f"{len(blurry)} photograph(s) have low sharpness scores and may reduce "
                "match quality."
            )
        if len(prepared) < MIN_REGISTERED_IMAGES:
            result["status"] = "insufficient_overlap"
            result["warnings"].append(
                "At least two distinct, readable photographs are required for "
                "structure-from-motion."
            )
            result["duration_seconds"] = round(time.time() - started, 2)
            self._save(workspace, result)
            return result

        database_path = workspace / "database.db"
        if database_path.exists():
            database_path.unlink()
        image_names = [item["sfm_file"] for item in prepared]

        try:
            pycolmap.extract_features(
                database_path,
                images_dir,
                image_names=image_names,
                camera_mode=pycolmap.CameraMode.SINGLE,
                device=pycolmap.Device.cpu,
            )
            result["stages"]["feature_extraction"] = True
        except Exception as exc:  # noqa: BLE001
            result["status"] = "failed"
            result["errors"].append(f"COLMAP feature extraction failed: {exc}")
            result["duration_seconds"] = round(time.time() - started, 2)
            self._save(workspace, result)
            return result

        try:
            pycolmap.match_exhaustive(database_path, device=pycolmap.Device.cpu)
            result["stages"]["feature_matching"] = True
            match_stats = _match_count(database_path)
            result["matched_pairs"] = match_stats["verified_pairs"]
            result["inlier_matches"] = match_stats["inlier_matches"]
        except Exception as exc:  # noqa: BLE001
            result["status"] = "failed"
            result["errors"].append(f"COLMAP feature matching failed: {exc}")
            result["duration_seconds"] = round(time.time() - started, 2)
            self._save(workspace, result)
            return result

        reconstructions = {}
        try:
            reconstructions = pycolmap.incremental_mapping(
                database_path, images_dir, sparse_dir
            )
            result["stages"]["mapping"] = bool(reconstructions)
        except Exception as exc:  # noqa: BLE001
            result["status"] = "failed"
            result["errors"].append(f"COLMAP mapping failed: {exc}")
            result["duration_seconds"] = round(time.time() - started, 2)
            self._save(workspace, result)
            return result

        if not reconstructions:
            result["status"] = "insufficient_overlap"
            result["warnings"].append(
                "COLMAP could not register the uploaded photographs. The photos likely "
                "lack sufficient overlap, texture, or distinct viewpoints."
            )
            result["duration_seconds"] = round(time.time() - started, 2)
            self._save(workspace, result)
            return result

        reconstruction = max(
            reconstructions.values(), key=lambda item: item.num_reg_images()
        )
        registered = int(reconstruction.num_reg_images())
        points = int(reconstruction.num_points3D())
        ratio = registered / max(1, len(prepared))
        result["registered_images"] = registered
        result["registration_ratio"] = round(ratio, 3)
        result["sparse_points"] = points
        result["mean_reprojection_error_px"] = round(
            float(reconstruction.compute_mean_reprojection_error()), 3
        )
        result["mean_track_length"] = round(
            float(reconstruction.compute_mean_track_length()), 3
        )
        result["cameras"] = [
            _pose_payload(reconstruction.images[image_id])
            for image_id in sorted(reconstruction.reg_image_ids())
        ]
        result["models_found"] = len(reconstructions)

        sparse_model_dir = sparse_dir / "0"
        ply_path = workspace / "sparse_points.ply"
        try:
            self._write_sparse_ply(reconstruction, ply_path)
            result["sparse_point_cloud"] = str(ply_path.relative_to(workspace))
        except Exception as exc:  # noqa: BLE001
            result["warnings"].append(f"Sparse point cloud export failed: {exc}")

        result["alignment"] = self._alignment_metrics(
            reconstruction, floor_plan_result, result["cameras"]
        )

        if registered < MIN_REGISTERED_IMAGES or points < MIN_SPARSE_POINTS:
            result["status"] = "insufficient_overlap"
            result["warnings"].append(
                f"COLMAP registered {registered} image(s) with {points} sparse point(s); "
                "this is below the threshold for reliable image-derived geometry."
            )
        elif ratio < MIN_REGISTRATION_RATIO:
            result["status"] = "partial"
            result["warnings"].append(
                f"Only {registered} of {len(prepared)} usable photographs registered in the "
                "camera reconstruction. Coverage is partial."
            )
        else:
            result["status"] = "reconstructed"

        if result["mean_reprojection_error_px"] and result["mean_reprojection_error_px"] > 2.0:
            result["warnings"].append(
                f"Mean reprojection error is {result['mean_reprojection_error_px']} px; "
                "camera calibration may be unreliable."
            )

        cuda_available = bool(result["engine"].get("cuda_available"))
        if result["status"] in {"reconstructed", "partial"} and cuda_available and sparse_model_dir.exists():
            try:
                dense_result = self._run_dense(
                    pycolmap, workspace, dense_dir, images_dir, sparse_model_dir, result
                )
                result.update(dense_result)
            except Exception as exc:  # noqa: BLE001
                result["dense"] = {"status": "failed", "reason": str(exc)}
        else:
            reason = (
                "This COLMAP build has no CUDA support; dense multi-view stereo was not run."
                if not cuda_available
                else "Dense reconstruction requires a valid sparse model."
            )
            result["dense"] = {"status": "unavailable", "reason": reason}

        result["duration_seconds"] = round(time.time() - started, 2)
        (workspace / "cameras.json").write_text(
            json.dumps(result["cameras"], indent=2)
        )
        self._save(workspace, result)
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _prepare_photos(
        self, photo_paths: List[str], images_dir: Path
    ) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        prepared: List[Dict] = []
        duplicates: List[Dict] = []
        rejected: List[Dict] = []
        seen_hashes: List[Tuple[int, str]] = []

        for index, raw_path in enumerate(photo_paths):
            source = Path(raw_path)
            try:
                gray = cv2.imread(str(source), cv2.IMREAD_GRAYSCALE)
                if gray is None:
                    raise ValueError("OpenCV could not decode the image")
                height, width = gray.shape[:2]
                if min(width, height) < 160:
                    raise ValueError(f"image too small ({width}x{height}px)")
                blur_score = float(
                    cv2.Laplacian(gray, cv2.CV_64F).var()
                )
                difference_hash = _difference_hash(gray)
                if difference_hash is None:
                    raise ValueError("could not compute a perceptual hash")
            except Exception as exc:  # noqa: BLE001
                rejected.append({"file": source.name, "error": str(exc)})
                continue

            duplicate_of = next(
                (
                    other_name
                    for other_hash, other_name in seen_hashes
                    if _hamming(other_hash, difference_hash) <= DUPLICATE_HAMMING_DISTANCE
                ),
                None,
            )
            if duplicate_of is not None:
                duplicates.append({"file": source.name, "duplicate_of": duplicate_of})
                continue
            seen_hashes.append((difference_hash, source.name))

            sfm_file = f"photo_{index:02d}_{source.stem[:40]}.jpg"
            target = images_dir / sfm_file
            try:
                width, height = _prepare_image(source, target)
            except Exception as exc:  # noqa: BLE001
                rejected.append({"file": source.name, "error": str(exc)})
                continue
            prepared.append(
                {
                    "file": source.name,
                    "sfm_file": sfm_file,
                    "sfm_path": str(target),
                    "width": int(width),
                    "height": int(height),
                    "blur_score": round(blur_score, 2),
                    "perceptual_hash": f"{difference_hash:016x}",
                }
            )
        return prepared, duplicates, rejected

    def _write_sparse_ply(self, reconstruction, path: Path):
        import trimesh

        vertices = []
        colors = []
        for point in reconstruction.points3D.values():
            vertices.append(point.xyz)
            colors.append(point.color)
        if not vertices:
            raise ValueError("COLMAP produced no 3D points")
        cloud = trimesh.points.PointCloud(
            vertices=np.asarray(vertices, dtype=np.float64),
            colors=np.asarray(colors, dtype=np.uint8),
        )
        cloud.export(str(path))

    def _alignment_metrics(
        self, reconstruction, floor_plan_result: Dict, cameras: List[Dict]
    ) -> Dict:
        try:
            up_vectors = []
            for image in reconstruction.images.values():
                if not image.has_pose:
                    continue
                rotation = np.asarray(image.cam_from_world().rotation.matrix())
                up_vectors.append(rotation.T @ np.array([0.0, -1.0, 0.0]))
            if not up_vectors:
                return {"status": "unavailable"}
            up = np.median(np.asarray(up_vectors), axis=0)
            norm = float(np.linalg.norm(up))
            if norm < 1e-6:
                return {"status": "unavailable"}
            up = up / norm
            centers = np.asarray([camera["center"] for camera in cameras])
            centered = centers - centers.mean(axis=0)
            projected = centered - np.outer(centered @ up, up)
            if len(projected) >= 2:
                _, singular_values, _ = np.linalg.svd(projected, full_matrices=False)
                spread = singular_values[:2]
                if spread[1] > 1e-9:
                    aspect = float(spread[0] / spread[1])
                else:
                    aspect = None
            else:
                aspect = None
            footprint = floor_plan_result.get("footprint_m") or {}
            plan_width = float(footprint.get("width") or 0.0)
            plan_length = float(footprint.get("length") or 0.0)
            plan_aspect = (
                max(plan_width, plan_length) / max(1e-9, min(plan_width, plan_length))
                if plan_width > 0 and plan_length > 0
                else None
            )
            aspect_delta = (
                abs(aspect - plan_aspect) / plan_aspect
                if aspect is not None and plan_aspect
                else None
            )
            return {
                "status": "estimated",
                "up_vector": [round(float(value), 4) for value in up],
                "camera_spread": [round(float(value), 4) for value in spread],
                "camera_aspect_ratio": round(aspect, 3) if aspect else None,
                "floor_plan_aspect_ratio": round(plan_aspect, 3) if plan_aspect else None,
                "aspect_delta": round(aspect_delta, 3) if aspect_delta is not None else None,
                "note": (
                    "COLMAP world scale and origin are arbitrary. This comparison checks "
                    "viewpoint-spread shape against the floor-plan footprint; it is not a "
                    "metric alignment to the plan."
                ),
            }
        except Exception as exc:  # noqa: BLE001
            return {"status": "failed", "error": str(exc)}

    def _run_dense(
        self,
        pycolmap,
        workspace: Path,
        dense_dir: Path,
        images_dir: Path,
        sparse_model_dir: Path,
        result: Dict,
    ) -> Dict:
        dense_dir.mkdir(parents=True, exist_ok=True)
        pycolmap.undistort_images(str(dense_dir), str(sparse_model_dir), str(images_dir))
        pycolmap.patch_match_stereo(str(dense_dir))
        result["stages"]["dense_stereo"] = True
        fused_path = workspace / "dense_points.ply"
        pycolmap.stereo_fusion(str(fused_path), str(dense_dir))
        result["stages"]["fusion"] = True
        dense = {
            "status": "completed",
            "point_cloud": str(fused_path.relative_to(workspace)),
            "mesh": None,
        }
        try:
            import open3d as o3d

            cloud = o3d.io.read_point_cloud(str(fused_path))
            if len(cloud.points) >= 1000:
                cloud, _ = cloud.remove_statistical_outlier(nb_neighbors=20, std_ratio=2.0)
                cloud = cloud.voxel_down_sample(voxel_size=0.02)
                cloud.estimate_normals()
                mesh, _ = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(
                    cloud, depth=8
                )
                mesh.remove_degenerate_triangles()
                mesh.remove_unreferenced_vertices()
                mesh_path = workspace / "dense_mesh.ply"
                o3d.io.write_triangle_mesh(str(mesh_path), mesh)
                dense["mesh"] = str(mesh_path.relative_to(workspace))
        except Exception as exc:  # noqa: BLE001
            dense["mesh_error"] = str(exc)
        return {"dense": dense, "dense_point_cloud": dense.get("point_cloud")}

    def _save(self, workspace: Path, result: Dict):
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "photogrammetry.json").write_text(
            json.dumps(result, indent=2, default=str)
        )


@lru_cache(maxsize=1)
def probe_pycolmap() -> Dict:
    """Report the installed COLMAP binding without importing it in-process."""
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json, pycolmap; "
                    "print(json.dumps({'version': getattr(pycolmap, '__version__', "
                    "'installed'), 'cuda': bool(getattr(pycolmap, 'has_cuda', False))}))"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"version": None, "cuda": False, "error": str(exc)}
    if completed.returncode != 0:
        return {
            "version": None,
            "cuda": False,
            "error": (completed.stderr or completed.stdout or "").strip()[-400:],
        }
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        return {"version": None, "cuda": False, "error": str(exc)}
    return {
        "version": payload.get("version"),
        "cuda": bool(payload.get("cuda")),
        "error": None,
    }


def _worker_main(argv: List[str]) -> int:
    if len(argv) != 2 or argv[0] != "--worker":
        print("usage: photogrammetry.py --worker <spec.json>", file=sys.stderr)
        return 2
    spec = json.loads(Path(argv[1]).read_text())
    engine = PhotogrammetryEngine(workspace_root=Path(spec["workspace_root"]))
    result = engine._reconstruct_sync(
        spec["job_id"],
        [str(path) for path in spec["photo_paths"]],
        spec.get("floor_plan_result") or {},
    )
    print(json.dumps({"status": result.get("status")}))
    return 0


if __name__ == "__main__":
    sys.exit(_worker_main(sys.argv[1:]))

