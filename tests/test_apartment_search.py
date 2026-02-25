"""Tests for ApartmentListing schema and ApartmentSearchTool."""

import json
from pathlib import Path

import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine-repo"))

from listings.schema import ApartmentListing
from scheduling.tools.apartment_search import ApartmentSearchTool


# ── ApartmentListing tests ──────────────────────────────────────────


class TestApartmentListing:
    @pytest.fixture
    def sample_listing(self):
        return ApartmentListing(
            id="apt-001",
            address="100 Congress Ave",
            neighborhood="Downtown",
            bedrooms=2,
            bathrooms=1.5,
            sqft=900,
            rent=2100,
            available_date="2026-03-01",
            description="Modern downtown loft with skyline views",
            amenities=["pool", "gym"],
            pet_friendly=True,
            parking=True,
            laundry="in-unit",
        )

    def test_basic_fields(self, sample_listing):
        assert sample_listing.bedrooms == 2
        assert sample_listing.rent == 2100
        assert sample_listing.pet_friendly is True

    def test_defaults(self):
        listing = ApartmentListing(
            id="x",
            address="1 Main",
            neighborhood="Test",
            bedrooms=1,
            bathrooms=1.0,
            sqft=500,
            rent=1000,
            available_date="2026-03-01",
            description="Test",
        )
        assert listing.city == "Austin"
        assert listing.state == "TX"
        assert listing.amenities == []
        assert listing.pet_friendly is False

    def test_to_searchable_text(self, sample_listing):
        text = sample_listing.to_searchable_text()
        assert "2 bedroom" in text
        assert "Downtown" in text
        assert "$2100" in text or "2100" in text
        assert "Pet friendly" in text
        assert "pool" in text
        assert "in-unit" in text

    def test_model_dump_roundtrip(self, sample_listing):
        """Serializing and deserializing should preserve all fields."""
        data = sample_listing.model_dump()
        restored = ApartmentListing(**data)
        assert restored == sample_listing


class TestSampleData:
    def test_sample_data_loads(self):
        """The sample apartments.json should load into valid models."""
        data_path = Path(__file__).parent.parent / "listings" / "sample_data" / "apartments.json"
        with open(data_path) as f:
            raw = json.load(f)

        listings = [ApartmentListing(**item) for item in raw]
        assert len(listings) >= 8

        # Check diversity
        neighborhoods = {l.neighborhood for l in listings}
        assert len(neighborhoods) >= 4

        bedrooms = {l.bedrooms for l in listings}
        assert 1 in bedrooms
        assert 2 in bedrooms

        prices = [l.rent for l in listings]
        assert min(prices) >= 1000
        assert max(prices) <= 4000

    def test_all_have_searchable_text(self):
        data_path = Path(__file__).parent.parent / "listings" / "sample_data" / "apartments.json"
        with open(data_path) as f:
            raw = json.load(f)

        for item in raw:
            listing = ApartmentListing(**item)
            text = listing.to_searchable_text()
            assert len(text) > 50
            assert listing.address in text


# ── ApartmentSearchTool tests ───────────────────────────────────────


class TestApartmentSearchTool:
    def test_tool_schema(self):
        tool = ApartmentSearchTool()
        assert tool.name == "apartment_search"
        schema = tool.to_openai_schema()
        assert schema["type"] == "function"
        assert "query" in schema["function"]["parameters"]["properties"]

    @pytest.mark.asyncio
    async def test_empty_query(self):
        tool = ApartmentSearchTool()
        result = await tool.execute(query="")
        assert "provide a search query" in result.lower()

    @pytest.mark.asyncio
    async def test_connection_error(self):
        """Should return a friendly error when RAG service is down."""
        from unittest.mock import patch
        with patch("scheduling.tools.apartment_search.RAG_URL", "http://localhost:19999"):
            tool = ApartmentSearchTool()
            result = await tool.execute(query="2 bedroom downtown")
            assert "unavailable" in result.lower() or "error" in result.lower()

    def test_format_results(self):
        """Test the result formatter with mock RAG output."""
        results = [
            {
                "id": "apt-001",
                "text": "2 bedroom apartment at 100 Congress Ave",
                "score": 0.92,
                "metadata": {
                    "address": "100 Congress Ave",
                    "neighborhood": "Downtown",
                    "bedrooms": 2,
                    "bathrooms": 1,
                    "sqft": 900,
                    "rent": 2100,
                    "available_date": "2026-03-01",
                    "description": "Modern loft",
                    "pet_friendly": True,
                    "parking": True,
                    "amenities": ["pool", "gym"],
                    "laundry": "in-unit",
                    "contact_name": "Jane",
                    "contact_email": "jane@test.com",
                },
            },
        ]
        formatted = ApartmentSearchTool._format_results(results)
        assert "Option 1" in formatted
        assert "100 Congress Ave" in formatted
        assert "Downtown" in formatted
        assert "2100" in formatted
        assert "Pet friendly" in formatted
        assert "Jane" in formatted
