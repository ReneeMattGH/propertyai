"""
Create a test video for reconstruction testing
"""
import cv2
import numpy as np
from pathlib import Path

def create_test_video(output_path: str, duration: float = 5.0, fps: float = 30.0):
    """
    Create a synthetic test video with a simple scene.
    
    Args:
        output_path: Path to save the video
        duration: Duration in seconds
        fps: Frames per second
    """
    width, height = 640, 480
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    
    out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))
    
    # Create a simple room simulation
    # Gradient background with moving "camera"
    total_frames = int(duration * fps)
    
    for frame_idx in range(total_frames):
        # Create a frame with a simulated room
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        
        # Simulate wall colors
        # Back wall (blue)
        frame[:, :, 0] = 60
        frame[:, :, 1] = 80
        frame[:, :, 2] = 120
        
        # Floor (brown)
        frame[int(height*0.7):, :, 0] = 100
        frame[int(height*0.7):, :, 1] = 80
        frame[int(height*0.7):, :, 2] = 60
        
        # Ceiling (white-ish)
        frame[:int(height*0.3), :, :] = 200
        
        # Add some "furniture" (simple rectangles)
        # Table
        cv2.rectangle(frame, (200, 300), (440, 380), (150, 100, 50), -1)
        
        # Add some texture variation
        noise = np.random.randint(-20, 20, (height, width, 3), dtype=np.int16)
        frame = np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        
        # Simulate camera movement by shifting the "scene"
        offset_x = int(20 * np.sin(frame_idx * 0.1))
        frame = np.roll(frame, offset_x, axis=1)
        
        out.write(frame)
    
    out.release()
    print(f"Created test video: {output_path}")
    print(f"Duration: {duration}s, FPS: {fps}, Frames: {total_frames}")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    
    # Create test video in input directory
    input_dir = Path(__file__).parent / "input"
    input_dir.mkdir(exist_ok=True)
    
    test_video_path = input_dir / "test_property.mp4"
    create_test_video(str(test_video_path), duration=5.0, fps=30.0)
