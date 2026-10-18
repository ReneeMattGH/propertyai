"""
Photo analyzer module
Analyzes house photographs and assigns them to rooms
"""
import cv2
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional


class PhotoAnalyzer:
    """Analyze house photographs and assign to rooms."""
    
    def __init__(self):
        pass
    
    async def analyze_photos(
        self,
        job_id: str,
        photo_paths: List[str],
        floor_plan_result: Dict
    ) -> Dict:
        """
        Analyze photographs and assign them to rooms.
        
        Args:
            job_id: Job identifier
            photo_paths: List of photo file paths
            floor_plan_result: Results from floor plan parsing
        
        Returns:
            Dict with photo analysis results
        """
        results = {
            "total_photos": len(photo_paths),
            "analyzed_photos": 0,
            "room_assignments": {},
            "unassigned_photos": [],
            "photo_features": {}
        }
        
        for i, photo_path in enumerate(photo_paths):
            photo = Path(photo_path)
            if not photo.exists():
                continue
            
            # Load photo
            image = cv2.imread(str(photo))
            if image is None:
                continue
            
            # Extract basic features
            features = self._extract_features(image)
            results["photo_features"][photo.name] = features
            results["analyzed_photos"] += 1
            
            # Attempt to assign to a room
            # For now, this is a simplified assignment
            # A more sophisticated version would use room detection
            room_assignment = self._guess_room_type(features)
            
            if room_assignment:
                if room_assignment not in results["room_assignments"]:
                    results["room_assignments"][room_assignment] = []
                results["room_assignments"][room_assignment].append(photo.name)
            else:
                results["unassigned_photos"].append(photo.name)
        
        # Save photo analysis results
        self._save_photo_analysis(job_id, photo_paths, results)
        
        return results
    
    def _extract_features(self, image: np.ndarray) -> Dict:
        """
        Extract visual features from a photo.
        
        Returns:
            Dict with feature descriptors
        """
        features = {
            "width": image.shape[1],
            "height": image.shape[0],
            "aspect_ratio": image.shape[1] / image.shape[0],
            "brightness": float(np.mean(image)),
            "contrast": float(np.std(image)),
            "dominant_colors": self._get_dominant_colors(image, k=3)
        }
        
        return features
    
    def _get_dominant_colors(self, image: np.ndarray, k: int = 3) -> List[List[int]]:
        """
        Extract dominant colors using k-means clustering.
        
        Returns:
            List of RGB color values
        """
        try:
            # Reshape image to list of pixels
            pixels = image.reshape(-1, 3)
            
            # Simple quantization (not full k-means for speed)
            # Just sample random pixels
            indices = np.random.choice(len(pixels), min(1000, len(pixels)), replace=False)
            sampled_pixels = pixels[indices]
            
            # Calculate average color
            avg_color = np.mean(sampled_pixels, axis=0).astype(int)
            
            return [avg_color.tolist()]
        except:
            return [[128, 128, 128]]
    
    def _guess_room_type(self, features: Dict) -> Optional[str]:
        """
        Guess room type based on visual features.
        
        This is a simplified heuristic-based approach.
        A production system would use a trained classifier.
        
        Returns:
            Room type string or None
        """
        # Very basic heuristics - this would be replaced with ML
        brightness = features.get("brightness", 128)
        contrast = features.get("contrast", 50)
        
        # Bright rooms might be living areas
        if brightness > 150:
            return "living_room"
        elif brightness > 100:
            return "bedroom"
        else:
            return "unknown"
    
    def _save_photo_analysis(
        self,
        job_id: str,
        photo_paths: List[str],
        results: Dict
    ):
        """Save photo analysis results."""
        import json
        output_dir = Path(__file__).parent / "geometry" / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        
        analysis_file = output_dir / "photo_analysis.json"
        with open(analysis_file, "w") as f:
            json.dump(results, f, indent=2, default=str)
