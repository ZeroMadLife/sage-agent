"""Local scenic spot data client."""

import json
from pathlib import Path
from typing import Any, TypedDict


class ScenicSpot(TypedDict):
    """Full scenic spot record."""

    id: str
    name: str
    city: str
    category: str
    description: str
    opening_hours: str
    ticket_price: int
    recommended_duration_hours: int
    best_season: str
    tags: list[str]
    location: str
    rating: float


class ScenicSpotSummary(TypedDict):
    """Token-efficient scenic spot summary."""

    id: str
    name: str
    city: str
    category: str
    ticket_price: int
    recommended_duration_hours: int
    rating: float
    tags: list[str]


class ScenicClient:
    """Scenic data client backed by a local JSON file."""

    def __init__(self, data_path: str) -> None:
        self._data_path = Path(data_path)
        self._spots: list[ScenicSpot] = []
        self._load()

    def _load(self) -> None:
        """Load scenic data from disk."""
        if not self._data_path.exists():
            self._spots = []
            return
        raw = json.loads(self._data_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            self._spots = []
            return
        self._spots = [self._normalize_spot(item) for item in raw if isinstance(item, dict)]

    def search_scenic_spots(
        self,
        city: str = "",
        category: str = "",
        keywords: str = "",
        free_only: bool = False,
        limit: int = 20,
    ) -> list[ScenicSpotSummary]:
        """Search scenic spots with optional filters."""
        results = self._spots

        if city:
            results = [spot for spot in results if spot["city"] == city]
        if category:
            results = [spot for spot in results if spot["category"] == category]
        if free_only:
            results = [spot for spot in results if spot["ticket_price"] == 0]
        if keywords:
            tokens = [token for token in keywords.lower().split() if token]
            results = [spot for spot in results if self._matches_keywords(spot, tokens)]

        ranked = sorted(results, key=lambda spot: spot["rating"], reverse=True)
        return [self._summarize_spot(spot) for spot in ranked[:limit]]

    def get_scenic_detail(self, spot_id: str) -> ScenicSpot | None:
        """Return full scenic spot details by ID."""
        for spot in self._spots:
            if spot["id"] == spot_id:
                return self._copy_spot(spot)
        return None

    def get_opening_hours(self, spot_id: str) -> str | None:
        """Return opening hours for a scenic spot."""
        detail = self.get_scenic_detail(spot_id)
        return detail.get("opening_hours") if detail else None

    def get_ticket_price(self, spot_id: str) -> int | None:
        """Return ticket price for a scenic spot."""
        detail = self.get_scenic_detail(spot_id)
        return detail.get("ticket_price") if detail else None

    @staticmethod
    def _summarize_spot(spot: ScenicSpot) -> ScenicSpotSummary:
        """Create a compact scenic spot summary."""
        return {
            "id": spot["id"],
            "name": spot["name"],
            "city": spot["city"],
            "category": spot["category"],
            "ticket_price": spot["ticket_price"],
            "recommended_duration_hours": spot["recommended_duration_hours"],
            "rating": spot["rating"],
            "tags": spot["tags"],
        }

    @staticmethod
    def _matches_keywords(spot: ScenicSpot, keywords: list[str]) -> bool:
        """Return whether a spot matches at least one search keyword."""
        if not keywords:
            return True

        haystack = [
            spot["name"].lower(),
            spot["category"].lower(),
            spot["description"].lower(),
            *(tag.lower() for tag in spot["tags"]),
        ]
        return any(keyword in text for keyword in keywords for text in haystack)

    @staticmethod
    def _copy_spot(spot: ScenicSpot) -> ScenicSpot:
        """Return a shallow copy of a scenic spot."""
        return {
            "id": spot["id"],
            "name": spot["name"],
            "city": spot["city"],
            "category": spot["category"],
            "description": spot["description"],
            "opening_hours": spot["opening_hours"],
            "ticket_price": spot["ticket_price"],
            "recommended_duration_hours": spot["recommended_duration_hours"],
            "best_season": spot["best_season"],
            "tags": list(spot["tags"]),
            "location": spot["location"],
            "rating": spot["rating"],
        }

    @staticmethod
    def _normalize_spot(raw: dict[str, Any]) -> ScenicSpot:
        """Normalize a raw JSON record."""
        tags = raw.get("tags", [])
        return {
            "id": str(raw.get("id", "")),
            "name": str(raw.get("name", "")),
            "city": str(raw.get("city", "")),
            "category": str(raw.get("category", "")),
            "description": str(raw.get("description", "")),
            "opening_hours": str(raw.get("opening_hours", "")),
            "ticket_price": int(raw.get("ticket_price", 0)),
            "recommended_duration_hours": int(raw.get("recommended_duration_hours", 0)),
            "best_season": str(raw.get("best_season", "")),
            "tags": [str(tag) for tag in tags] if isinstance(tags, list) else [],
            "location": str(raw.get("location", "")),
            "rating": float(raw.get("rating", 0.0)),
        }
