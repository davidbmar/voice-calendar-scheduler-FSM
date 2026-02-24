"""Pydantic model for apartment listings with RAG-friendly text generation."""

from pydantic import BaseModel
from typing import Optional


class ApartmentListing(BaseModel):
    id: str
    address: str
    neighborhood: str
    city: str = "Austin"
    state: str = "TX"
    bedrooms: int
    bathrooms: float
    sqft: int
    rent: int  # monthly rent in USD
    available_date: str  # YYYY-MM-DD
    description: str
    amenities: list[str] = []
    pet_friendly: bool = False
    parking: bool = False
    laundry: str = ""  # "in-unit", "on-site", "none"
    contact_name: str = ""
    contact_email: str = ""

    def to_searchable_text(self) -> str:
        """Generate text for RAG embedding."""
        parts = [
            f"{self.bedrooms} bedroom {self.bathrooms} bathroom apartment",
            f"at {self.address}, {self.neighborhood}, {self.city}",
            f"${self.rent}/month, {self.sqft} sqft",
            f"Available {self.available_date}",
            self.description,
        ]
        if self.amenities:
            parts.append(f"Amenities: {', '.join(self.amenities)}")
        if self.pet_friendly:
            parts.append("Pet friendly")
        if self.parking:
            parts.append("Parking available")
        if self.laundry:
            parts.append(f"Laundry: {self.laundry}")
        return ". ".join(parts)
