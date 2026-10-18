# RealEstAI Reconstruction Engine

Standalone AI-powered video-to-3D reconstruction service for property tours.

## Overview

This service transforms property walkthrough videos into realistic 3D environments using:
- **COLMAP**: Structure-from-motion and sparse reconstruction
- **Gaussian Splatting (via nerfstudio)**: High-quality neural scene rendering
- **OpenCV**: Video processing and frame extraction

## Architecture

```
Input Video → Frame Extraction → Quality Assessment → COLMAP → Gaussian Splatting → Export
```

## Dependencies

### Required (CPU-only for video processing)
- Python 3.8+
- OpenCV (opencv-python)
- NumPy
- Pillow
- FastAPI + Uvicorn

### Required for Full Reconstruction
- **COLMAP**: Camera pose estimation and sparse reconstruction
  - Mac: `brew install colmap`
  - Linux: `conda install -c conda-forge colmap`
  - Or build from source: https://colmap.github.io/

- **NVIDIA GPU with CUDA**: Required for Gaussian Splatting training
  - CUDA 11.8+ or 12.x
  - Compute capability 7.0+

- **PyTorch with CUDA**: 
  ```bash
  pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
  ```

- **nerfstudio**:
  ```bash
  pip install nerfstudio
  ```

### Optional (for mesh processing)
- Open3D
- Trimesh

## Installation

### 1. Install base dependencies
```bash
cd reconstruction
pip install -r requirements.txt
```

### 2. Install COLMAP
**Mac:**
```bash
brew install colmap
```

**Linux (Ubuntu):**
```bash
sudo apt-get install colmap
```

**Or via conda:**
```bash
conda install -c conda-forge colmap
```

### 3. Install PyTorch with CUDA (GPU required)
Check your CUDA version and install accordingly:

For CUDA 11.8:
```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
```

For CUDA 12.1:
```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
```

Verify CUDA:
```bash
python -c "import torch; print(torch.cuda.is_available())"
```

### 4. Install nerfstudio
```bash
pip install nerfstudio
```

### 5. Verify installation
```bash
python -c "import cv2, nerfstudio, torch; print('All dependencies OK')"
```

## Running the Service

### Start the reconstruction server
```bash
cd reconstruction
python main.py
```

The API will be available at `http://localhost:8001`

### API Endpoints

- `GET /` - Service info
- `GET /health` - Dependency health check
- `POST /reconstructions` - Submit video for reconstruction
- `GET /reconstructions/{job_id}` - Get job status
- `GET /reconstructions/{job_id}/assets` - List generated assets
- `GET /reconstructions/{job_id}/assets/{asset_name}` - Download asset
- `GET /reconstructions/{job_id}/report` - Get quality report

## Usage Example

### Submit a video for reconstruction
```bash
curl -X POST http://localhost:8001/reconstructions \
  -F "video=@property_walkthrough.mp4" \
  -F "description=Living room walkthrough"
```

Response:
```json
{
  "job_id": "abc-123-def",
  "status": "pending",
  "current_stage": "pending",
  "progress": 0.0,
  "video_filename": "property_walkthrough.mp4"
}
```

### Check job status
```bash
curl http://localhost:8001/reconstructions/abc-123-def
```

### Download reconstructed assets
```bash
# List assets
curl http://localhost:8001/reconstructions/abc-123-def/assets

# Download specific asset
curl http://localhost:8001/reconstructions/abc-123-def/assets/splat.ply -o output.ply
```

## Workspace Structure

```
reconstruction/
├── input/           # Uploaded videos
├── frames/          # Extracted frames (per job)
├── colmap/          # COLMAP output (per job)
├── output/          # Generated assets (per job)
├── logs/            # Processing logs
└── jobs.json        # Job state persistence
```

## Input Requirements

### Video Specifications
- **Format**: MP4, MOV, AVI, MKV
- **Resolution**: Minimum 640x480
- **Duration**: Minimum 2 seconds
- **Frame count**: Minimum 30 frames
- **File size**: Maximum 2GB

### Capture Guidelines
For best reconstruction quality:
- **Slow, smooth camera movement** - fast motion causes blur
- **Good lighting** - avoid dark areas
- **Overlap between frames** - 60-80% overlap ideal
- **Full room coverage** - capture all corners and walls
- **Avoid reflections** - mirrors and windows can confuse reconstruction
- **Steady camera** - use gimbal or tripod if possible

