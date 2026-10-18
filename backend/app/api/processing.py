from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
import uuid

from app.core.database import get_db
from app.schemas.processing_job import ProcessingJobCreate, ProcessingJobUpdate, ProcessingJobResponse, JobStatus
from app.models.processing_job import ProcessingJob
from app.models.property_media import PropertyMedia

router = APIRouter(prefix="/processing", tags=["processing"])


@router.post("/jobs", response_model=ProcessingJobResponse, status_code=status.HTTP_201_CREATED)
async def create_processing_job(
    job_data: ProcessingJobCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new processing job for 3D reconstruction."""
    
    # Validate media exists
    media_query = select(PropertyMedia).where(PropertyMedia.id == job_data.media_id)
    media_result = await db.execute(media_query)
    media = media_result.scalar_one_or_none()
    
    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media not found"
        )
    
    # Create job
    job_id = str(uuid.uuid4())
    db_job = ProcessingJob(
        id=job_id,
        **job_data.model_dump()
    )
    db.add(db_job)
    await db.commit()
    await db.refresh(db_job)
    
    # In production, enqueue job to worker queue here
    # For now, job remains in 'uploaded' state
    
    return db_job


@router.get("/jobs/{job_id}", response_model=ProcessingJobResponse)
async def get_processing_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get processing job status."""
    query = select(ProcessingJob).where(ProcessingJob.id == job_id)
    result = await db.execute(query)
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Processing job not found"
        )
    
    return job


@router.get("/jobs", response_model=List[ProcessingJobResponse])
async def list_processing_jobs(
    property_id: str = None,
    status: JobStatus = None,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """List processing jobs, optionally filtered."""
    query = select(ProcessingJob)
    
    if property_id:
        query = query.where(ProcessingJob.property_id == property_id)
    if status:
        query = query.where(ProcessingJob.status == status)
    
    query = query.offset(skip).limit(limit).order_by(ProcessingJob.created_at.desc())
    result = await db.execute(query)
    jobs = result.scalars().all()
    return jobs


@router.put("/jobs/{job_id}", response_model=ProcessingJobResponse)
async def update_processing_job(
    job_id: str,
    job_update: ProcessingJobUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update processing job status (called by worker)."""
    query = select(ProcessingJob).where(ProcessingJob.id == job_id)
    result = await db.execute(query)
    db_job = result.scalar_one_or_none()
    
    if not db_job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Processing job not found"
        )
    
    update_data = job_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_job, field, value)
    
    await db.commit()
    await db.refresh(db_job)
    return db_job
