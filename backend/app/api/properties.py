from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from typing import List
import uuid

from app.core.database import get_db
from app.schemas.property import PropertyCreate, PropertyUpdate, PropertyResponse
from app.models.property import Property

router = APIRouter(prefix="/properties", tags=["properties"])


@router.post("/", response_model=PropertyResponse, status_code=status.HTTP_201_CREATED)
async def create_property(
    property_data: PropertyCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new property listing."""
    property_id = str(uuid.uuid4())
    # In production, owner_id would come from auth token
    # For now, accept it in the request body
    owner_id = property_data.owner_id if hasattr(property_data, 'owner_id') else "default-owner"
    db_property = Property(
        id=property_id,
        owner_id=owner_id,
        **property_data.model_dump(exclude={'owner_id'} if hasattr(property_data, 'owner_id') else {})
    )
    db.add(db_property)
    await db.commit()
    await db.refresh(db_property)
    return db_property


@router.get("/", response_model=List[PropertyResponse])
async def list_properties(
    owner_id: str = None,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """List properties, optionally filtered by owner."""
    query = select(Property)
    if owner_id:
        query = query.where(Property.owner_id == owner_id)
    query = query.offset(skip).limit(limit).order_by(Property.created_at.desc())
    result = await db.execute(query)
    properties = result.scalars().all()
    return properties


@router.get("/{property_id}", response_model=PropertyResponse)
async def get_property(
    property_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get a specific property by ID."""
    query = select(Property).where(Property.id == property_id)
    result = await db.execute(query)
    property = result.scalar_one_or_none()
    
    if not property:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found"
        )
    return property


@router.put("/{property_id}", response_model=PropertyResponse)
async def update_property(
    property_id: str,
    property_update: PropertyUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a property."""
    query = select(Property).where(Property.id == property_id)
    result = await db.execute(query)
    db_property = result.scalar_one_or_none()
    
    if not db_property:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found"
        )
    
    update_data = property_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_property, field, value)
    
    await db.commit()
    await db.refresh(db_property)
    return db_property


@router.delete("/{property_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_property(
    property_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete a property."""
    query = select(Property).where(Property.id == property_id)
    result = await db.execute(query)
    db_property = result.scalar_one_or_none()
    
    if not db_property:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found"
        )
    
    await db.delete(db_property)
    await db.commit()
