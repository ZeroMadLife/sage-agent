"""Async Amap Web Service API client."""

from typing import Any, Literal, TypedDict

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential


class AmapClientError(Exception):
    """Raised when Amap returns an API error."""


class AmapPoi(TypedDict):
    """Normalized Amap POI."""

    id: str
    name: str
    type: str
    address: str
    location: str
    tel: str
    tag: str
    rating: str
    cost: str


class RouteStep(TypedDict):
    """Normalized route step."""

    instruction: str
    distance_m: int
    duration_s: int


class RouteResult(TypedDict):
    """Normalized route result."""

    distance_m: int
    duration_s: int
    steps: list[RouteStep]


class GeocodeResult(TypedDict):
    """Normalized geocode result."""

    location: str
    formatted_address: str
    level: str


RouteMode = Literal["walking", "driving", "transit"]


class AmapClient:
    """Async client wrapping Amap Web Service APIs."""

    def __init__(self, api_key: str, base_url: str = "https://restapi.amap.com/v3") -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._http = httpx.AsyncClient(timeout=10.0)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10), reraise=True)
    async def search_attractions(
        self, city: str, keywords: str = "", category: str = "", limit: int = 20
    ) -> list[AmapPoi]:
        """Search attraction POIs and return normalized results."""
        params = {
            "key": self._api_key,
            "keywords": keywords or category or "景点",
            "city": city,
            "citylimit": "true",
            "types": "110000",
            "offset": str(limit),
            "page": "1",
            "extensions": "all",
        }
        resp = await self._http.get(f"{self._base_url}/place/text", params=params)
        data = resp.json()

        if data.get("status") != "1":
            raise AmapClientError(f"高德API错误: {data.get('info', 'unknown')}")

        pois = data.get("pois", [])
        if not isinstance(pois, list):
            return []
        return [self._normalize_poi(poi) for poi in pois if isinstance(poi, dict)]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10), reraise=True)
    async def get_route(
        self, origin: str, destination: str, mode: RouteMode = "walking"
    ) -> RouteResult:
        """Plan a route and return distance, duration, and navigation steps."""
        endpoint_map: dict[RouteMode, str] = {
            "walking": "/direction/walking",
            "driving": "/direction/driving",
            "transit": "/direction/transit/integrated",
        }
        params = {
            "key": self._api_key,
            "origin": origin,
            "destination": destination,
        }
        if mode == "transit":
            params["city"] = "杭州"

        resp = await self._http.get(f"{self._base_url}{endpoint_map[mode]}", params=params)
        data = resp.json()

        if data.get("status") != "1":
            raise AmapClientError(f"高德API错误: {data.get('info', 'unknown')}")

        route = data.get("route", {})
        paths = route.get("paths", []) if isinstance(route, dict) else []
        if not paths:
            return {"distance_m": 0, "duration_s": 0, "steps": []}

        path = paths[0]
        if not isinstance(path, dict):
            return {"distance_m": 0, "duration_s": 0, "steps": []}

        raw_steps = path.get("steps", [])
        steps = raw_steps if isinstance(raw_steps, list) else []
        return {
            "distance_m": int(path.get("distance", 0)),
            "duration_s": int(path.get("duration", 0)),
            "steps": [
                {
                    "instruction": str(step.get("instruction", "")),
                    "distance_m": int(step.get("distance", 0)),
                    "duration_s": int(step.get("duration", 0)),
                }
                for step in steps
                if isinstance(step, dict)
            ],
        }

    async def geocode(self, address: str, city: str = "") -> GeocodeResult:
        """Convert an address to an Amap coordinate string."""
        params = {"key": self._api_key, "address": address}
        if city:
            params["city"] = city
        resp = await self._http.get(f"{self._base_url}/geocode/geo", params=params)
        data = resp.json()
        geocodes = data.get("geocodes", [])
        if data.get("status") != "1" or not geocodes:
            raise AmapClientError(f"地理编码失败: {address}")

        geo = geocodes[0]
        if not isinstance(geo, dict):
            raise AmapClientError(f"地理编码失败: {address}")
        return {
            "location": str(geo["location"]),
            "formatted_address": str(geo.get("formatted_address", address)),
            "level": str(geo.get("level", "")),
        }

    @staticmethod
    def _normalize_poi(raw: dict[str, Any]) -> AmapPoi:
        """Normalize an Amap POI payload."""
        biz = raw.get("biz_ext", {})
        biz_ext = biz if isinstance(biz, dict) else {}
        return {
            "id": str(raw.get("id", "")),
            "name": str(raw.get("name", "")),
            "type": str(raw.get("type", "")),
            "address": str(raw.get("address", "")),
            "location": str(raw.get("location", "")),
            "tel": str(raw.get("tel", "")),
            "tag": str(raw.get("tag", "")),
            "rating": str(biz_ext.get("rating", "")),
            "cost": str(biz_ext.get("cost", "")),
        }

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()
