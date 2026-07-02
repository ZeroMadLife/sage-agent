"""FastAPI app factory."""

from typing import Any

from fastapi import FastAPI

from api import routes, ws


def create_app(graph: Any | None = None) -> FastAPI:
    """Create the TourSwarm API app."""
    app = FastAPI(title="TourSwarm API")
    app.state.graph = graph
    app.include_router(routes.router)
    app.include_router(ws.router)
    return app


app = create_app()
