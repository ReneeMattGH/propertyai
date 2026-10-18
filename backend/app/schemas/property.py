from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class PropertyBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    address: str = Field(..., min_length=1, max_length=500)
    city: str = Field(..., min_length=1, max_length=100)
    state: str = Field(..., min_length=2, max_length=2)
    zip_code: str = Field(..., min_length=5, max_length=10)
    description: Optional[str] = None
    price: Optional[float] = Field(None, ge=0)
    bedrooms: Optional[int] = Field(None, ge=0)
    bathrooms: Optional[int] = Field(None, ge=0)
    square_feet: Optional[int] = Field(None, ge=0)
    property_type: Optional[str] = None
    listing_status: Optional[str] = "draft"


class PropertyCreate(PropertyBase):
    owner_id: str


class PropertyUpdate(BaseModel):
    owner_id: Optional[str] = None
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    address: Optional[str] = Field(None, min_length=1, max_length=500)
    city: Optional[str] = Field(None, min_length=1, max_length=100)
    state: Optional[str] = Field(None, min_length=2, max_length=2)
    zip_code: Optional[str] = Field(None, min_length=5, max_length=10)
    description: Optional[str] = None
    price: Optional[float] = Field(None, ge=0)
    bedrooms: Optional[int] = Field(None, ge=0)
    bathrooms: Optional[int] = Field(None, ge=0)
    square_feet: Optional[int] = Field(None, ge=0)
    property_type: Optional[str] = None
    listing_status: Optional[str] = None


class PropertyResponse(PropertyBase):
    id: str
    owner_id: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
