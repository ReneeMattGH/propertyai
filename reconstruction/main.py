"""
RealEstAI Reconstruction Engine
Standalone service for video-to-3D property reconstruction
No Supabase dependency - uses local file storage and job management
"""
import sys
from pathlib import Path

# Add current directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import os
import uuid
import shutil
import json
from datetime import datetime

app = FastAPI(
    title="RealEstAI Reconstruction Engine",
    version="2.0.0",
    description="AI-powered property video to 3D reconstruction service"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
RECONSTRUCTION_DIR = Path(__file__).parent
INPUT_DIR = RECONSTRUCTION_DIR / "input"
FRAMES_DIR = RECONSTRUCTION_DIR / "frames"
COLMAP_DIR = RECONSTRUCTION_DIR / "colmap"
OUTPUT_DIR = RECONSTRUCTION_DIR / "output"
LOGS_DIR = RECONSTRUCTION_DIR / "logs"
JOBS_FILE = RECONSTRUCTION_DIR / "jobs.json"

# Create directories
for dir_path in [INPUT_DIR, FRAMES_DIR, COLMAP_DIR, OUTPUT_DIR, LOGS_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)

# Initialize jobs file
if not JOBS_FILE.exists():
    JOBS_FILE.write_text("{}")

# Pydantic models
class JobStatus(str):
    PENDING = "pending"
    VALIDATING = "validating"
    EXTRACTING_FRAMES = "extracting_frames"
    ASSESSING_QUALITY = "assessing_quality"
    COLMAP_PROCESSING = "colmap_processing"
    TRAINING = "training"
    EXPORTING = "exporting"
    COMPLETED = "completed"
    FAILED = "failed"

class ReconstructionRequest(BaseModel):
    job_id: Optional[str] = None
    description: Optional[str] = None

class JobResponse(BaseModel):
    job_id: str
    status: str
    current_stage: str
    progress: float
    created_at: str
    updated_at: str
    error: Optional[str] = None
    video_filename: Optional[str] = None
    frame_count: Optional[int] = None
    duration: Optional[float] = None
    resolution: Optional[str] = None
    diagnostics: Optional[Dict[str, Any]] = None
    assets: Optional[List[str]] = None


# Job management functions
def load_jobs() -> Dict[str, Dict]:
    """Load all jobs from JSON file."""
    try:
        return json.loads(JOBS_FILE.read_text())
    except:
        return {}

def save_jobs(jobs: Dict[str, Dict]):
    """Save all jobs to JSON file."""
    JOBS_FILE.write_text(json.dumps(jobs, indent=2, default=str))

def create_job(video_filename: str, description: Optional[str] = None) -> str:
    """Create a new reconstruction job."""
    jobs = load_jobs()
    job_id = str(uuid.uuid4())
    
    jobs[job_id] = {
        "id": job_id,
        "status": JobStatus.PENDING,
        "current_stage": "pending",
        "progress": 0.0,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "error": None,
        "video_filename": video_filename,
        "description": description,
        "frame_count": None,
        "duration": None,
        "resolution": None,
        "diagnostics": {},
        "assets": []
    }
    
    save_jobs(jobs)
    return job_id

def update_job(job_id: str, **kwargs):
    """Update job status and metadata."""
    jobs = load_jobs()
    if job_id in jobs:
        jobs[job_id].update(kwargs)
        jobs[job_id]["updated_at"] = datetime.now().isoformat()
        save_jobs(jobs)

def get_job(job_id: str) -> Optional[Dict]:
    """Get job by ID."""
    jobs = load_jobs()
    return jobs.get(job_id)


# Import reconstruction modules (deferred to avoid circular import)
video_processor = None
reconstruction_pipeline = None

def initialize_processors():
    global video_processor, reconstruction_pipeline
    from video_processor import VideoProcessor
    from reconstruction_pipeline import ReconstructionPipeline
    video_processor = VideoProcessor()
    reconstruction_pipeline = ReconstructionPipeline()


@app.get("/")
async def root():
    initialize_processors()
    return {
        "service": "RealEstAI Reconstruction Engine",
        "version": "2.0.0",
        "status": "operational",
        "gpu_available": reconstruction_pipeline.check_gpu_available() if reconstruction_pipeline else False
    }


@app.get("/health")
async def health_check():
    """Detailed health check of reconstruction dependencies."""
    initialize_processors()
    return {
        "status": "healthy",
        "dependencies": {
            "opencv": video_processor.check_opencv() if video_processor else False,
            "nerfstudio": reconstruction_pipeline.check_nerfstudio() if reconstruction_pipeline else False,
            "gpu": reconstruction_pipeline.check_gpu_available() if reconstruction_pipeline else False,
            "cuda": reconstruction_pipeline.check_cuda() if reconstruction_pipeline else False,
            "colmap": reconstruction_pipeline._check_colmap() if reconstruction_pipeline else False
        },
        "workspace": {
            "input_dir": str(INPUT_DIR),
            "frames_dir": str(FRAMES_DIR),
            "colmap_dir": str(COLMAP_DIR),
            "output_dir": str(OUTPUT_DIR),
            "logs_dir": str(LOGS_DIR)
        }
    }


@app.post("/reconstructions", response_model=JobResponse)
async def create_reconstruction(
    background_tasks: BackgroundTasks,
    video: UploadFile = File(...),
    description: Optional[str] = Form(None)
):
    """
    Submit a video for 3D reconstruction.
    Accepts MP4, MOV, and other supported video formats.
    """
    # Validate file
    if not video.filename:
        raise HTTPException(status_code=400, detail="No file provided")
    
    # Check file extension
    allowed_extensions = {'.mp4', '.mov', '.avi', '.mkv'}
    file_ext = Path(video.filename).suffix.lower()
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format. Allowed: {', '.join(allowed_extensions)}"
        )
    
    # Save uploaded video
    job_id = create_job(video.filename, description)
    video_path = INPUT_DIR / f"{job_id}_{video.filename}"
    
    try:
        with open(video_path, "wb") as buffer:
            shutil.copyfileobj(video.file, buffer)
    except Exception as e:
        update_job(job_id, status=JobStatus.FAILED, error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to save video: {str(e)}")
    
    # Start reconstruction in background
    initialize_processors()
    background_tasks.add_task(
        run_reconstruction_pipeline,
        job_id,
        str(video_path),
        description
    )
    
    job = get_job(job_id)
    # Map 'id' to 'job_id' for response
    job_response = job.copy()
    job_response['job_id'] = job_response.pop('id')
    return job_response


@app.get("/reconstructions/{job_id}", response_model=JobResponse)
async def get_reconstruction_status(job_id: str):
    """Get the status of a reconstruction job."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job_response = job.copy()
    job_response['job_id'] = job_response.pop('id')
    return job_response


@app.get("/reconstructions/{job_id}/assets")
async def list_reconstruction_assets(job_id: str):
    """List generated assets for a reconstruction job."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job["status"] != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail=f"Job not completed. Current status: {job['status']}"
        )
    
    job_output_dir = OUTPUT_DIR / job_id
    if not job_output_dir.exists():
        return {"assets": []}
    
    assets = []
    for file_path in job_output_dir.rglob("*"):
        if file_path.is_file():
            relative_path = file_path.relative_to(job_output_dir)
            assets.append({
                "name": str(relative_path),
                "size": file_path.stat().st_size,
                "type": file_path.suffix
            })
    
    return {"job_id": job_id, "assets": assets}


