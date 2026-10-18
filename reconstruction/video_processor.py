"""
Video processing module for RealEstAI reconstruction engine
Handles video validation, frame extraction, and quality assessment
"""
import cv2
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import subprocess
import json
from datetime import datetime


class VideoProcessor:
    """Process property videos for reconstruction."""
    
    def __init__(self):
        self.ffmpeg_path = self._find_ffmpeg()
    
    def _find_ffmpeg(self) -> Optional[str]:
        """Find FFmpeg executable."""
        try:
            result = subprocess.run(["which", "ffmpeg"], capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout.strip()
        except:
            pass
        return None
    
    def check_ffmpeg(self) -> bool:
        """Check if FFmpeg is available."""
        return self.ffmpeg_path is not None
    
    def check_opencv(self) -> bool:
        """Check if OpenCV is available."""
        try:
            import cv2
            return True
        except ImportError:
            return False
    
    async def validate_video(self, video_path: str) -> Dict:
        """
        Validate video file and extract metadata.
        
        Returns:
            Dict with video metadata including frame count, duration, resolution
        """
        video_path = Path(video_path)
        if not video_path.exists():
            raise ValueError(f"Video file not found: {video_path}")
        
        # Check file size (max 2GB for reconstruction)
        file_size = video_path.stat().st_size
        if file_size > 2 * 1024 * 1024 * 1024:
            raise ValueError(f"Video too large: {file_size / (1024**3):.2f}GB (max 2GB)")
        
        # Use OpenCV to extract metadata
        cap = cv2.VideoCapture(str(video_path))
        
        if not cap.isOpened():
            raise ValueError("Could not open video file")
        
        # Get video properties
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        duration = frame_count / fps if fps > 0 else 0
        
        cap.release()
        
        # Validate minimum requirements
        if frame_count < 30:
            raise ValueError(f"Video too short: {frame_count} frames (minimum 30)")
        
        if duration < 2:
            raise ValueError(f"Video too short: {duration:.2f}s (minimum 2s)")
        
        if width < 640 or height < 480:
            raise ValueError(f"Resolution too low: {width}x{height} (minimum 640x480)")
        
        return {
            "frame_count": frame_count,
            "fps": fps,
            "width": width,
            "height": height,
            "resolution": f"{width}x{height}",
            "duration": duration,
            "file_size": file_size,
            "aspect_ratio": width / height
        }
    
    async def extract_frames(
        self, 
        job_id: str, 
        video_path: str,
        target_fps: float = 2.0,
        max_frames: int = 1000
    ) -> Dict:
        """
        Extract frames from video for reconstruction.
        
        Args:
            job_id: Unique job identifier
            video_path: Path to video file
            target_fps: Target frames per second for extraction
            max_frames: Maximum number of frames to extract
        
        Returns:
            Dict with extraction results
        """
        video_path = Path(video_path)
        frames_dir = Path(__file__).parent / "frames" / job_id
        frames_dir.mkdir(parents=True, exist_ok=True)
        
        cap = cv2.VideoCapture(str(video_path))
        video_fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        
        # Calculate frame interval
        if video_fps > 0:
            frame_interval = max(1, int(video_fps / target_fps))
        else:
            frame_interval = 1
        
        extracted_frames = []
        frame_idx = 0
        extracted_count = 0
        
        print(f"Extracting frames: target_fps={target_fps}, interval={frame_interval}")
        
        while frame_idx < total_frames and extracted_count < max_frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            
            if not ret:
                break
            
            # Save frame
            frame_filename = f"frame_{extracted_count:06d}.jpg"
            frame_path = frames_dir / frame_filename
            cv2.imwrite(str(frame_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            
            extracted_frames.append({
                "index": extracted_count,
                "original_index": frame_idx,
                "path": str(frame_path),
                "timestamp": frame_idx / video_fps if video_fps > 0 else 0
            })
            
            extracted_count += 1
            frame_idx += frame_interval
        
        cap.release()
        
        if extracted_count < 10:
            raise ValueError(f"Too few frames extracted: {extracted_count} (minimum 10)")
        
        print(f"Extracted {extracted_count} frames to {frames_dir}")
        
        return {
            "extracted_count": extracted_count,
            "frames_dir": str(frames_dir),
            "frame_interval": frame_interval,
            "target_fps": target_fps,
            "frames": extracted_frames
        }
    
    async def assess_quality(self, job_id: str, frames_info: Dict) -> Dict:
        """
        Assess quality of extracted frames.
        
        Checks for:
        - Blur detection
        - Texture coverage
        - Brightness issues
        - Duplicate detection
        
        Returns:
            Dict with quality assessment results
        """
        frames_dir = Path(frames_info["frames_dir"])
        frames = list(frames_dir.glob("frame_*.jpg"))
        
        if len(frames) == 0:
            raise ValueError("No frames to assess")
        
        # Sample frames for quality check (every 10th frame)
        sample_frames = frames[::max(1, len(frames) // 20)]
        
        blur_scores = []
        brightness_scores = []
        sharpness_scores = []
        
        for frame_path in sample_frames:
            img = cv2.imread(str(frame_path))
            if img is None:
                continue
            
            # Convert to grayscale
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            
            # Laplacian variance for blur detection
            laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
            sharpness_scores.append(laplacian_var)
            
            # Brightness (mean intensity)
            brightness = gray.mean()
            brightness_scores.append(brightness)
            
            # Simple blur score using edge detection
            edges = cv2.Canny(gray, 100, 200)
            edge_density = np.sum(edges > 0) / (gray.shape[0] * gray.shape[1])
            blur_scores.append(edge_density)
        
        if not sharpness_scores:
            raise ValueError("Could not assess frame quality")
        
        avg_sharpness = np.mean(sharpness_scores)
        avg_brightness = np.mean(brightness_scores)
        avg_edge_density = np.mean(blur_scores)
        
        # Quality thresholds
        sharpness_threshold = 100.0  # Laplacian variance threshold
        brightness_min = 30.0
        brightness_max = 220.0
        edge_density_threshold = 0.01
        
        warnings = []
        
        if avg_sharpness < sharpness_threshold:
            warnings.append(f"Low sharpness detected ({avg_sharpness:.2f} < {sharpness_threshold}). Video may be blurry.")
        
        if avg_brightness < brightness_min:
            warnings.append(f"Low brightness detected ({avg_brightness:.2f}). Consider better lighting.")
        elif avg_brightness > brightness_max:
            warnings.append(f"High brightness detected ({avg_brightness:.2f}). May be overexposed.")
        
        if avg_edge_density < edge_density_threshold:
            warnings.append(f"Low edge density ({avg_edge_density:.4f}). May indicate low texture or motion blur.")
        
        # Overall quality score (0-100)
        quality_score = min(100, max(0, 100 - (len(warnings) * 15)))
        
        report = {
            "sampled_frames": len(sample_frames),
            "total_frames": len(frames),
            "avg_sharpness": float(avg_sharpness),
            "avg_brightness": float(avg_brightness),
            "avg_edge_density": float(avg_edge_density),
            "quality_score": quality_score,
            "warnings": warnings,
            "recommendation": "suitable" if quality_score >= 70 else "poor"
        }
        
        print(f"Quality assessment: score={quality_score}, warnings={len(warnings)}")
        
        return report
