from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum


class MediaType(str, Enum):
    VIDEO = "video"
    IMAGE_360 = "image_360"
    IMAGE_STANDARD = "image_standard"


class PropertyMediaBase(BaseModel):
    property_id: str
    media_type: MediaType
    original_filename: str
    file_size: int = Field(..., gt=0)
    mime_type: str


class PropertyMediaCreate(PropertyMediaBase):
    storage_key: str


class PropertyMediaResponse(PropertyMediaBase):
    id: str
    storage_key: str
    storage_url: Optional[str] = None
    upload_status: str
    created_at: datetime

    class Config:
        from_attributes = True


class UploadResponse(BaseModel):
    media_id: str
    upload_url: Optional[str] = None
    storage_key: str
    message: str
