from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum


class JobStatus(str, Enum):
    UPLOADED = "uploaded"
    QUEUED = "queued"
    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    PUBLISHED = "published"
    FAILED = "failed"


class ProcessingJobBase(BaseModel):
    property_id: str
    media_id: str
    status: JobStatus = JobStatus.UPLOADED
    progress: Optional[str] = None
    error_message: Optional[str] = None
    reconstruction_config: Optional[str] = None


class ProcessingJobCreate(ProcessingJobBase):
    pass


class ProcessingJobUpdate(BaseModel):
    status: Optional[JobStatus] = None
    progress: Optional[str] = None
    error_message: Optional[str] = None
    output_assets: Optional[str] = None


class ProcessingJobResponse(ProcessingJobBase):
    id: str
    retry_count: str
    output_assets: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True
