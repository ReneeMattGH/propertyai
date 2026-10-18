"""
Reconstruction pipeline for RealEstAI
Handles COLMAP processing and Gaussian Splatting training using nerfstudio
"""
import subprocess
import json
from pathlib import Path
from typing import Dict, Optional, List
import sys
import os


class ReconstructionPipeline:
    """Pipeline for 3D reconstruction from video frames."""
    
    def __init__(self):
        self.nerfstudio_available = self._check_nerfstudio()
        self.colmap_available = self._check_colmap()
        self.cuda_available = self._check_cuda()
    
    def _check_nerfstudio(self) -> bool:
        """Check if nerfstudio is installed."""
        try:
            import nerfstudio
            return True
        except ImportError:
            return False
    
    def _check_colmap(self) -> bool:
        """Check if COLMAP is available."""
        try:
            result = subprocess.run(["colmap", "--version"], capture_output=True, text=True)
            return result.returncode == 0
        except:
            return False
    
    def _check_cuda(self) -> bool:
        """Check if CUDA is available."""
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            return False
    
    def check_nerfstudio(self) -> bool:
        return self.nerfstudio_available
    
    def check_gpu_available(self) -> bool:
        """Check if GPU is available for reconstruction."""
        return self.cuda_available
    
    def check_cuda(self) -> bool:
        return self.cuda_available
    
    async def run_colmap(self, job_id: str, frames_info: Dict) -> Dict:
        """
        Run COLMAP for camera pose estimation and sparse reconstruction.
        
        COLMAP performs structure-from-motion to estimate camera poses
        and creates a sparse point cloud of the scene.
        
        Args:
            job_id: Job identifier
            frames_info: Frame extraction information
        
        Returns:
            Dict with COLMAP results
        """
        if not self.colmap_available:
            return {
                "status": "skipped",
                "reason": "COLMAP not installed. Please install COLMAP: https://colmap.github.io/",
                "instructions": "Mac: brew install colmap, Linux: conda install -c conda-forge colmap"
            }
        
        frames_dir = Path(frames_info["frames_dir"])
        colmap_dir = Path(__file__).parent / "colmap" / job_id
        colmap_dir.mkdir(parents=True, exist_ok=True)
        
        # Prepare COLMAP input
        images_dir = colmap_dir / "images"
        images_dir.mkdir(exist_ok=True)
        
        # Copy frames to COLMAP images directory
        import shutil
        for frame_file in frames_dir.glob("frame_*.jpg"):
            shutil.copy(frame_file, images_dir / frame_file.name)
        
        # Create COLMAP project file
        project_file = colmap_dir / "project.txt"
        project_file.write_text(f"""
# Project file for COLMAP
ImageReader.single_camera 1
ImageReader.camera_model PINHOLE
ImageReader.default_focal_length_factor 1.0
Mapper.ba_global_function_rotation False
Mapper.ba_global_function_translation False
Mapper.ba_global_function_scale False
Mapper.ba_global_function_const_pose 0
Mapper.tri_ignore_two_view_tracks True
Mapper.filter_max_reproj_error 2.0
Mapper.filter_min_tri_angle 3.0
Mapper.map_filter_max_reproj_error 2.0
Mapper.map_filter_min_tri_angle 3.0
""")
        
        try:
            # Run COLMAP feature extraction
            print("Running COLMAP feature extraction...")
            feature_extraction_cmd = [
                "colmap", "feature_extractor",
                "--database_path", str(colmap_dir / "database.db"),
                "--image_path", str(images_dir),
                "--ImageReader.single_camera", "1",
                "--ImageReader.camera_model", "PINHOLE"
            ]
            
            result = subprocess.run(
                feature_extraction_cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode != 0:
                return {
                    "status": "failed",
                    "stage": "feature_extraction",
                    "error": result.stderr
                }
            
            # Run COLMAP matcher
            print("Running COLMAP matcher...")
            matcher_cmd = [
                "colmap", "exhaustive_matcher",
                "--database_path", str(colmap_dir / "database.db"),
                "--SiftExtraction.max_num_features", "8192"
            ]
            
            result = subprocess.run(
                matcher_cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode != 0:
                return {
                    "status": "failed",
                    "stage": "matcher",
                    "error": result.stderr
                }
            
            # Run COLMAP mapper
            print("Running COLMAP mapper...")
            mapper_cmd = [
                "colmap", "mapper",
                "--database_path", str(colmap_dir / "database.db"),
                "--image_path", str(images_dir),
                "--output_path", str(colmap_dir / "sparse")
            ]
            
            result = subprocess.run(
                mapper_cmd,
                capture_output=True,
                text=True,
                timeout=600
            )
            
            if result.returncode != 0:
                return {
                    "status": "failed",
                    "stage": "mapper",
                    "error": result.stderr
                }
            
            # Run COLMAP bundle adjustment
            print("Running COLMAP bundle adjustment...")
            bundle_adjust_cmd = [
                "colmap", "bundle_adjuster",
                "--database_path", str(colmap_dir / "database.db"),
                "--image_path", str(images_dir),
                "--output_path", str(colmap_dir / "sparse"),
                "--BundleAdjustment.refine_principal_point", "1"
            ]
            
            result = subprocess.run(
                bundle_adjust_cmd,
                capture_output=True,
                text=True,
                timeout=600
            )
            
            if result.returncode != 0:
                return {
                    "status": "failed",
                    "stage": "bundle_adjustment",
                    "error": result.stderr
                }
            
            # Export COLMAP reconstruction
            print("Exporting COLMAP reconstruction...")
            export_cmd = [
                "colmap", "model_converter",
                "--input_path", str(colmap_dir / "sparse/0"),
                "--output_path", str(colmap_dir / "colmap_output"),
                "--output_type", "TXT"
            ]
            
            result = subprocess.run(
                export_cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode != 0:
                return {
                    "status": "failed",
                    "stage": "export",
                    "error": result.stderr
                }
            
            # Count registered images
            images_txt = colmap_dir / "colmap_output" / "images.txt"
            if images_txt.exists():
                with open(images_txt) as f:
                    lines = f.readlines()
                registered_images = len([l for l in lines if l.strip() and not l.startswith("#")])
            else:
                registered_images = 0
            
            # Count points
            points3d_txt = colmap_dir / "colmap_output" / "points3D.txt"
            if points3d_txt.exists():
                with open(points3d_txt) as f:
                    lines = f.readlines()
                num_points = len([l for l in lines if l.strip() and not l.startswith("#")])
            else:
                num_points = 0
            
            print(f"COLMAP completed: {registered_images} images, {num_points} points")
            
            return {
                "status": "completed",
                "registered_images": registered_images,
                "num_points": num_points,
                "colmap_dir": str(colmap_dir),
                "sparse_reconstruction": str(colmap_dir / "colmap_output")
            }
            
        except subprocess.TimeoutExpired:
            return {
                "status": "failed",
                "error": "COLMAP processing timed out",
                "stage": "timeout"
            }
        except Exception as e:
            return {
                "status": "failed",
                "error": str(e),
                "stage": "exception"
            }
    
    async def train_gaussian_splatting(self, job_id: str, colmap_result: Dict) -> Dict:
        """
        Train Gaussian Splatting model using nerfstudio.
        
        Gaussian Splatting provides efficient, high-quality view-dependent rendering
        of the captured scene.
        
        Args:
            job_id: Job identifier
            colmap_result: COLMAP processing results
        
        Returns:
            Dict with training results
        """
        if not self.nerfstudio_available:
            return {
                "status": "skipped",
                "reason": "nerfstudio not installed",
                "instructions": "Install with: pip install nerfstudio",
                "note": "Requires PyTorch and CUDA for GPU acceleration"
            }
        
        if not self.cuda_available:
            return {
                "status": "skipped",
                "reason": "CUDA not available",
                "instructions": "GPU required for Gaussian Splatting training",
                "note": "Training on CPU is extremely slow and not recommended"
            }
        
        if colmap_result.get("status") != "completed":
            return {
                "status": "skipped",
                "reason": "COLMAP processing did not complete successfully",
                "colmap_status": colmap_result.get("status")
            }
        
        colmap_dir = Path(colmap_result["colmap_dir"])
        output_dir = Path(__file__).parent / "output" / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Create nerfstudio config
        config = {
            "method_name": "splatfacto",
            "data": "nerfstudio.data",
            "output_dir": str(output_dir),
            "pipeline": {
                "datamanager": {
                    "dataparser": "nerfstudio.data.parsers.colmap_parser",
                    "data": str(colmap_dir)
                },
                "model": {
                    "output_splat_directory": str(output_dir / "splat")
                }
            },
            "logging": {
                "steps_per_log": 10,
                "max_buffer_size": 100
            },
            "optim": {
                "max_num_iterations": 30000  # 30k iterations for good quality
            }
        }
        
        config_file = output_dir / "config.json"
        with open(config_file, "w") as f:
            json.dump(config, f, indent=2)
        
        try:
            # Run nerfstudio training
            print("Starting Gaussian Splatting training with nerfstudio...")
            
            # Use ns-train command
            train_cmd = [
                "ns-train",
                "splatfacto",
                "--data", str(colmap_dir),
                "--output-dir", str(output_dir),
                "--max-num-iterations", "30000",
                "--vis", "wandb"  # Disable wandb by using tensorboard instead
            ]
            
            # For headless training, disable wandb
            env = os.environ.copy()
            env["WANDB_MODE"] = "disabled"
            
            result = subprocess.run(
                train_cmd,
                capture_output=True,
                text=True,
                env=env,
                timeout=7200  # 2 hour timeout
            )
            
            if result.returncode != 0:
                return {
                    "status": "failed",
                    "error": result.stderr,
                    "stdout": result.stdout
                }
            
            # Check for generated splat file
            splat_dir = output_dir / "splatfacto" / "splat"
            if splat_dir.exists():
                splat_files = list(splat_dir.glob("*.ply"))
                print(f"Generated {len(splat_files)} splat files")
            else:
                return {
                    "status": "failed",
                    "error": "Splat output directory not found"
                }
            
            print("Gaussian Splatting training completed")
            
            return {
                "status": "completed",
                "output_dir": str(output_dir),
                "splat_dir": str(splat_dir),
                "splat_files": [f.name for f in splat_files.glob("*.ply")],
                "iterations": 30000
            }
            
        except subprocess.TimeoutExpired:
            return {
                "status": "failed",
                "error": "Training timed out after 2 hours"
            }
        except Exception as e:
            return {
                "status": "failed",
                "error": str(e)
            }
    
    async def export_assets(self, job_id: str, training_result: Dict) -> Dict:
        """
        Export reconstruction assets for use in the viewer.
        
        Exports:
        - Gaussian Splat PLY files
        - Config files for loading
        - Preview renders
        
        Args:
            job_id: Job identifier
            training_result: Training results
        
        Returns:
            Dict with exported assets
        """
        output_dir = Path(__file__).parent / "output" / job_id
        
        if training_result.get("status") != "completed":
            return {
                "status": "skipped",
                "reason": "Training did not complete",
                "training_status": training_result.get("status")
            }
        
        assets = []
        limitations = []
        navigation_available = False
        
        # List all generated files
        for file_path in output_dir.rglob("*"):
            if file_path.is_file():
                relative_path = file_path.relative_to(output_dir)
                assets.append(str(relative_path))
        
        # Check for splat files
        splat_dir = output_dir / "splatfacto" / "splat"
        if splat_dir.exists():
            splat_files = list(splat_dir.glob("*.ply"))
            if not splat_files:
                limitations.append("No Gaussian Splat PLY files generated")
        else:
            limitations.append("Splat output directory not found")
        
        # Navigation geometry (not currently generated by Gaussian Splatting)
        limitations.append("Navigation mesh not generated - Gaussian Splatting produces visual representation only")
        limitations.append("Collision detection not available - requires separate mesh reconstruction")
        limitations.append("Room topology not inferred - requires semantic segmentation")
        
        # Check for renders
        renders_dir = output_dir / "splatfacto" / "renders"
        if renders_dir.exists():
            render_files = list(renders_dir.glob("*.png"))
            if render_files:
                print(f"Found {len(render_files)} preview renders")
        
        return {
            "status": "completed",
            "assets": assets,
            "assets_dir": str(output_dir),
            "navigation_available": navigation_available,
            "limitations": limitations,
            "note": "Gaussian Splatting provides high-quality visual reconstruction but does not generate navigable geometry. For game-like exploration, additional mesh reconstruction and collision detection would be required."
        }
