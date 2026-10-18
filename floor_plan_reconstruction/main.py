"""
RealEstAI Floor Plan Reconstruction Engine
Standalone service for floor-plan-and-photo-to-3D walkthrough generation
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
    title="RealEstAI Floor Plan Reconstruction Engine",
    version="3.0.0",
    description="Floor-plan-and-photo-to-3D walkthrough generation service"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001", "http://127.0.0.1:3000", "http://127.0.0.1:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
RECONSTRUCTION_DIR = Path(__file__).parent
INPUT_DIR = RECONSTRUCTION_DIR / "input"
FLOOR_PLANS_DIR = RECONSTRUCTION_DIR / "floor_plans"
PHOTOS_DIR = RECONSTRUCTION_DIR / "photos"
GEOMETRY_DIR = RECONSTRUCTION_DIR / "geometry"
OUTPUT_DIR = RECONSTRUCTION_DIR / "output"
LOGS_DIR = RECONSTRUCTION_DIR / "logs"
JOBS_FILE = RECONSTRUCTION_DIR / "jobs.json"

# Create directories
for dir_path in [INPUT_DIR, FLOOR_PLANS_DIR, PHOTOS_DIR, GEOMETRY_DIR, OUTPUT_DIR, LOGS_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)

# Initialize jobs file
if not JOBS_FILE.exists():
    JOBS_FILE.write_text("{}")

# Pydantic models
class JobStatus(str):
    CREATED = "created"
    VALIDATING = "validating"
    PARSING_FLOOR_PLAN = "parsing_floor_plan"
    ANALYZING_PHOTOS = "analyzing_photos"
    BUILDING_GEOMETRY = "building_geometry"
    RECONSTRUCTING_APPEARANCE = "reconstructing_appearance"
    VALIDATING_SCENE = "validating_scene"
    READY_FOR_REVIEW = "ready_for_review"
    COMPLETED = "completed"
    FAILED = "failed"

class ReconstructionRequest(BaseModel):
    project_name: str
    num_floors: int = 1
    known_width: Optional[float] = None
    known_length: Optional[float] = None

class JobResponse(BaseModel):
    job_id: str
    status: str
    current_stage: str
    progress: float
    created_at: str
    updated_at: str
    error: Optional[str] = None
    project_name: Optional[str] = None
    floor_plan_filename: Optional[str] = None
    photo_count: Optional[int] = None
    num_floors: Optional[int] = None
    known_dimensions: Optional[Dict[str, float]] = None
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

def create_job(project_name: str, floor_plan_filename: str, photo_count: int, 
               num_floors: int, known_width: Optional[float], known_length: Optional[float]) -> str:
    """Create a new reconstruction job."""
    jobs = load_jobs()
    job_id = str(uuid.uuid4())
    
    known_dimensions = {}
    if known_width:
        known_dimensions["width_meters"] = known_width
    if known_length:
        known_dimensions["length_meters"] = known_length
    
    jobs[job_id] = {
        "id": job_id,
        "status": JobStatus.CREATED,
        "current_stage": "created",
        "progress": 0.0,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "error": None,
        "project_name": project_name,
        "floor_plan_filename": floor_plan_filename,
        "photo_count": photo_count,
        "num_floors": num_floors,
        "known_dimensions": known_dimensions,
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


# Import reconstruction modules (deferred)
floor_plan_parser = None
photo_analyzer = None
geometry_builder = None
scene_validator = None

def initialize_processors():
    global floor_plan_parser, photo_analyzer, geometry_builder, scene_validator
    from improved_floor_plan_parser import ImprovedFloorPlanParser
    from photo_analyzer import PhotoAnalyzer
    from geometry_builder import GeometryBuilder
    from scene_validator import SceneValidator
    floor_plan_parser = ImprovedFloorPlanParser()
    photo_analyzer = PhotoAnalyzer()
    geometry_builder = GeometryBuilder()
    scene_validator = SceneValidator()


@app.get("/")
async def root():
    initialize_processors()
    return {
        "service": "RealEstAI Floor Plan Reconstruction Engine",
        "version": "3.0.0",
        "status": "operational"
    }


@app.get("/health")
async def health_check():
    """Detailed health check of reconstruction dependencies."""
    initialize_processors()
    return {
        "status": "healthy",
        "dependencies": {
            "opencv": floor_plan_parser.check_opencv() if floor_plan_parser else False,
            "open3d": geometry_builder.check_open3d() if geometry_builder else False,
            "pillow": floor_plan_parser.check_pillow() if floor_plan_parser else False
        },
        "workspace": {
            "input_dir": str(INPUT_DIR),
            "floor_plans_dir": str(FLOOR_PLANS_DIR),
            "photos_dir": str(PHOTOS_DIR),
            "geometry_dir": str(GEOMETRY_DIR),
            "output_dir": str(OUTPUT_DIR),
            "logs_dir": str(LOGS_DIR)
        }
    }


@app.post("/reconstructions", response_model=JobResponse)
async def create_reconstruction(
    background_tasks: BackgroundTasks,
    project_name: str = Form(...),
    floor_plan: UploadFile = File(...),
    num_floors: int = Form(1),
    known_width: Optional[float] = Form(None),
    known_length: Optional[float] = Form(None),
    photos: List[UploadFile] = File([])
):
    """
    Submit a floor plan and photos for 3D reconstruction.
    """
    # Validate inputs
    if not project_name:
        raise HTTPException(status_code=400, detail="Project name is required")
    
    if not floor_plan:
        raise HTTPException(status_code=400, detail="Floor plan is required")
    
    if len(photos) == 0:
        raise HTTPException(status_code=400, detail="At least one photo is required")
    
    # Validate floor plan format
    allowed_extensions = {'.png', '.jpg', '.jpeg', '.pdf'}
    file_ext = Path(floor_plan.filename).suffix.lower()
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported floor plan format. Allowed: {', '.join(allowed_extensions)}"
        )
    
    # Validate floor plan size
    floor_plan.file.seek(0, 2)  # Seek to end
    floor_plan_size = floor_plan.file.tell()
    floor_plan.file.seek(0)  # Seek back to start
    if floor_plan_size > 10 * 1024 * 1024:  # 10MB
        raise HTTPException(
            status_code=400,
            detail="Floor plan too large. Maximum 10MB"
        )
    
    # Validate photo formats
    for photo in photos:
        photo_ext = Path(photo.filename).suffix.lower()
        if photo_ext not in {'.png', '.jpg', '.jpeg'}:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported photo format: {photo.filename}. Allowed: PNG, JPG, JPEG"
            )
        
        # Validate photo size
        photo.file.seek(0, 2)
        photo_size = photo.file.tell()
        photo.file.seek(0)
        if photo_size > 10 * 1024 * 1024:  # 10MB
            raise HTTPException(
                status_code=400,
                detail=f"Photo too large: {photo.filename}. Maximum 10MB per photo"
            )
    
    # Save files
    job_id = create_job(
        project_name,
        floor_plan.filename,
        len(photos),
        num_floors,
        known_width,
        known_length
    )
    
    job_dir = INPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    
    # Save floor plan
    floor_plan_path = job_dir / floor_plan.filename
    try:
        with open(floor_plan_path, "wb") as buffer:
            shutil.copyfileobj(floor_plan.file, buffer)
    except Exception as e:
        update_job(job_id, status=JobStatus.FAILED, error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to save floor plan: {str(e)}")
    
    # Save photos
    photo_paths = []
    for i, photo in enumerate(photos):
        photo_path = job_dir / f"photo_{i}_{photo.filename}"
        try:
            with open(photo_path, "wb") as buffer:
                shutil.copyfileobj(photo.file, buffer)
            photo_paths.append(str(photo_path))
            print(f"Saved photo {i}: {photo.filename} to {photo_path}")
        except Exception as e:
            update_job(job_id, status=JobStatus.FAILED, error=str(e))
            raise HTTPException(status_code=500, detail=f"Failed to save photo: {str(e)}")
    
    print(f"Job {job_id} created with {len(photos)} photos. Starting reconstruction...")
    
    # Start reconstruction in background
    initialize_processors()
    background_tasks.add_task(
        run_reconstruction_pipeline,
        job_id,
        str(floor_plan_path),
        photo_paths,
        num_floors,
        known_width,
        known_length
    )
    
    job = get_job(job_id)
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
    """Get detailed reconstruction quality and diagnostics report."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return {
        "job_id": job_id,
        "project_name": job.get("project_name"),
        "floor_plan_filename": job.get("floor_plan_filename"),
        "photo_count": job.get("photo_count"),
        "diagnostics": job.get("diagnostics", {}),
        "limitations": job.get("limitations", []),
        "navigation_available": job.get("navigation_available", False),
        "geometry_valid": job.get("geometry_valid", False),
        "scale_verified": job.get("scale_verified", False)
    }


