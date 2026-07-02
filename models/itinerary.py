"""Pydantic models for structured itinerary output.

These models are the data contract between agents, API responses, and future
frontend itinerary views.
"""

from pydantic import BaseModel, Field


class SpotVisit(BaseModel):
    """A planned visit to one scenic spot."""

    spot_id: str = Field(description="Spot identifier")
    name: str = Field(description="Spot name")
    arrival_time: str = Field(description="Arrival time in HH:MM")
    departure_time: str = Field(description="Departure time in HH:MM")
    duration_hours: float = Field(description="Recommended visit duration in hours")
    ticket_price: int = Field(default=0, description="Ticket price in CNY")
    category: str = Field(default="", description="Spot category")
    location: str = Field(default="", description="Coordinates in lng,lat")


class Meal(BaseModel):
    """A planned meal."""

    name: str = Field(description="Meal name")
    meal_type: str = Field(description="breakfast/lunch/dinner")
    estimated_cost: int = Field(description="Estimated cost in CNY")


class Transport(BaseModel):
    """Transport between two locations."""

    from_name: str = Field(description="Origin name")
    to_name: str = Field(description="Destination name")
    mode: str = Field(description="walking/driving/transit")
    distance_m: int = Field(default=0, description="Distance in meters")
    duration_s: int = Field(default=0, description="Duration in seconds")


class ItineraryDay(BaseModel):
    """A one-day itinerary."""

    date: str = Field(description="Date in YYYY-MM-DD")
    spots: list[SpotVisit] = Field(default_factory=list, description="Spot visits")
    meals: list[Meal] = Field(default_factory=list, description="Meals")
    transport: list[Transport] = Field(default_factory=list, description="Transport legs")
    total_cost: int = Field(default=0, description="Daily total cost in CNY")


class BudgetBreakdown(BaseModel):
    """Budget allocation and spend summary."""

    total: int = Field(description="Total budget in CNY")
    spent: int = Field(description="Allocated or spent amount in CNY")
    transport: int = Field(default=0, description="Transport budget")
    accommodation: int = Field(default=0, description="Accommodation budget")
    food: int = Field(default=0, description="Food budget")
    tickets: int = Field(default=0, description="Ticket budget")
    misc: int = Field(default=0, description="Miscellaneous budget")
    over_budget: bool = Field(default=False, description="Whether spending exceeds budget")


class Itinerary(BaseModel):
    """A complete itinerary plan."""

    destination: str = Field(description="Destination city or region")
    days: list[ItineraryDay] = Field(default_factory=list, description="Daily plans")
    total_cost: int = Field(default=0, description="Total cost in CNY")
    weather_summary: str = Field(default="", description="Weather summary")
    budget: BudgetBreakdown | None = Field(default=None, description="Budget summary")