@app.get("/reconstructions/{job_id}/assets/{asset_name}")
async def get_reconstruction_asset(job_id: str, asset_name: str):
    """Download a specific reconstruction asset."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    asset_path = OUTPUT_DIR / job_id / asset_name
    if not asset_path.exists():
        raise HTTPException(status_code=404, detail="Asset not found")
    
    return FileResponse(asset_path)


@app.get("/reconstructions/{job_id}/report")
async def get_reconstruction_report(job_id: str):
    """Get detailed reconstruction quality and coverage report."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job["status"] != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail=f"Report not available. Current status: {job['status']}"
        )
    
    return {
        "job_id": job_id,
        "video_filename": job["video_filename"],
        "diagnostics": job.get("diagnostics", {}),
        "quality_report": job.get("quality_report", {}),
        "limitations": job.get("limitations", []),
        "navigation_available": job.get("navigation_available", False)
    }


async def run_reconstruction_pipeline(job_id: str, video_path: str, description: Optional[str]):
    """Run the full reconstruction pipeline in the background."""
    try:
        # Stage 1: Validate video
        update_job(job_id, status=JobStatus.VALIDATING, current_stage="validating_video", progress=5)
        video_info = await video_processor.validate_video(video_path)
        update_job(job_id, 
            frame_count=video_info.get("frame_count"),
            duration=video_info.get("duration"),
            resolution=video_info.get("resolution"),
            progress=10
        )
        
        # Stage 2: Extract frames
        update_job(job_id, status=JobStatus.EXTRACTING_FRAMES, current_stage="extracting_frames", progress=15)
        frames_info = await video_processor.extract_frames(job_id, video_path)
        update_job(job_id, progress=30)
        
        # Stage 3: Assess quality
        update_job(job_id, status=JobStatus.ASSESSING_QUALITY, current_stage="assessing_quality", progress=35)
        quality_report = await video_processor.assess_quality(job_id, frames_info)
        update_job(job_id, diagnostics={"quality": quality_report}, progress=40)
        
        # Stage 4: COLMAP processing
        update_job(job_id, status=JobStatus.COLMAP_PROCESSING, current_stage="colmap_processing", progress=45)
        colmap_result = await reconstruction_pipeline.run_colmap(job_id, frames_info)
        update_job(job_id, diagnostics={"colmap": colmap_result}, progress=60)
        
        # Stage 5: Training
        update_job(job_id, status=JobStatus.TRAINING, current_stage="training", progress=65)
        training_result = await reconstruction_pipeline.train_gaussian_splatting(job_id, colmap_result)
        update_job(job_id, diagnostics={"training": training_result}, progress=90)
        
        # Stage 6: Export
        update_job(job_id, status=JobStatus.EXPORTING, current_stage="exporting", progress=95)
        export_result = await reconstruction_pipeline.export_assets(job_id, training_result)
        update_job(job_id, 
            assets=export_result.get("assets", []),
            diagnostics={"export": export_result},
            progress=100
        )
        
        # Complete
        update_job(job_id, 
            status=JobStatus.COMPLETED, 
            current_stage="completed",
            quality_report=quality_report,
            navigation_available=export_result.get("navigation_available", False),
            limitations=export_result.get("limitations", [])
        )
        
    except Exception as e:
        import traceback
        error_detail = f"{str(e)}\n{traceback.format_exc()}"
        update_job(job_id, status=JobStatus.FAILED, error=error_detail)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
