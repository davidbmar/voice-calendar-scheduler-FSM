"""CSV import pipeline — reads apartment CSVs, maps columns, outputs JSON.

Usage:
    python -m listings.import_csv data/apartments_for_rent.csv \
        --city Austin --state TX --limit 200 \
        --output listings/data/austin_apartments.json
"""

import argparse
import csv
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

csv.field_size_limit(10 * 1024 * 1024)  # 10 MB — some Kaggle rows have huge description fields

from listings.schema import ApartmentListing


DEFAULT_MAPPING = Path(__file__).parent / "data" / "column_mappings" / "kaggle_shashanks1202.json"

# Keywords for deriving fields not in the CSV
PARKING_KEYWORDS = [
    "parking", "garage", "carport", "car port", "covered parking",
    "off-street parking", "off street parking",
]
LAUNDRY_KEYWORDS = {
    "in-unit": ["in-unit", "in unit", "washer/dryer in unit", "w/d in unit",
                "washer dryer in unit", "in-unit laundry"],
    "on-site": ["on-site laundry", "on site laundry", "laundry facility",
                "laundry room", "shared laundry", "coin laundry"],
}


def load_mapping(mapping_path: str | Path) -> dict:
    """Load a column mapping config file."""
    with open(mapping_path) as f:
        return json.load(f)


def parse_amenities(raw: str) -> list[str]:
    """Parse amenities from a comma/pipe-separated string."""
    if not raw or raw.strip().lower() in ("", "nan", "none"):
        return []
    # Handle both comma and pipe separators
    for sep in ["|", ";"]:
        raw = raw.replace(sep, ",")
    return [a.strip() for a in raw.split(",") if a.strip()]


def parse_pet_friendly(raw: str) -> bool:
    """Parse pet-friendliness from various string formats."""
    if not raw or raw.strip().lower() in ("", "nan", "none", "no", "no pets"):
        return False
    lower = raw.strip().lower()
    return lower in ("yes", "cats", "dogs", "cats,dogs", "dogs,cats",
                     "cats allowed", "dogs allowed", "small dogs allowed",
                     "cats ok", "dogs ok") or "allowed" in lower


def derive_parking(amenities: list[str], description: str) -> bool:
    """Derive parking availability from amenities and description."""
    text = " ".join(amenities).lower() + " " + description.lower()
    return any(kw in text for kw in PARKING_KEYWORDS)


def derive_laundry(amenities: list[str], description: str) -> str:
    """Derive laundry type from amenities and description."""
    text = " ".join(amenities).lower() + " " + description.lower()
    for laundry_type, keywords in LAUNDRY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return laundry_type
    return ""


def generate_available_date() -> str:
    """Generate a random available date 2-8 weeks from today."""
    offset = random.randint(14, 56)
    return (date.today() + timedelta(days=offset)).isoformat()


def int_from_float(val: str) -> int | None:
    """Safely convert a string that might be float-formatted to int."""
    if not val or val.strip().lower() in ("", "nan", "none"):
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def float_from_str(val: str) -> float | None:
    """Safely convert a string to float."""
    if not val or val.strip().lower() in ("", "nan", "none"):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def apply_transforms(value: str, transform: str) -> object:
    """Apply a named transform to a raw CSV value."""
    transforms = {
        "int_from_float": int_from_float,
        "parse_list": parse_amenities,
        "parse_bool_pet": parse_pet_friendly,
    }
    fn = transforms.get(transform)
    if fn:
        return fn(value)
    return value


