from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Enum
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import enum
from app.core.database import Base


class JobStatus(str, enum.Enum):
    UPLOADED = "uploaded"
    QUEUED = "queued"
    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    PUBLISHED = "published"
    FAILED = "failed"


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id = Column(String, primary_key=True)
    property_id = Column(String, ForeignKey("properties.id"), nullable=False, index=True)
    media_id = Column(String, ForeignKey("property_media.id"), nullable=False)
    status = Column(Enum(JobStatus), default=JobStatus.UPLOADED, nullable=False)
    progress = Column(String, nullable=True)  # Optional progress percentage or status message
    error_message = Column(Text, nullable=True)
    retry_count = Column(String, default="0")
    reconstruction_config = Column(Text, nullable=True)  # JSON config for reconstruction pipeline
    output_assets = Column(Text, nullable=True)  # JSON with URLs to generated assets
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    property = relationship("Property", back_populates="processing_jobs")
