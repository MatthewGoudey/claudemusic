"""
Shared date filtering logic.

Every time-aware endpoint uses the same pattern:
  - start_date: ISO date string (e.g., "2024-03-01")
  - end_date: ISO date string (e.g., "2024-06-15")
  - days: convenience shortcut for "last N days", ignored if start_date/end_date provided
  - limit: default 50, max 1000
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from typing import Optional
from fastapi import HTTPException


@dataclass
class DateFilter:
    start_date: date | None
    end_date: date | None
    days: int | None
    limit: int

    @property
    def effective_start(self) -> datetime | None:
        if self.start_date:
            return datetime.combine(self.start_date, datetime.min.time(), tzinfo=timezone.utc)
        if self.days:
            return datetime.now(timezone.utc) - timedelta(days=self.days)
        return None

    @property
    def effective_end(self) -> datetime | None:
        if self.end_date:
            # End of the specified day
            return datetime.combine(self.end_date, datetime.max.time(), tzinfo=timezone.utc)
        return None

    def build_where(self, col: str = "listened_at", param_offset: int = 0) -> tuple[list[str], list]:
        """Build WHERE clauses and params for asyncpg ($1, $2, ...) placeholders.

        Returns (clauses, params) where clauses are strings like "listened_at >= $1"
        and params are the corresponding values.
        """
        clauses = []
        params = []
        idx = param_offset + 1

        start = self.effective_start
        end = self.effective_end

        if start:
            clauses.append(f"{col} >= ${idx}")
            params.append(start)
            idx += 1
        if end:
            clauses.append(f"{col} <= ${idx}")
            params.append(end)
            idx += 1

        return clauses, params

    def as_dict(self) -> dict:
        return {
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "days": self.days,
        }


def parse_date_filter(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    days: Optional[int] = None,
    limit: int = 50,
) -> DateFilter:
    """Parse and validate date filter parameters."""
    parsed_start = None
    parsed_end = None

    if start_date:
        try:
            parsed_start = date.fromisoformat(start_date)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid start_date format: {start_date}. Use ISO format (YYYY-MM-DD).")

    if end_date:
        try:
            parsed_end = date.fromisoformat(end_date)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid end_date format: {end_date}. Use ISO format (YYYY-MM-DD).")

    if parsed_start and parsed_end and parsed_start > parsed_end:
        raise HTTPException(status_code=400, detail="start_date must be before end_date.")

    if limit < 1:
        limit = 1
    if limit > 1000:
        limit = 1000

    # days is ignored if start_date or end_date is provided
    effective_days = days if not parsed_start and not parsed_end else None

    return DateFilter(
        start_date=parsed_start,
        end_date=parsed_end,
        days=effective_days,
        limit=limit,
    )
