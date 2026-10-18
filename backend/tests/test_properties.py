import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.property import Property
from app.models.user import User
import uuid


@pytest.mark.asyncio
async def test_create_property(client: AsyncClient, db_session: AsyncSession):
    """Test creating a new property."""
    property_data = {
        "owner_id": "test-user-id",
        "name": "Test Property",
        "address": "123 Test St",
        "city": "Test City",
        "state": "TC",
        "zip_code": "12345",
        "description": "A test property",
        "price": 250000.00,
        "bedrooms": 3,
        "bathrooms": 2,
        "square_feet": 1500,
        "property_type": "house",
        "listing_status": "draft"
    }
    
    response = await client.post("/properties/", json=property_data)
    
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == property_data["name"]
    assert data["address"] == property_data["address"]
    assert "id" in data


@pytest.mark.asyncio
async def test_list_properties(client: AsyncClient, db_session: AsyncSession):
    """Test listing properties."""
    response = await client.get("/properties/")
    
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_get_property(client: AsyncClient, db_session: AsyncSession):
    """Test getting a specific property."""
    # First create a property
    property_data = {
        "owner_id": "test-user-id",
        "name": "Test Property",
        "address": "123 Test St",
        "city": "Test City",
        "state": "TC",
        "zip_code": "12345"
    }
    
    create_response = await client.post("/properties/", json=property_data)
    property_id = create_response.json()["id"]
    
    # Then get it
    response = await client.get(f"/properties/{property_id}")
    
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == property_id
    assert data["name"] == property_data["name"]


@pytest.mark.asyncio
async def test_update_property(client: AsyncClient, db_session: AsyncSession):
    """Test updating a property."""
    # Create a property
    property_data = {
        "owner_id": "test-user-id",
        "name": "Test Property",
        "address": "123 Test St",
        "city": "Test City",
        "state": "TC",
        "zip_code": "12345"
    }
    
    create_response = await client.post("/properties/", json=property_data)
    property_id = create_response.json()["id"]
    
    # Update it
    update_data = {
        "name": "Updated Property Name",
        "price": 300000.00
    }
    
    response = await client.put(f"/properties/{property_id}", json=update_data)
    
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == update_data["name"]
    assert data["price"] == update_data["price"]


@pytest.mark.asyncio
async def test_delete_property(client: AsyncClient, db_session: AsyncSession):
    """Test deleting a property."""
    # Create a property
    property_data = {
        "owner_id": "test-user-id",
        "name": "Test Property",
        "address": "123 Test St",
        "city": "Test City",
        "state": "TC",
        "zip_code": "12345"
    }
    
    create_response = await client.post("/properties/", json=property_data)
    property_id = create_response.json()["id"]
    
    # Delete it
    response = await client.delete(f"/properties/{property_id}")
    
    assert response.status_code == 204
    
    # Verify it's gone
    get_response = await client.get(f"/properties/{property_id}")
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_get_nonexistent_property(client: AsyncClient):
    """Test getting a property that doesn't exist."""
    fake_id = str(uuid.uuid4())
    response = await client.get(f"/properties/{fake_id}")
    
    assert response.status_code == 404
