"""Tests for the CSV import pipeline."""

import csv
import json
import io
import tempfile
from pathlib import Path

import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from listings.import_csv import (
    parse_amenities,
    parse_pet_friendly,
    derive_parking,
    derive_laundry,
    int_from_float,
    float_from_str,
    row_to_listing,
    import_csv,
    load_mapping,
)
from listings.schema import ApartmentListing


# ── Helper parsers ─────────────────────────────────────────────────


class TestParseAmenities:
    def test_comma_separated(self):
        assert parse_amenities("pool, gym, parking") == ["pool", "gym", "parking"]

    def test_pipe_separated(self):
        assert parse_amenities("pool|gym|parking") == ["pool", "gym", "parking"]

    def test_empty(self):
        assert parse_amenities("") == []
        assert parse_amenities("nan") == []
        assert parse_amenities("None") == []

    def test_strips_whitespace(self):
        result = parse_amenities("  pool  ,  gym  ")
        assert result == ["pool", "gym"]


class TestParsePetFriendly:
    def test_yes_values(self):
        assert parse_pet_friendly("Yes") is True
        assert parse_pet_friendly("Cats") is True
        assert parse_pet_friendly("Dogs") is True
        assert parse_pet_friendly("Cats,Dogs") is True

    def test_no_values(self):
        assert parse_pet_friendly("No") is False
        assert parse_pet_friendly("") is False
        assert parse_pet_friendly("nan") is False
        assert parse_pet_friendly("None") is False

    def test_allowed_substring(self):
        assert parse_pet_friendly("Pets allowed with deposit") is True


class TestDeriveParking:
    def test_from_amenities(self):
        assert derive_parking(["parking", "pool"], "") is True
        assert derive_parking(["garage", "gym"], "") is True

    def test_from_description(self):
        assert derive_parking([], "Covered parking included") is True

    def test_no_parking(self):
        assert derive_parking(["pool", "gym"], "Nice apartment") is False


class TestDeriveLaundry:
    def test_in_unit(self):
        assert derive_laundry(["washer/dryer in unit"], "") == "in-unit"
        assert derive_laundry([], "In-unit laundry included") == "in-unit"

    def test_on_site(self):
        assert derive_laundry(["laundry facility"], "") == "on-site"
        assert derive_laundry([], "On-site laundry available") == "on-site"

    def test_none(self):
        assert derive_laundry(["pool"], "Nice place") == ""


class TestNumericConversions:
    def test_int_from_float(self):
        assert int_from_float("3.0") == 3
        assert int_from_float("2") == 2
        assert int_from_float("") is None
        assert int_from_float("nan") is None

    def test_float_from_str(self):
        assert float_from_str("1.5") == 1.5
        assert float_from_str("2") == 2.0
        assert float_from_str("") is None


# ── CSV import pipeline ────────────────────────────────────────────


@pytest.fixture
def sample_mapping():
    """Minimal column mapping for tests."""
    return {
        "columns": {
            "id": "id",
            "address": "address",
            "city": "cityname",
            "state": "state",
            "bedrooms": "bedrooms",
            "bathrooms": "bathrooms",
            "sqft": "square_feet",
            "rent": "price",
            "description": "body",
            "amenities": "amenities",
            "pet_friendly": "pets_allowed",
        },
        "transforms": {
            "bedrooms": "int_from_float",
            "sqft": "int_from_float",
            "rent": "int_from_float",
            "amenities": "parse_list",
            "pet_friendly": "parse_bool_pet",
        },
    }


