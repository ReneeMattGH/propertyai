from sqlalchemy import Column, String, BigInteger, DateTime, ForeignKey, Enum
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import enum
from app.core.database import Base


class MediaType(str, enum.Enum):
    VIDEO = "video"
    IMAGE_360 = "image_360"
    IMAGE_STANDARD = "image_standard"


class PropertyMedia(Base):
    __tablename__ = "property_media"

    id = Column(String, primary_key=True)
    property_id = Column(String, ForeignKey("properties.id"), nullable=False, index=True)
    media_type = Column(Enum(MediaType), nullable=False)
    original_filename = Column(String, nullable=False)
    storage_key = Column(String, nullable=False, unique=True)
    storage_url = Column(String, nullable=True)  # Signed URL
    file_size = Column(BigInteger, nullable=False)
    mime_type = Column(String, nullable=False)
    upload_status = Column(String, default="pending")  # pending, completed, failed
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    property = relationship("Property", back_populates="media")
