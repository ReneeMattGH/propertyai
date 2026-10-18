from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
import uuid
import os

from app.core.database import get_db
from app.core.config import settings
from app.schemas.upload import PropertyMediaCreate, PropertyMediaResponse, UploadResponse
from app.models.property_media import PropertyMedia, MediaType
from app.models.property import Property

router = APIRouter(prefix="/upload", tags=["upload"])


@router.post("/property-media", response_model=UploadResponse)
async def upload_property_media(
    property_id: str = Form(...),
    media_type: str = Form(...),
    file: UploadFile = File(...),
    owner_id: str = Form(...),  # Will be extracted from auth token
    db: AsyncSession = Depends(get_db),
):
    """Upload property media (video or images)."""
    
    # Validate property exists and belongs to owner
    property_query = select(Property).where(Property.id == property_id)
    property_result = await db.execute(property_query)
    db_property = property_result.scalar_one_or_none()
    
    if not db_property:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found"
        )
    
    if db_property.owner_id != owner_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to upload to this property"
        )
    
    # Validate media type
    try:
        media_type_enum = MediaType(media_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid media type. Must be one of: {[m.value for m in MediaType]}"
        )
    
    # Validate file type
    allowed_mime_types = {
        MediaType.VIDEO: ["video/mp4", "video/quicktime", "video/x-msvideo"],
        MediaType.IMAGE_360: ["image/jpeg", "image/png"],
        MediaType.IMAGE_STANDARD: ["image/jpeg", "image/png"],
    }
    
    if file.content_type not in allowed_mime_types[media_type_enum]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type for {media_type}. Allowed: {allowed_mime_types[media_type_enum]}"
        )
    
    # Read file content and validate size (max 500MB)
    MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB
    file_content = await file.read()
    file_size = len(file_content)
    
    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large. Maximum size is 500MB"
        )
    
    # Generate storage key
    storage_key = f"properties/{property_id}/{str(uuid.uuid4())}_{file.filename}"
    
    # In production, upload to Supabase Storage or S3
    # For now, store locally in a temporary directory
    upload_dir = "uploads"
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, storage_key.replace("/", "_"))
    
    with open(file_path, "wb") as f:
        f.write(file_content)
    
    # Create media record
    media_id = str(uuid.uuid4())
    db_media = PropertyMedia(
        id=media_id,
        property_id=property_id,
        media_type=media_type_enum,
        original_filename=file.filename,
        storage_key=storage_key,
        file_size=file_size,
        mime_type=file.content_type,
        upload_status="completed"
    )
    
    db.add(db_media)
    await db.commit()
    await db.refresh(db_media)
    
    return UploadResponse(
        media_id=media_id,
        upload_url=None,  # In production, return presigned URL
        storage_key=storage_key,
        message="File uploaded successfully"
    )


@router.get("/media/{media_id}", response_model=PropertyMediaResponse)
async def get_media(
    media_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get media metadata."""
    query = select(PropertyMedia).where(PropertyMedia.id == media_id)
    result = await db.execute(query)
    media = result.scalar_one_or_none()
    
    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media not found"
        )
    
    return media