def row_to_listing(row: dict, mapping: dict, row_idx: int) -> ApartmentListing | None:
    """Convert a CSV row to an ApartmentListing using column mapping.

    Returns None if the row has missing required fields.
    """
    columns = mapping["columns"]
    transforms = mapping.get("transforms", {})

    def get(field: str) -> str:
        csv_col = columns.get(field, "")
        return row.get(csv_col, "").strip() if csv_col else ""

    def transformed(field: str) -> object:
        raw = get(field)
        if field in transforms:
            return apply_transforms(raw, transforms[field])
        return raw

    # Required fields
    address = get("address")
    bedrooms = transformed("bedrooms")
    rent = transformed("rent")

    if not address or bedrooms is None or rent is None:
        return None

    # Optional with defaults
    sqft = transformed("sqft") or 0
    bathrooms = float_from_str(get("bathrooms")) or 1.0
    description = get("description") or ""
    amenities = transformed("amenities") if "amenities" in columns else []
    pet_friendly = transformed("pet_friendly") if "pet_friendly" in columns else False

    # Derived fields
    parking = derive_parking(amenities, description)
    laundry = derive_laundry(amenities, description)

    listing_id = get("id") or f"csv-{row_idx:04d}"

    try:
        return ApartmentListing(
            id=listing_id,
            address=address,
            neighborhood="",
            city=get("city") or "Austin",
            state=get("state") or "TX",
            bedrooms=bedrooms,
            bathrooms=bathrooms,
            sqft=sqft,
            rent=rent,
            available_date=generate_available_date(),
            description=description,
            amenities=amenities,
            pet_friendly=pet_friendly,
            parking=parking,
            laundry=laundry,
        )
    except Exception:
        return None


def _detect_delimiter(csv_path: str | Path) -> str:
    """Detect CSV delimiter by inspecting the header line."""
    with open(csv_path, encoding="utf-8", errors="replace") as f:
        header = f.readline()
    # Count candidate delimiters in the header
    for delim in [";", "\t", "|"]:
        if header.count(delim) > header.count(","):
            return delim
    return ","


def import_csv(
    csv_path: str | Path,
    mapping: dict,
    city: str | None = None,
    state: str | None = None,
    limit: int | None = None,
) -> list[ApartmentListing]:
    """Import apartments from a CSV file.

    Args:
        csv_path: Path to the CSV file
        mapping: Column mapping config dict
        city: Filter by city name (case-insensitive)
        state: Filter by state (case-insensitive)
        limit: Maximum number of listings to return

    Returns:
        List of validated ApartmentListing objects
    """
    columns = mapping["columns"]
    city_col = columns.get("city", "")
    state_col = columns.get("state", "")

    delimiter = _detect_delimiter(csv_path)
    listings: list[ApartmentListing] = []

    with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        for idx, row in enumerate(reader):
            # Pre-filter by city/state before expensive transforms
            if city and city_col:
                row_city = row.get(city_col, "").strip()
                if row_city.lower() != city.lower():
                    continue
            if state and state_col:
                row_state = row.get(state_col, "").strip()
                if row_state.lower() != state.lower():
                    continue

            listing = row_to_listing(row, mapping, idx)
            if listing:
                listings.append(listing)
                if limit and len(listings) >= limit:
                    break

    return listings


def main():
    parser = argparse.ArgumentParser(
        description="Import apartment listings from CSV to JSON",
        prog="python -m listings.import_csv",
    )
    parser.add_argument("csv_path", help="Path to the input CSV file")
    parser.add_argument("--city", help="Filter by city (case-insensitive)")
    parser.add_argument("--state", help="Filter by state (case-insensitive)")
    parser.add_argument("--limit", type=int, help="Max number of listings")
    parser.add_argument("--output", "-o", help="Output JSON path (default: stdout)")
    parser.add_argument(
        "--mapping-file",
        default=str(DEFAULT_MAPPING),
        help="Column mapping JSON file (default: Kaggle shashanks1202)",
    )

    args = parser.parse_args()

    mapping = load_mapping(args.mapping_file)
    listings = import_csv(
        csv_path=args.csv_path,
        mapping=mapping,
        city=args.city,
        state=args.state,
        limit=args.limit,
    )

    data = [l.model_dump() for l in listings]

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(data, f, indent=2)
        print(f"Exported {len(data)} listings to {args.output}", file=sys.stderr)
    else:
        json.dump(data, sys.stdout, indent=2)
        print(f"\n# {len(data)} listings", file=sys.stderr)


if __name__ == "__main__":
    main()
