"""FastAPI route tests."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import api.main as api_main
from api.main import create_app


def test_health_endpoint() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_start_chat_returns_session_id() -> None:
    client = TestClient(create_app())

    response = client.post("/api/v1/chat", json={"content": "周末去杭州"})

    assert response.status_code == 200
    assert response.json()["session_id"]


def test_create_runtime_agent_returns_none_when_configuration_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置失败时 create_runtime_agent 返回 None。"""

    def raise_settings_error() -> object:
        raise RuntimeError("missing config")

    monkeypatch.setattr(api_main, "get_settings", raise_settings_error)

    assert api_main.create_runtime_agent() is None


async def test_create_runtime_agent_wraps_weather_client_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime Agent exposes city-based weather tools over the lower-level WeatherClient."""

    class FakeWeatherClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def search_city(self, city: str) -> dict[str, str]:
            return {"location_id": f"{city}-location"}

        async def get_current_weather(self, location_id: str) -> dict[str, object]:
            return {"location_id": location_id, "text": "晴"}

        async def get_forecast(self, location_id: str, days: int = 7) -> list[dict[str, object]]:
            return [{"location_id": location_id, "days": days}]

    class FakeScenicClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        def search_scenic_spots(self, **kwargs: object) -> list[object]:
            return []

        def get_scenic_detail(self, spot_id: str) -> None:
            return None

    class FakeAmapClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def search_nearby(self, **kwargs: object) -> list[object]:
            return []

        async def get_poi_detail(self, poi_id: str) -> dict[str, str]:
            return {"id": poi_id}

        async def search_attractions(self, **kwargs: object) -> list[object]:
            return []

        async def get_route(self, **kwargs: object) -> dict[str, object]:
            return {}

        async def geocode(self, **kwargs: object) -> dict[str, object]:
            return {}

    settings = SimpleNamespace(
        llm_model="doubao:test-model",
        qweather_api_key="weather-key",
        qweather_base_url="https://weather.example/v7",
        qweather_geo_url="https://weather.example/geo",
        amap_api_key="amap-key",
    )
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    monkeypatch.setattr(api_main, "create_llm", lambda model_spec: object())
    monkeypatch.setattr(api_main, "WeatherClient", FakeWeatherClient)
    monkeypatch.setattr(api_main, "ScenicClient", FakeScenicClient)
    monkeypatch.setattr(api_main, "AmapClient", FakeAmapClient)
    monkeypatch.setattr(api_main, "build_graph", lambda **kwargs: object())

    agent = api_main.create_runtime_agent()

    assert agent is not None
    weather = await agent._tools["get_weather"]("杭州")
    forecast = await agent._tools["get_forecast"]("杭州", days=3)
    assert weather == {"location_id": "杭州-location", "text": "晴"}
    assert forecast == [{"location_id": "杭州-location", "days": 3}]