async def run_reconstruction_pipeline(
    job_id: str,
    floor_plan_path: str,
    photo_paths: List[str],
    num_floors: int,
    known_width: Optional[float],
    known_length: Optional[float]
):
    """Run the full reconstruction pipeline in the background."""
    try:
        print(f"[{job_id}] Starting reconstruction pipeline...")
        
        # Stage 1: Validate inputs
        print(f"[{job_id}] Stage 1: Validating inputs")
        update_job(job_id, status=JobStatus.VALIDATING, current_stage="validating_inputs", progress=5)
        validation_result = await floor_plan_parser.validate_inputs(floor_plan_path, photo_paths)
        update_job(job_id, diagnostics={"validation": validation_result}, progress=10)
        print(f"[{job_id}] Validation complete: {validation_result}")
        
        # Stage 2: Parse floor plan
        print(f"[{job_id}] Stage 2: Parsing floor plan")
        update_job(job_id, status=JobStatus.PARSING_FLOOR_PLAN, current_stage="parsing_floor_plan", progress=15)
        floor_plan_result = await floor_plan_parser.parse_floor_plan(
            job_id, floor_plan_path, known_width, known_length
        )
        update_job(job_id, diagnostics={"floor_plan": floor_plan_result}, progress=30)
        print(f"[{job_id}] Floor plan parsed: {floor_plan_result.get('rooms_count', 0)} rooms, {floor_plan_result.get('doorways_count', 0)} doorways")
        
        # Stage 3: Analyze photos
        print(f"[{job_id}] Stage 3: Analyzing photos")
        update_job(job_id, status=JobStatus.ANALYZING_PHOTOS, current_stage="analyzing_photos", progress=35)
        photo_result = await photo_analyzer.analyze_photos(job_id, photo_paths, floor_plan_result)
        update_job(job_id, diagnostics={"photos": photo_result}, progress=50)
        print(f"[{job_id}] Photos analyzed: {photo_result.get('analyzed_photos', 0)} photos")
        
        # Stage 4: Build geometry
        print(f"[{job_id}] Stage 4: Building geometry")
        update_job(job_id, status=JobStatus.BUILDING_GEOMETRY, current_stage="building_geometry", progress=55)
        geometry_result = await geometry_builder.build_geometry(
            job_id, floor_plan_result, photo_result, num_floors, known_width, known_length
        )
        update_job(job_id, diagnostics={"geometry": geometry_result}, progress=75)
        print(f"[{job_id}] Geometry built: {geometry_result.get('vertices_count', 0)} vertices")
        
        # Stage 5: Reconstruct appearance
        print(f"[{job_id}] Stage 5: Reconstructing appearance")
        update_job(job_id, status=JobStatus.RECONSTRUCTING_APPEARANCE, current_stage="reconstructing_appearance", progress=80)
        appearance_result = await geometry_builder.apply_textures(job_id, geometry_result, photo_result)
        update_job(job_id, diagnostics={"appearance": appearance_result}, progress=90)
        print(f"[{job_id}] Appearance reconstruction complete")
        
        # Stage 6: Validate scene
        print(f"[{job_id}] Stage 6: Validating scene")
        update_job(job_id, status=JobStatus.VALIDATING_SCENE, current_stage="validating_scene", progress=95)
        validation_result = await scene_validator.validate_scene(job_id, geometry_result)
        update_job(job_id, 
            diagnostics={"validation": validation_result},
            geometry_valid=validation_result.get("geometry_valid", False),
            navigation_available=validation_result.get("navigation_available", False),
            scale_verified=validation_result.get("scale_verified", False),
            progress=98
        )
        print(f"[{job_id}] Scene validation: geometry_valid={validation_result.get('geometry_valid')}")
        
        # Stage 7: Export
        print(f"[{job_id}] Stage 7: Exporting scene")
        update_job(job_id, status=JobStatus.READY_FOR_REVIEW, current_stage="exporting", progress=99)
        export_result = await geometry_builder.export_scene(job_id, geometry_result)
        update_job(job_id, 
            assets=export_result.get("assets", []),
            limitations=export_result.get("limitations", []),
            progress=100
        )
        print(f"[{job_id}] Export complete: {export_result.get('assets', [])}")
        
        # Complete
        print(f"[{job_id}] ✓ Reconstruction completed successfully")
        update_job(job_id, 
            status=JobStatus.COMPLETED, 
            current_stage="completed"
        )
        
    except Exception as e:
        import traceback
        error_detail = f"{str(e)}\n{traceback.format_exc()}"
        print(f"[{job_id}] ✗ Reconstruction failed: {error_detail}")
        update_job(job_id, status=JobStatus.FAILED, error=error_detail)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
