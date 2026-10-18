"""
Floor plan parser module
Extracts walls, rooms, and doorways from floor plan images
"""
import cv2
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from PIL import Image


class FloorPlanParser:
    """Parse floor plans to extract architectural geometry."""
    
    def __init__(self):
        pass
    
    def check_opencv(self) -> bool:
        """Check if OpenCV is available."""
        try:
            import cv2
            return True
        except ImportError:
            return False
    
    def check_pillow(self) -> bool:
        """Check if Pillow is available."""
        try:
            from PIL import Image
            return True
        except ImportError:
            return False
    
    async def validate_inputs(self, floor_plan_path: str, photo_paths: List[str]) -> Dict:
        """
        Validate input files.
        
        Returns:
            Dict with validation results
        """
        results = {
            "floor_plan_valid": False,
            "photos_valid": False,
            "floor_plan_size": 0,
            "photo_count": len(photo_paths),
            "errors": []
        }
        
        # Validate floor plan
        floor_plan = Path(floor_plan_path)
        if not floor_plan.exists():
            results["errors"].append("Floor plan file not found")
            return results
        
        results["floor_plan_size"] = floor_plan.stat().st_size
        results["floor_plan_valid"] = True
        
        # Validate photos
        valid_photos = 0
        for photo_path in photo_paths:
            photo = Path(photo_path)
            if photo.exists():
                valid_photos += 1
            else:
                results["errors"].append(f"Photo not found: {photo_path}")
        
        results["photos_valid"] = valid_photos > 0
        results["valid_photo_count"] = valid_photos
        
        return results
    
    async def parse_floor_plan(
        self,
        job_id: str,
        floor_plan_path: str,
        known_width: Optional[float],
        known_length: Optional[float]
    ) -> Dict:
        """
        Parse floor plan to extract walls, rooms, and doorways.
        
        Args:
            job_id: Job identifier
            floor_plan_path: Path to floor plan image
            known_width: Known width in meters (optional)
            known_length: Known length in meters (optional)
        
        Returns:
            Dict with parsed floor plan data
        """
        floor_plan = Path(floor_plan_path)
        
        # Convert PDF to image if necessary
        if floor_plan.suffix.lower() == '.pdf':
            from pdf2image import convert_from_path
            images = convert_from_path(str(floor_plan), dpi=300)
            if len(images) > 0:
                # Save first page as image
                image_path = floor_plan.parent / f"{floor_plan.stem}_page1.png"
                images[0].save(str(image_path))
                floor_plan = image_path
            else:
                return {
                    "status": "failed",
                    "error": "PDF has no pages"
                }
        
        # Load image
        try:
            image = cv2.imread(str(floor_plan))
            if image is None:
                return {
                    "status": "failed",
                    "error": "Could not load floor plan image"
                }
        except Exception as e:
            return {
                "status": "failed",
                "error": f"Error loading image: {str(e)}"
            }
        
        # Convert to grayscale
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        
        # Apply adaptive thresholding to get binary image
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 11, 2
        )
        
        # Detect walls using edge detection
        edges = cv2.Canny(binary, 50, 150, apertureSize=3)
        
        # Detect lines using Hough transform
        lines = cv2.HoughLinesP(
            edges, 1, np.pi/180, threshold=100,
            minLineLength=50, maxLineGap=10
        )
        
        # Extract line segments
        wall_segments = []
        if lines is not None:
            for line in lines:
                if len(line) == 1 and len(line[0]) == 4:
                    x1, y1, x2, y2 = line[0]
                    wall_segments.append({
                        "start": [int(x1), int(y1)],
                        "end": [int(x2), int(y2)],
                        "length": float(np.sqrt((x2-x1)**2 + (y2-y1)**2))
                    })
        
        # Find contours for rooms
        contours, _ = cv2.findContours(
            binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        
        rooms = []
        for i, contour in enumerate(contours):
            area = cv2.contourArea(contour)
            if area > 1000:  # Filter small contours
                x, y, w, h = cv2.boundingRect(contour)
                rooms.append({
                    "id": i,
                    "bounds": [int(x), int(y), int(w), int(h)],
                    "area": float(area),
                    "center": [int(x + w/2), int(y + h/2)]
                })
        
        # Estimate scale if known dimensions provided
        scale_pixels_per_meter = None
        scale_source = "estimated"
        
        if known_width and known_length:
            # Use known dimensions to calculate scale
            image_width = image.shape[1]
            image_height = image.shape[0]
            
            # Assume the floor plan fills most of the image
            scale_width = image_width / known_width
            scale_height = image_height / known_length
            scale_pixels_per_meter = (scale_width + scale_height) / 2
            scale_source = "provided_dimensions"
        else:
            # Estimate based on typical room sizes
            # Average room in pixels should be ~50-100 pixels per meter
            scale_pixels_per_meter = 75.0
            scale_source = "estimated"
        
        # Detect potential doorways (gaps in walls)
        doorways = self._detect_doorways(wall_segments, binary)
        
        # Calculate real-world dimensions
        real_width = image.shape[1] / scale_pixels_per_meter if scale_pixels_per_meter else None
        real_height = image.shape[0] / scale_pixels_per_meter if scale_pixels_per_meter else None
        
        result = {
            "status": "completed",
            "image_dimensions": [image.shape[1], image.shape[0]],
            "wall_segments_count": len(wall_segments),
            "rooms_count": len(rooms),
            "doorways_count": len(doorways),
            "scale_pixels_per_meter": scale_pixels_per_meter,
            "scale_source": scale_source,
            "real_dimensions_meters": {
                "width": real_width,
                "height": real_height
            },
            "rooms": rooms,
            "wall_segments": wall_segments[:50],  # Limit to prevent huge JSON
            "doorways": doorways
        }
        
        # Save intermediate results
        self._save_intermediate_results(job_id, image, edges, contours)
        
        return result
    
    def _detect_doorways(self, wall_segments: List[Dict], binary: np.ndarray) -> List[Dict]:
        """
        Detect potential doorways in the floor plan.
        
        Doorways appear as gaps in walls or as specific markers.
        """
        doorways = []
        
        # Simple heuristic: find horizontal and vertical gaps
        # This is a simplified implementation
        # A more sophisticated implementation would use wall analysis
        
        # For now, return empty list - this would be enhanced with better algorithms
        return doorways
    
    def _save_intermediate_results(
        self,
        job_id: str,
        image: np.ndarray,
        edges: np.ndarray,
        contours: List
    ):
        """Save intermediate processing results for debugging."""
        output_dir = Path(__file__).parent / "geometry" / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Save original
        cv2.imwrite(str(output_dir / "original.png"), image)
        
        # Save edges
        cv2.imwrite(str(output_dir / "edges.png"), edges)
        
        # Save contours visualization
        contour_image = image.copy()
        cv2.drawContours(contour_image, contours, -1, (0, 255, 0), 2)
        cv2.imwrite(str(output_dir / "contours.png"), contour_image)
