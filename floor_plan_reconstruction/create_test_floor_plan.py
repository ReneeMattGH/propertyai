"""
Create a test floor plan image
"""
import cv2
import numpy as np
from pathlib import Path


def create_test_floor_plan(output_path: str):
    """
    Create a simple test floor plan with rooms.
    
    Args:
        output_path: Path to save the floor plan image
    """
    # Create a white canvas
    width, height = 800, 600
    canvas = np.ones((height, width, 3), dtype=np.uint8) * 255
    
    # Draw walls (black lines)
    wall_thickness = 4
    wall_color = (0, 0, 0)
    
    # Outer walls
    cv2.rectangle(canvas, (50, 50), (750, 550), wall_color, wall_thickness)
    
    # Internal walls - create rooms
    # Living room (left)
    cv2.line(canvas, (300, 50), (300, 300), wall_color, wall_thickness)
    cv2.line(canvas, (50, 300), (300, 300), wall_color, wall_thickness)
    
    # Kitchen (top right)
    cv2.line(canvas, (300, 50), (300, 200), wall_color, wall_thickness)
    cv2.line(canvas, (300, 200), (750, 200), wall_color, wall_thickness)
    
    # Bedroom (right)
    cv2.line(canvas, (550, 200), (550, 550), wall_color, wall_thickness)
    cv2.line(canvas, (300, 400), (750, 400), wall_color, wall_thickness)
    
    # Bathroom (bottom right)
    cv2.line(canvas, (550, 400), (550, 550), wall_color, wall_thickness)
    
    # Add room labels
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.7
    font_color = (100, 100, 100)
    thickness = 2
    
    # Living room
    cv2.putText(canvas, "Living Room", (100, 180), font, font_scale, font_color, thickness)
    
    # Kitchen
    cv2.putText(canvas, "Kitchen", (400, 130), font, font_scale, font_color, thickness)
    
    # Bedroom
    cv2.putText(canvas, "Bedroom", (580, 320), font, font_scale, font_color, thickness)
    
    # Bathroom
    cv2.putText(canvas, "Bath", (580, 480), font, font_scale, font_color, thickness)
    
    # Add door indicators (gaps in walls)
    # Door from living room to kitchen
    cv2.line(canvas, (280, 180), (320, 180), (255, 255, 255), wall_thickness + 2)
    
    # Door from living room to bedroom
    cv2.line(canvas, (280, 350), (320, 350), (255, 255, 255), wall_thickness + 2)
    
    # Door from bedroom to bathroom
    cv2.line(canvas, (530, 450), (570, 450), (255, 255, 255), wall_thickness + 2)
    
    # Add dimensions
    dim_color = (150, 0, 0)
    cv2.putText(canvas, "10m", (360, 45), font, 0.5, dim_color, 1)
    cv2.putText(canvas, "8m", (20, 300), font, 0.5, dim_color, 1)
    
    # Save the image
    cv2.imwrite(output_path, canvas)
    print(f"Created test floor plan: {output_path}")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    
    # Create test floor plan in input directory
    input_dir = Path(__file__).parent / "input"
    input_dir.mkdir(exist_ok=True)
    
    test_floor_plan_path = input_dir / "test_floor_plan.png"
    create_test_floor_plan(str(test_floor_plan_path))
