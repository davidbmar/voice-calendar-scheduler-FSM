"""Apartment search tool — queries the RAG service for matching listings."""

import os
from typing import Any

import httpx

from voice_assistant.tools.base import BaseTool

RAG_URL = os.environ.get("RAG_SERVICE_URL", "http://localhost:9900")


class ApartmentSearchTool(BaseTool):
    """Search available apartment listings using natural language queries."""

    @property
    def name(self) -> str:
        return "apartment_search"

    @property
    def description(self) -> str:
        return (
            "Search available apartment listings in Austin, TX. "
            "Accepts a natural language query such as "
            "'2 bedroom near downtown under $2000' and returns the "
            "top matching listings with address, price, and details."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Natural language search query describing the "
                        "desired apartment, e.g. '2 bedroom pet friendly "
                        "with parking under $2500'"
                    ),
                },
            },
            "required": ["query"],
        }

    async def execute(self, **kwargs: Any) -> str:
        """Query the RAG service and return formatted apartment results."""
        query = kwargs.get("query", "")
        if not query:
            return "Please provide a search query describing what kind of apartment you're looking for."

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{RAG_URL}/query",
                    json={"query": query, "top_k": 3},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError:
            return (
                "I'm sorry, the apartment search service is currently "
                "unavailable. Please try again in a moment."
            )
        except httpx.HTTPStatusError as exc:
            return (
                f"The apartment search returned an error "
                f"(status {exc.response.status_code}). Please try again."
            )

        results = data.get("results", [])
        if not results:
            return (
                "I didn't find any apartments matching that description. "
                "Could you try broadening your search?"
            )

        return self._format_results(results)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_results(results: list[dict]) -> str:
        """Turn RAG results into a readable string for the LLM to narrate."""
        parts: list[str] = []
        parts.append(f"I found {len(results)} apartment(s) that match:\n")

        for idx, result in enumerate(results, start=1):
            meta = result.get("metadata", {})
            score = result.get("score", 0.0)

            address = meta.get("address", "Unknown address")
            neighborhood = meta.get("neighborhood", "")
            bedrooms = meta.get("bedrooms", "?")
            bathrooms = meta.get("bathrooms", "?")
            sqft = meta.get("sqft", "?")
            rent = meta.get("rent", "?")
            available = meta.get("available_date", "?")
            description = meta.get("description", "")
            pet_friendly = meta.get("pet_friendly", False)
            parking = meta.get("parking", False)
            laundry = meta.get("laundry", "")
            amenities = meta.get("amenities", [])
            contact_name = meta.get("contact_name", "")
            contact_email = meta.get("contact_email", "")

            section = (
                f"--- Option {idx} (match score: {score:.0%}) ---\n"
                f"Address: {address}, {neighborhood}\n"
                f"Bedrooms: {bedrooms} | Bathrooms: {bathrooms} | "
                f"Sqft: {sqft}\n"
                f"Rent: ${rent}/month\n"
                f"Available: {available}\n"
                f"Description: {description}\n"
            )

            extras: list[str] = []
            if pet_friendly:
                extras.append("Pet friendly")
            if parking:
                extras.append("Parking included")
            if laundry:
                extras.append(f"Laundry: {laundry}")
            if amenities:
                extras.append(f"Amenities: {', '.join(amenities)}")
            if extras:
                section += " | ".join(extras) + "\n"

            if contact_name:
                section += f"Contact: {contact_name}"
                if contact_email:
                    section += f" ({contact_email})"
                section += "\n"

            parts.append(section)

        return "\n".join(parts)
