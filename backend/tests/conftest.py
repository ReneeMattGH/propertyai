import pytest
import sys
import os
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool
from typing import AsyncGenerator, Generator

# Set test environment variables before importing
os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///:memory:'
os.environ['SUPABASE_URL'] = 'test_url'
os.environ['SUPABASE_ANON_KEY'] = 'test_anon_key'
os.environ['SUPABASE_SERVICE_ROLE_KEY'] = 'test_service_key'
os.environ['SECRET_KEY'] = 'test_secret_key'
os.environ['DEBUG'] = 'true'

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from main import app
from app.core.database import Base, get_db
from app.core.config import settings


# Test database URL (in-memory SQLite for tests)
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Create a test database session."""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    async_session = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    
    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    session = async_session()
    
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


@pytest.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[any, None]:
    """Create a test client with database dependency override."""
    from httpx import AsyncClient
    
    async def override_get_db():
        yield db_session
    
    app.dependency_overrides[get_db] = override_get_db
    
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    
    app.dependency_overrides.clear()
