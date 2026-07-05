"""Deterministic validation for LLM-generated itineraries."""

from datetime import date, timedelta
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from models.itinerary import Itinerary


class VerificationIssue(BaseModel):
    """One deterministic validation issue."""

    code: str
    message: str
    severity: str = "error"


class VerificationResult(BaseModel):
    """Verification summary for API responses and evals."""

    passed: bool
    issues: list[VerificationIssue]


@runtime_checkable
class Verifier(Protocol):
    """Domain verifier interface used by pluggable Skills."""

    def verify(self, output: Any, context: dict[str, Any]) -> VerificationResult:
        """Validate one domain output with the provided runtime context."""
        ...


def _date_range(start: str, end: str) -> set[str]:
    """Return inclusive ISO date strings between start and end."""
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    days: set[str] = set()
    current = start_date
    while current <= end_date:
        days.add(current.isoformat())
        current += timedelta(days=1)
    return days


def verify_itinerary(
    itinerary: Itinerary,
    dates: dict[str, str],
    budget_total: int,
    weather_info: dict[str, Any],
) -> VerificationResult:
    """Validate itinerary dates, costs, budget flags, and obvious empty plans."""
    _ = weather_info
    issues: list[VerificationIssue] = []

    expected_dates = _date_range(dates["start"], dates["end"])
    actual_dates = {day.date for day in itinerary.days}
    for missing in sorted(expected_dates - actual_dates):
        issues.append(
            VerificationIssue(
                code="missing_date",
                message=f"行程缺少日期 {missing}",
            )
        )

    for day in itinerary.days:
        if not day.spots:
            issues.append(
                VerificationIssue(
                    code="empty_day",
                    message=f"{day.date} 没有任何景点安排",
                )
            )

    day_total = sum(day.total_cost for day in itinerary.days)
    if itinerary.total_cost != day_total:
        issues.append(
            VerificationIssue(
                code="total_cost_mismatch",
                message=f"总费用 {itinerary.total_cost} 与每日费用合计 {day_total} 不一致",
            )
        )

    if itinerary.budget is not None:
        should_over_budget = itinerary.budget.spent > budget_total
        if itinerary.budget.over_budget != should_over_budget:
            issues.append(
                VerificationIssue(
                    code="over_budget_flag_mismatch",
                    message="预算超支标记与实际花费不一致",
                )
            )

    return VerificationResult(passed=not issues, issues=issues)


class ItineraryVerifier:
    """Verifier implementation that preserves the legacy itinerary checks."""

    def verify(self, output: Any, context: dict[str, Any]) -> VerificationResult:
        """Validate an itinerary output using the legacy verification function."""
        itinerary = output if isinstance(output, Itinerary) else Itinerary.model_validate(output)
        dates = self._dict_str(context.get("dates", {}))
        budget_total = int(context.get("budget_total", 0))
        weather_info = self._dict_any(context.get("weather_info", {}))
        return verify_itinerary(
            itinerary=itinerary,
            dates=dates,
            budget_total=budget_total,
            weather_info=weather_info,
        )

    @staticmethod
    def _dict_str(value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {str(key): str(item) for key, item in value.items()}

    @staticmethod
    def _dict_any(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        return dict(value)
