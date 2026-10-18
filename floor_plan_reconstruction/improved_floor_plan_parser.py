"""
Improved floor plan parser with better room and doorway detection
"""
import cv2
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from PIL import Image


class ImprovedFloorPlanParser:
    """Parse floor plans with advanced room and doorway detection."""
    
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
        """Validate input files."""
        results = {
            "floor_plan_valid": False,
            "photos_valid": False,
            "floor_plan_size": 0,
            "photo_count": len(photo_paths),
            "errors": []
        }
        
        floor_plan = Path(floor_plan_path)
        if not floor_plan.exists():
            results["errors"].append("Floor plan file not found")
            return results
        
        results["floor_plan_size"] = floor_plan.stat().st_size
        results["floor_plan_valid"] = True
        
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
        Parse floor plan with improved room and doorway detection.
        """
        floor_plan = Path(floor_plan_path)
        
        # Convert PDF to image if necessary
        if floor_plan.suffix.lower() == '.pdf':
            from pdf2image import convert_from_path
            images = convert_from_path(str(floor_plan), dpi=300)
            if len(images) > 0:
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
        
        # Apply adaptive thresholding
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 11, 2
        )
        
        # Morphological operations to clean up
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        cleaned = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=2)
        
        # Detect walls using edge detection
        edges = cv2.Canny(cleaned, 50, 150, apertureSize=3)
        
        # Detect lines using Hough transform
        lines = cv2.HoughLinesP(
            edges, 1, np.pi/180, threshold=80,
            minLineLength=40, maxLineGap=15
        )
        
        # Extract and filter wall segments
        wall_segments = []
        if lines is not None:
            for line in lines:
                if len(line) == 1 and len(line[0]) == 4:
                    x1, y1, x2, y2 = line[0]
                    length = float(np.sqrt((x2-x1)**2 + (y2-y1)**2))
                    if length > 30:  # Filter very short lines
                        wall_segments.append({
                            "start": [int(x1), int(y1)],
                            "end": [int(x2), int(y2)],
                            "length": length
                        })
        
        # Detect rooms using hierarchical approach
        rooms = self._detect_rooms_advanced(cleaned, gray)
        
        # Detect doorways
        doorways = self._detect_doorways_advanced(wall_segments, cleaned, image.shape)
        
        # Estimate scale
        scale_pixels_per_meter, scale_source = self._estimate_scale(
            known_width, known_length, image.shape, rooms
        )
        
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
            "wall_segments": wall_segments[:100],  # Limit for JSON
            "doorways": doorways
        }
        
        # Save intermediate results
        self._save_intermediate_results(job_id, image, edges, rooms, doorways)
        
        return result
    
    def _detect_rooms_advanced(self, binary: np.ndarray, gray: np.ndarray) -> List[Dict]:
        """
        Advanced room detection using multiple techniques.
        """
        rooms = []
        
        # Method 1: Contour detection on inverted binary
        inverted = cv2.bitwise_not(binary)
        contours, _ = cv2.findContours(
            inverted, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        
        for i, contour in enumerate(contours):
            area = cv2.contourArea(contour)
            if area > 5000:  # Minimum room area
                x, y, w, h = cv2.boundingRect(contour)
                aspect_ratio = w / h if h > 0 else 0
                if 0.3 < aspect_ratio < 3.0:
                    rooms.append({
                        "id": i,
                        "bounds": [int(x), int(y), int(w), int(h)],
                        "area": float(area),
                        "center": [int(x + w/2), int(y + h/2)],
                        "aspect_ratio": aspect_ratio,
                        "method": "contour"
                    })
        
        # Method 2: Hierarchical contour detection for nested rooms
        try:
            # Find all contours with hierarchy
            contours, hierarchy = cv2.findContours(
                inverted, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE
            )
            
            # Use hierarchy to find nested rooms (rooms within rooms)
            for i, contour in enumerate(contours):
                area = cv2.contourArea(contour)
                if area > 5000:
                    x, y, w, h = cv2.boundingRect(contour)
                    aspect_ratio = w / h if h > 0 else 0
                    if 0.3 < aspect_ratio < 3.0:
                        rooms.append({
                            "id": len(rooms) + 1000,
                            "bounds": [int(x), int(y), int(w), int(h)],
                            "area": float(area),
                            "center": [int(x + w/2), int(y + h/2)],
                            "aspect_ratio": aspect_ratio,
                            "method": "hierarchical_contour"
                        })
        except Exception as e:
            print(f"Hierarchical contour failed: {e}")
        
        # Deduplicate rooms by center proximity
        rooms = self._deduplicate_rooms(rooms)
        
        return rooms
    
    def _deduplicate_rooms(self, rooms: List[Dict]) -> List[Dict]:
        """Remove duplicate rooms based on center proximity."""
        if len(rooms) == 0:
            return rooms
        
        # Sort by area (largest first)
        rooms_sorted = sorted(rooms, key=lambda r: r['area'], reverse=True)
        
        deduplicated = []
        proximity_threshold = 50  # pixels
        
        for room in rooms_sorted:
            is_duplicate = False
            for existing in deduplicated:
                center1 = np.array(room['center'])
                center2 = np.array(existing['center'])
                distance = np.linalg.norm(center1 - center2)
                
                if distance < proximity_threshold:
                    is_duplicate = True
                    break
            
            if not is_duplicate:
                deduplicated.append(room)
        
        return deduplicated
    
    def _detect_doorways_advanced(
        self,
        wall_segments: List[Dict],
        binary: np.ndarray,
        image_shape: Tuple[int, int, int]
    ) -> List[Dict]:
        """
        Advanced doorway detection.
        """
        doorways = []
        
        # Group wall segments by orientation (horizontal vs vertical)
        horizontal_walls = []
        vertical_walls = []
        
        for wall in wall_segments:
            start = np.array(wall['start'])
            end = np.array(wall['end'])
            vector = end - start
            
            # Determine orientation
            if abs(vector[0]) > abs(vector[1]):
                horizontal_walls.append(wall)
            else:
                vertical_walls.append(wall)
        
        # Find gaps in horizontal walls (potential doorways)
        for i, wall1 in enumerate(horizontal_walls):
            for wall2 in horizontal_walls[i+1:]:
                if self._walls_aligned_horizontally(wall1, wall2):
                    gap = self._calculate_gap(wall1, wall2)
                    if 20 < gap < 150:  # Doorway width range in pixels
                        doorways.append({
                            "type": "horizontal",
                            "gap": gap,
                            "position": self._gap_center(wall1, wall2),
                            "confidence": 0.7
                        })
        
        # Find gaps in vertical walls
        for i, wall1 in enumerate(vertical_walls):
            for wall2 in vertical_walls[i+1:]:
                if self._walls_aligned_vertically(wall1, wall2):
                    gap = self._calculate_gap(wall1, wall2)
                    if 20 < gap < 150:
                        doorways.append({
                            "type": "vertical",
                            "gap": gap,
                            "position": self._gap_center(wall1, wall2),
                            "confidence": 0.7
                        })
        
        return doorways
    
    def _walls_aligned_horizontally(self, wall1: Dict, wall2: Dict) -> bool:
        """Check if two walls are aligned horizontally."""
        y1 = (wall1['start'][1] + wall1['end'][1]) / 2
        y2 = (wall2['start'][1] + wall2['end'][1]) / 2
        return abs(y1 - y2) < 10  # 10 pixel tolerance
    
    def _walls_aligned_vertically(self, wall1: Dict, wall2: Dict) -> bool:
        """Check if two walls are aligned vertically."""
        x1 = (wall1['start'][0] + wall1['end'][0]) / 2
        x2 = (wall2['start'][0] + wall2['end'][0]) / 2
        return abs(x1 - x2) < 10  # 10 pixel tolerance
    
    def _calculate_gap(self, wall1: Dict, wall2: Dict) -> float:
        """Calculate gap between two walls."""
        return min(
            abs(wall1['end'][0] - wall2['start'][0]),
            abs(wall2['end'][0] - wall1['start'][0])
        )
    
    def _gap_center(self, wall1: Dict, wall2: Dict) -> List[float]:
        """Calculate center point of a gap."""
        x1 = wall1['end'][0]
        x2 = wall2['start'][0]
        y = (wall1['end'][1] + wall2['start'][1]) / 2
        return [(x1 + x2) / 2, y]
    
    def _estimate_scale(
        self,
        known_width: Optional[float],
        known_length: Optional[float],
        image_shape: Tuple[int, int, int],
        rooms: List[Dict]
    ) -> Tuple[Optional[float], str]:
        """Estimate scale in pixels per meter."""
        # If known dimensions provided, use them
        if known_width and known_length:
            image_width = image_shape[1]
            image_height = image_shape[0]
            scale_width = image_width / known_width
            scale_height = image_height / known_length
            return (scale_width + scale_height) / 2, "provided_dimensions"
        
        # Otherwise, estimate from typical room sizes
        # Average room is ~3-5 meters wide
        if rooms:
            avg_width_pixels = np.mean([r['bounds'][2] for r in rooms])
            # Assume average room is 4 meters wide
            return avg_width_pixels / 4.0, "estimated_from_rooms"
        
        # Fallback: use standard scale
        return 75.0, "estimated_standard"
    
    def _save_intermediate_results(
        self,
        job_id: str,
        image: np.ndarray,
        edges: np.ndarray,
        rooms: List[Dict],
        doorways: List[Dict]
    ):
        """Save intermediate processing results."""
        output_dir = Path(__file__).parent / "geometry" / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Save original
        cv2.imwrite(str(output_dir / "original.png"), image)
        
        # Save edges
        cv2.imwrite(str(output_dir / "edges.png"), edges)
        
        # Save room visualization
        room_viz = image.copy()
        for room in rooms:
            x, y, w, h = room['bounds']
            cv2.rectangle(room_viz, (x, y), (x+w, y+h), (0, 255, 0), 2)
            cv2.putText(room_viz, f"Room {room['id']}", (x, y-5),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        cv2.imwrite(str(output_dir / "rooms_detected.png"), room_viz)
        
        # Save doorway visualization
        doorway_viz = image.copy()
        for doorway in doorways:
            pos = doorway['position']
            cv2.circle(doorway_viz, (int(pos[0]), int(pos[1])), 15, (0, 0, 255), -1)
        cv2.imwrite(str(output_dir / "doorways_detected.png"), doorway_viz)
