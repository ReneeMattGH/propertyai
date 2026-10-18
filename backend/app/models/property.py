from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Numeric, Integer
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.core.database import Base


class Property(Base):
    __tablename__ = "properties"

    id = Column(String, primary_key=True)
    owner_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    address = Column(String, nullable=False)
    city = Column(String, nullable=False)
    state = Column(String, nullable=False)
    zip_code = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    price = Column(Numeric(12, 2), nullable=True)
    bedrooms = Column(Integer, nullable=True)
    bathrooms = Column(Integer, nullable=True)
    square_feet = Column(Integer, nullable=True)
    property_type = Column(String, nullable=True)  # house, condo, apartment, etc.
    listing_status = Column(String, default="draft")  # draft, active, sold, rented
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    media = relationship("PropertyMedia", back_populates="property", cascade="all, delete-orphan")
    processing_jobs = relationship("ProcessingJob", back_populates="property", cascade="all, delete-orphan")
    inquiries = relationship("BuyerInquiry", back_populates="property", cascade="all, delete-orphan")
