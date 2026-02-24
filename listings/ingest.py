"""Load apartment listings into the RAG service for search."""

import json
import os
import asyncio
from pathlib import Path

import httpx

from listings.schema import ApartmentListing

RAG_URL = os.environ.get("RAG_SERVICE_URL", "http://localhost:8000")


async def ingest_listings(data_path: str = None):
    """Load listings from JSON file and ingest into RAG service."""
    if data_path is None:
        data_path = Path(__file__).parent / "sample_data" / "apartments.json"

    with open(data_path) as f:
        raw = json.load(f)

    listings = [ApartmentListing(**item) for item in raw]

    async with httpx.AsyncClient(timeout=30) as client:
        for listing in listings:
            payload = {
                "id": listing.id,
                "text": listing.to_searchable_text(),
                "metadata": listing.model_dump(),
            }
            resp = await client.post(f"{RAG_URL}/ingest", json=payload)
            resp.raise_for_status()
            print(f"Ingested: {listing.address}")

    print(f"Done: {len(listings)} listings ingested")


if __name__ == "__main__":
    asyncio.run(ingest_listings())