@pytest.fixture
def sample_csv(tmp_path):
    """Create a small test CSV file."""
    csv_path = tmp_path / "test_apartments.csv"
    rows = [
        {
            "id": "1", "address": "100 Congress Ave", "cityname": "Austin",
            "state": "TX", "bedrooms": "2.0", "bathrooms": "1.5",
            "square_feet": "900.0", "price": "2100.0",
            "body": "Modern downtown loft with garage parking",
            "amenities": "pool,gym,parking", "pets_allowed": "Yes",
        },
        {
            "id": "2", "address": "200 Main St", "cityname": "Austin",
            "state": "TX", "bedrooms": "1.0", "bathrooms": "1.0",
            "square_feet": "600.0", "price": "1200.0",
            "body": "Cozy studio with on-site laundry",
            "amenities": "laundry facility", "pets_allowed": "No",
        },
        {
            "id": "3", "address": "500 Elm St", "cityname": "Dallas",
            "state": "TX", "bedrooms": "3.0", "bathrooms": "2.0",
            "square_feet": "1200.0", "price": "1800.0",
            "body": "Spacious apartment", "amenities": "", "pets_allowed": "Cats",
        },
        {
            "id": "4", "address": "Bad Row", "cityname": "Austin",
            "state": "TX", "bedrooms": "", "bathrooms": "",
            "square_feet": "", "price": "",
            "body": "", "amenities": "", "pets_allowed": "",
        },
    ]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


class TestRowToListing:
    def test_valid_row(self, sample_mapping):
        row = {
            "id": "1", "address": "100 Main", "cityname": "Austin",
            "state": "TX", "bedrooms": "2.0", "bathrooms": "1.5",
            "square_feet": "900.0", "price": "1500.0",
            "body": "Nice place", "amenities": "pool,gym",
            "pets_allowed": "Yes",
        }
        listing = row_to_listing(row, sample_mapping, 0)
        assert listing is not None
        assert listing.bedrooms == 2
        assert listing.rent == 1500
        assert listing.pet_friendly is True
        assert listing.amenities == ["pool", "gym"]

    def test_missing_required_fields(self, sample_mapping):
        row = {
            "id": "x", "address": "", "cityname": "Austin",
            "state": "TX", "bedrooms": "", "bathrooms": "",
            "square_feet": "", "price": "",
            "body": "", "amenities": "", "pets_allowed": "",
        }
        assert row_to_listing(row, sample_mapping, 0) is None

    def test_derives_parking(self, sample_mapping):
        row = {
            "id": "1", "address": "100 Main", "cityname": "Austin",
            "state": "TX", "bedrooms": "2.0", "bathrooms": "1.0",
            "square_feet": "800.0", "price": "1500.0",
            "body": "Includes covered parking", "amenities": "",
            "pets_allowed": "No",
        }
        listing = row_to_listing(row, sample_mapping, 0)
        assert listing is not None
        assert listing.parking is True

    def test_derives_laundry(self, sample_mapping):
        row = {
            "id": "1", "address": "100 Main", "cityname": "Austin",
            "state": "TX", "bedrooms": "1.0", "bathrooms": "1.0",
            "square_feet": "500.0", "price": "1000.0",
            "body": "Has washer/dryer in unit", "amenities": "",
            "pets_allowed": "No",
        }
        listing = row_to_listing(row, sample_mapping, 0)
        assert listing is not None
        assert listing.laundry == "in-unit"


class TestImportCsv:
    def test_loads_all_valid_rows(self, sample_csv, sample_mapping):
        listings = import_csv(sample_csv, sample_mapping)
        # Row 4 (bad data) should be skipped
        assert len(listings) == 3

    def test_city_filter(self, sample_csv, sample_mapping):
        listings = import_csv(sample_csv, sample_mapping, city="Austin")
        assert len(listings) == 2  # 2 Austin rows (row 4 is invalid)
        assert all(l.city == "Austin" for l in listings)

    def test_state_filter(self, sample_csv, sample_mapping):
        listings = import_csv(sample_csv, sample_mapping, state="TX")
        assert len(listings) == 3  # All valid TX rows

    def test_limit(self, sample_csv, sample_mapping):
        listings = import_csv(sample_csv, sample_mapping, limit=1)
        assert len(listings) == 1

    def test_all_are_apartment_listings(self, sample_csv, sample_mapping):
        listings = import_csv(sample_csv, sample_mapping)
        for listing in listings:
            assert isinstance(listing, ApartmentListing)
            assert listing.to_searchable_text()


class TestColumnMapping:
    def test_default_mapping_loads(self):
        mapping_path = (
            Path(__file__).parent.parent
            / "listings" / "data" / "column_mappings" / "kaggle_shashanks1202.json"
        )
        mapping = load_mapping(mapping_path)
        assert "columns" in mapping
        assert "transforms" in mapping
        assert mapping["columns"]["city"] == "cityname"