### Known Limitations
- **Fast camera movement**: Causes motion blur, poor pose estimation
- **Low texture areas**: White walls, flat surfaces hard to reconstruct
- **Reflections**: Mirrors, windows can create artifacts
- **Repeated patterns**: Can cause registration errors
- **Insufficient overlap**: Missing connections between views

## Reconstruction Pipeline Stages

### 1. Video Validation
- Checks file format, size, duration
- Validates resolution and frame count
- Returns video metadata

### 2. Frame Extraction
- Extracts frames at target FPS (default 2 fps)
- Maximum 1000 frames per video
- Saves as JPEG with 95% quality

### 3. Quality Assessment
- Blur detection using Laplacian variance
- Brightness analysis
- Edge density estimation
- Returns quality score (0-100) and warnings

### 4. COLMAP Processing
- Feature extraction (SIFT)
- Exhaustive matching
- Sparse reconstruction
- Bundle adjustment
- Exports to COLMAP TXT format

### 5. Gaussian Splatting Training
- Uses nerfstudio's splatfacto method
- 30,000 iterations for good quality
- Generates PLY splat files
- Requires GPU (CUDA)

### 6. Asset Export
- Exports splat PLY files
- Generates preview renders
- Creates config files for loading

## Output Assets

### Visual Reconstruction
- **Gaussian Splat PLY files**: High-quality neural scene representation
- **Preview renders**: Sample views of the reconstructed scene
- **Config files**: For loading in viewers

### Navigation Geometry
**NOT CURRENTLY GENERATED** - This is a known limitation:
- Gaussian Splatting produces visual representation only
- No collision mesh or navigation geometry
- No room topology inference
- No floor plan generation

For game-like exploration, additional mesh reconstruction would be required.

## GPU Requirements

### Minimum
- NVIDIA GPU with 6GB+ VRAM
- CUDA 11.8+
- Compute capability 7.0+

### Recommended
- NVIDIA RTX 3060 or better
- 12GB+ VRAM
- CUDA 12.x

### CPU-only Mode
Video processing and COLMAP can run on CPU, but:
- Gaussian Splatting training requires GPU
- Training on CPU is impractically slow (days vs hours)
- The service will skip training and report the limitation

## Testing

### Run unit tests
```bash
cd reconstruction
pytest tests/
```

### Test with sample video
```bash
# Place test video in reconstruction/input/
curl -X POST http://localhost:8001/reconstructions \
  -F "video=@test_video.mp4"

# Monitor job status
curl http://localhost:8001/reconstructions/{job_id}
```

## Troubleshooting

### COLMAP not found
```
Error: COLMAP not installed
```
Install COLMAP per instructions above or set `COLMAP_SKIP=true` to skip this stage.

### CUDA not available
```
Error: CUDA not available
```
The service will skip Gaussian Splatting training. Video processing and COLMAP will still run.

### Out of memory
Reduce frame extraction rate or use a smaller video. The service limits extraction to 1000 frames maximum.

### Slow training
- Check GPU is being used: `nvidia-smi`
- Reduce max iterations in config
- Use smaller video

## Limitations

1. **No Navigation Geometry**: Gaussian Splatting does not generate walkable meshes
2. **GPU Required**: Training requires NVIDIA GPU with CUDA
3. **Processing Time**: Full reconstruction takes 30-60 minutes for typical videos
4. **Scene Coverage**: Unseen areas are not reconstructed
5. **Accuracy**: No measurement accuracy claimed
6. **Real-time**: Not real-time - async processing only

## Next Steps for Game-like Exploration

To achieve first-person navigation:

1. **Mesh Reconstruction**: Implement Poisson surface reconstruction or similar
2. **Collision Detection**: Generate collision geometry from mesh
3. **Room Segmentation**: Use semantic segmentation to identify rooms
4. **Floor Plan Generation**: Infer room topology and connections
5. **Navigation Graph**: Build waypoint graph for pathfinding

These would require additional research and development beyond the current pipeline.

## License

This reconstruction engine uses:
- COLMAP (BSD license)
- nerfstudio (Apache 2.0)
- OpenCV (Apache 2.0)
- PyTorch (BSD-style)
