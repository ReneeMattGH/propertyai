"""Initial schema

Revision ID: 001
Revises: 
Create Date: 2026-10-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create users table
    op.create_table(
        'users',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('full_name', sa.String(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=False)
    op.create_index(op.f('ix_users_id'), 'users', ['id'], unique=False)

    # Create properties table
    op.create_table(
        'properties',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('owner_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('address', sa.String(), nullable=False),
        sa.Column('city', sa.String(), nullable=False),
        sa.Column('state', sa.String(), nullable=False),
        sa.Column('zip_code', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('price', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('bedrooms', sa.Integer(), nullable=True),
        sa.Column('bathrooms', sa.Integer(), nullable=True),
        sa.Column('square_feet', sa.Integer(), nullable=True),
        sa.Column('property_type', sa.String(), nullable=True),
        sa.Column('listing_status', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_properties_id'), 'properties', ['id'], unique=False)
    op.create_index(op.f('ix_properties_owner_id'), 'properties', ['owner_id'], unique=False)

    # Create property_media table
    op.create_table(
        'property_media',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('property_id', sa.String(), nullable=False),
        sa.Column('media_type', sa.Enum('video', 'image_360', 'image_standard', name='mediatype'), nullable=False),
        sa.Column('original_filename', sa.String(), nullable=False),
        sa.Column('storage_key', sa.String(), nullable=False),
        sa.Column('storage_url', sa.String(), nullable=True),
        sa.Column('file_size', sa.BigInteger(), nullable=False),
        sa.Column('mime_type', sa.String(), nullable=False),
        sa.Column('upload_status', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['property_id'], ['properties.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('storage_key')
    )
    op.create_index(op.f('ix_property_media_id'), 'property_media', ['id'], unique=False)
    op.create_index(op.f('ix_property_media_property_id'), 'property_media', ['property_id'], unique=False)

    # Create processing_jobs table
    op.create_table(
        'processing_jobs',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('property_id', sa.String(), nullable=False),
        sa.Column('media_id', sa.String(), nullable=False),
        sa.Column('status', sa.Enum('uploaded', 'queued', 'processing', 'needs_review', 'published', 'failed', name='jobstatus'), nullable=False),
        sa.Column('progress', sa.String(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('retry_count', sa.String(), nullable=True),
        sa.Column('reconstruction_config', sa.Text(), nullable=True),
        sa.Column('output_assets', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['property_id'], ['properties.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_processing_jobs_id'), 'processing_jobs', ['id'], unique=False)
    op.create_index(op.f('ix_processing_jobs_property_id'), 'processing_jobs', ['property_id'], unique=False)

    # Create buyer_inquiries table
    op.create_table(
        'buyer_inquiries',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('property_id', sa.String(), nullable=False),
        sa.Column('buyer_name', sa.String(), nullable=False),
        sa.Column('buyer_email', sa.String(), nullable=False),
        sa.Column('buyer_phone', sa.String(), nullable=True),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('is_contacted', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['property_id'], ['properties.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_buyer_inquiries_id'), 'buyer_inquiries', ['id'], unique=False)
    op.create_index(op.f('ix_buyer_inquiries_property_id'), 'buyer_inquiries', ['property_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_buyer_inquiries_property_id'), table_name='buyer_inquiries')
    op.drop_index(op.f('ix_buyer_inquiries_id'), table_name='buyer_inquiries')
    op.drop_table('buyer_inquiries')
    
    op.drop_index(op.f('ix_processing_jobs_property_id'), table_name='processing_jobs')
    op.drop_index(op.f('ix_processing_jobs_id'), table_name='processing_jobs')
    op.drop_table('processing_jobs')
    
    op.drop_index(op.f('ix_property_media_property_id'), table_name='property_media')
    op.drop_index(op.f('ix_property_media_id'), table_name='property_media')
    op.drop_table('property_media')
    
    op.drop_index(op.f('ix_properties_owner_id'), table_name='properties')
    op.drop_index(op.f('ix_properties_id'), table_name='properties')
    op.drop_table('properties')
    
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_index(op.f('ix_users_id'), table_name='users')
    op.drop_table('users')
