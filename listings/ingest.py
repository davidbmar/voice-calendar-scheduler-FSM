"""Load apartment listings into the RAG service for search.

Usage:
    # Ingest sample data (default)
    python -m listings.ingest

    # Ingest Kaggle-imported data
    python -m listings.ingest --data listings/data/austin_apartments.json

    # Use a different RAG service URL
    RAG_SERVICE_URL=http://localhost:8100 python -m listings.ingest
"""

import argparse
import json
import os
import asyncio
from pathlib import Path

import httpx

from listings.schema import ApartmentListing

RAG_URL = os.environ.get("RAG_SERVICE_URL", "http://localhost:8000")

BATCH_SIZE = 50


async def ingest_listings(data_path: str | Path | None = None):
    """Load listings from JSON file and ingest into RAG service."""
    if data_path is None:
        data_path = Path(__file__).parent / "sample_data" / "apartments.json"
    else:
        data_path = Path(data_path)

    with open(data_path) as f:
        raw = json.load(f)

    listings = [ApartmentListing(**item) for item in raw]
    print(f"Loaded {len(listings)} listings from {data_path}")

    async with httpx.AsyncClient(timeout=60) as client:
        if len(listings) > BATCH_SIZE:
            await _ingest_batched(client, listings)
        else:
            await _ingest_single(client, listings)

    print(f"Done: {len(listings)} listings ingested into {RAG_URL}")


async def _ingest_single(client: httpx.AsyncClient, listings: list[ApartmentListing]):
    """Ingest listings one at a time (for small datasets)."""
    for listing in listings:
        payload = {
            "id": listing.id,
            "text": listing.to_searchable_text(),
            "metadata": listing.model_dump(),
        }
        resp = await client.post(f"{RAG_URL}/ingest", json=payload)
        resp.raise_for_status()
        print(f"  Ingested: {listing.address}")


async def _ingest_batched(client: httpx.AsyncClient, listings: list[ApartmentListing]):
    """Ingest listings in batches for efficiency."""
    for i in range(0, len(listings), BATCH_SIZE):
        batch = listings[i:i + BATCH_SIZE]
        records = [
            {
                "id": listing.id,
                "text": listing.to_searchable_text(),
                "metadata": listing.model_dump(),
            }
            for listing in batch
        ]
        resp = await client.post(
            f"{RAG_URL}/ingest/batch",
            json={"records": records},
        )
        resp.raise_for_status()
        data = resp.json()
        print(f"  Batch {i // BATCH_SIZE + 1}: {data['indexed']} indexed, {data['errors']} errors")


def main():
    parser = argparse.ArgumentParser(
        description="Ingest apartment listings into the RAG service",
        prog="python -m listings.ingest",
    )
    parser.add_argument(
        "--data",
        help="Path to listings JSON file (default: sample_data/apartments.json)",
    )
    args = parser.parse_args()
    asyncio.run(ingest_listings(args.data))


if __name__ == "__main__":
    main()
