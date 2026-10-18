"""
Create test room photos
"""
import cv2
import numpy as np
from pathlib import Path


def create_test_room_photo(output_path: str, room_type: str):
    """
    Create a test photo of a room.
    
    Args:
        output_path: Path to save the photo
        room_type: Type of room (living_room, kitchen, bedroom, bathroom)
    """
    # Create a canvas
    width, height = 640, 480
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    
    # Set room-specific colors
    if room_type == "living_room":
        # Warm colors
        wall_color = (200, 180, 160)
        floor_color = (160, 140, 120)
        brightness = 140
    elif room_type == "kitchen":
        # Bright colors
        wall_color = (220, 220, 200)
        floor_color = (180, 180, 160)
        brightness = 160
    elif room_type == "bedroom":
        # Soft colors
        wall_color = (180, 160, 200)
        floor_color = (140, 120, 160)
        brightness = 130
    elif room_type == "bathroom":
        # Cool colors
        wall_color = (200, 220, 240)
        floor_color = (180, 200, 220)
        brightness = 150
    else:
        wall_color = (200, 200, 200)
        floor_color = (180, 180, 180)
        brightness = 140
    
    # Draw walls
    canvas[:, :] = wall_color
    
    # Draw floor (bottom 30%)
    floor_start = int(height * 0.7)
    canvas[floor_start:, :] = floor_color
    
    # Add some noise for realism
    noise = np.random.randint(-20, 20, (height, width, 3), dtype=np.int16)
    canvas = np.clip(canvas.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    
    # Add brightness variation
    brightness_adj = np.random.randint(-10, 10)
    canvas = np.clip(canvas.astype(np.int16) + brightness_adj, 0, 255).astype(np.uint8)
    
    # Add some "furniture" (simple rectangles)
    if room_type == "living_room":
        # Sofa
        cv2.rectangle(canvas, (100, 250), (300, 350), (100, 80, 60), -1)
        # TV stand
        cv2.rectangle(canvas, (400, 280), (550, 320), (80, 60, 40), -1)
    elif room_type == "kitchen":
        # Counter
        cv2.rectangle(canvas, (50, 280), (300, 350), (150, 130, 110), -1)
        # Table
        cv2.rectangle(canvas, (350, 300), (450, 380), (120, 100, 80), -1)
    elif room_type == "bedroom":
        # Bed
        cv2.rectangle(canvas, (150, 250), (400, 380), (140, 120, 180), -1)
        # Nightstand
        cv2.rectangle(canvas, (420, 300), (480, 380), (120, 100, 140), -1)
    elif room_type == "bathroom":
        # Sink
        cv2.rectangle(canvas, (200, 300), (300, 350), (180, 200, 220), -1)
        # Toilet
        cv2.ellipse(canvas, (450, 320), (30, 40), 0, 0, 360, (200, 220, 240), -1)
    
    # Save the image
    cv2.imwrite(output_path, canvas)
    print(f"Created test photo: {output_path}")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    
    # Create test photos in input directory
    input_dir = Path(__file__).parent / "input"
    input_dir.mkdir(exist_ok=True)
    
    # Create photos for each room
    rooms = ["living_room", "kitchen", "bedroom", "bathroom"]
    for room in rooms:
        photo_path = input_dir / f"test_{room}.jpg"
        create_test_room_photo(str(photo_path), room)
