"""Async QWeather API client."""

from typing import Any, TypedDict

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential


class WeatherClientError(Exception):
    """Raised when QWeather returns an API error."""


class CityInfo(TypedDict):
    """Normalized QWeather city lookup result."""

    location_id: str
    name: str
    lat: str
    lon: str


class CurrentWeather(TypedDict):
    """Normalized current weather."""

    obs_time: str
    temp_c: int
    feels_like: int
    text: str
    icon: str
    humidity: int
    wind_dir: str
    wind_scale: str
    wind_speed: str
    pressure: str
    vis: str


class DailyForecast(TypedDict):
    """Normalized daily weather forecast."""

    date: str
    temp_max: int
    temp_min: int
    text_day: str
    text_night: str
    wind_dir_day: str
    wind_scale_day: str
    humidity: int
    precip: float
    uv_index: int


class WeatherAlert(TypedDict):
    """Normalized weather alert."""

    title: str
    type: str
    level: str
    text: str
    start_time: str
    end_time: str


class WeatherClient:
    """Async client wrapping QWeather APIs."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.qweather.com/v7",
        geo_url: str = "https://geoapi.qweather.com/v2",
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._geo_url = geo_url
        self._http = httpx.AsyncClient(timeout=10.0)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10), reraise=True)
    async def search_city(self, city_name: str) -> CityInfo:
        """Resolve a city name to a QWeather location ID."""
        params = {"key": self._api_key, "location": city_name}
        resp = await self._http.get(f"{self._geo_url}/city/lookup", params=params)
        data = resp.json()

        locations = data.get("location", [])
        if data.get("code") != "200" or not locations:
            raise WeatherClientError(f"城市查询失败: {city_name}")

        loc = locations[0]
        if not isinstance(loc, dict):
            raise WeatherClientError(f"城市查询失败: {city_name}")
        return {
            "location_id": str(loc["id"]),
            "name": str(loc["name"]),
            "lat": str(loc.get("lat", "")),
            "lon": str(loc.get("lon", "")),
        }

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10), reraise=True)
    async def get_current_weather(self, location_id: str) -> CurrentWeather:
        """Fetch current weather by QWeather location ID."""
        params = {"key": self._api_key, "location": location_id}
        resp = await self._http.get(f"{self._base_url}/weather/now", params=params)
        data = resp.json()

        if data.get("code") != "200":
            raise WeatherClientError(f"和风天气API错误: code={data.get('code')}")

        now = data["now"]
        if not isinstance(now, dict):
            raise WeatherClientError("和风天气API错误: missing now")
        return {
            "obs_time": str(now.get("obsTime", "")),
            "temp_c": int(now.get("temp", 0)),
            "feels_like": int(now.get("feelsLike", 0)),
            "text": str(now.get("text", "")),
            "icon": str(now.get("icon", "")),
            "humidity": int(now.get("humidity", 0)),
            "wind_dir": str(now.get("windDir", "")),
            "wind_scale": str(now.get("windScale", "")),
            "wind_speed": str(now.get("windSpeed", "")),
            "pressure": str(now.get("pressure", "")),
            "vis": str(now.get("vis", "")),
        }

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10), reraise=True)
    async def get_forecast(self, location_id: str, days: int = 7) -> list[DailyForecast]:
        """Fetch a 3-day or 7-day forecast."""
        endpoint = "3d" if days <= 3 else "7d"
        params = {"key": self._api_key, "location": location_id}
        resp = await self._http.get(f"{self._base_url}/weather/{endpoint}", params=params)
        data = resp.json()

        if data.get("code") != "200":
            raise WeatherClientError(f"和风天气API错误: code={data.get('code')}")

        daily = data.get("daily", [])
        if not isinstance(daily, list):
            return []
        return [self._normalize_daily(day) for day in daily if isinstance(day, dict)]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10), reraise=True)
    async def get_weather_alert(self, location_id: str) -> list[WeatherAlert]:
        """Fetch active weather alerts."""
        params = {"key": self._api_key, "location": location_id}
        resp = await self._http.get(f"{self._base_url}/warning/now", params=params)
        data = resp.json()

        if data.get("code") != "200":
            return []

        warnings = data.get("warning", [])
        if not isinstance(warnings, list):
            return []
        return [self._normalize_alert(alert) for alert in warnings if isinstance(alert, dict)]

    @staticmethod
    def _normalize_daily(day: dict[str, Any]) -> DailyForecast:
        """Normalize a QWeather daily forecast item."""
        return {
            "date": str(day.get("fxDate", "")),
            "temp_max": int(day.get("tempMax", 0)),
            "temp_min": int(day.get("tempMin", 0)),
            "text_day": str(day.get("textDay", "")),
            "text_night": str(day.get("textNight", "")),
            "wind_dir_day": str(day.get("windDirDay", "")),
            "wind_scale_day": str(day.get("windScaleDay", "")),
            "humidity": int(day.get("humidity", 0)),
            "precip": float(day.get("precip", 0)),
            "uv_index": int(day.get("uvIndex", 0)),
        }

    @staticmethod
    def _normalize_alert(alert: dict[str, Any]) -> WeatherAlert:
        """Normalize a QWeather warning item."""
        return {
            "title": str(alert.get("title", "")),
            "type": str(alert.get("type", "")),
            "level": str(alert.get("level", "")),
            "text": str(alert.get("text", "")),
            "start_time": str(alert.get("startTime", "")),
            "end_time": str(alert.get("endTime", "")),
        }

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()
