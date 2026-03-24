"""
retrieval.py — Retrieval Agent (Epic 2.1)

Given a GuestID, queries Azure SQL for the complete guest context:
  1. Guest profile          (dbo.Guests)
  2. Booking history        (dbo.Bookings + dbo.Properties)
  3. Survey responses       (dbo.Surveys)
  4. Available experiences  (dbo.Experiences + dbo.Properties)
  5. Inventory / availability (dbo.Inventory + dbo.Properties)

All queries use direct parameterized SQL (ADR-013) for speed and
determinism. GPT-writes-SQL is reserved for the NL search bar.

Results are aggregated into a GuestContext Pydantic model — the
contract consumed by downstream agents (Sentiment, Reasoning, Validation).
"""

import asyncio
import logging
import re
import time
from typing import Any

from db import execute_query, execute_query_params
from models import (
    Booking,
    Experience,
    GuestContext,
    GuestProfile,
    InventorySlot,
    ServiceRequest,
    Survey,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GuestID validation
# ---------------------------------------------------------------------------
_GUEST_ID_PATTERN = re.compile(r"^G-\d{7}$")


def _validate_guest_id(guest_id: str) -> None:
    """Raise ValueError if guest_id doesn't match G-NNNNNNN format."""
    if not _GUEST_ID_PATTERN.match(guest_id):
        raise ValueError(
            f"Invalid GuestID format: {guest_id!r}. Expected G-NNNNNNN (e.g. G-0001001)."
        )


# ---------------------------------------------------------------------------
# Individual query functions
# ---------------------------------------------------------------------------

async def _fetch_guest(guest_id: str) -> GuestProfile | None:
    """Task 2.1.1 — Fetch guest profile by GuestID."""
    rows = await execute_query_params(
        "SELECT * FROM dbo.Guests WHERE GuestID = ?",
        (guest_id,),
    )
    if not rows:
        return None
    return GuestProfile(**rows[0])


async def _fetch_bookings(guest_id: str) -> list[Booking]:
    """Task 2.1.2 — Fetch booking history with property names."""
    rows = await execute_query_params(
        """
        SELECT b.BookingID, b.GuestID, b.PropertyID,
               p.Name AS PropertyName,
               b.CheckIn, b.CheckOut, b.RoomType, b.RoomNumber,
               b.RatePerNight, b.TotalAmount, b.Status,
               b.SpecialRequests, b.BookedDate
        FROM   dbo.Bookings   b
        JOIN   dbo.Properties  p ON b.PropertyID = p.PropertyID
        WHERE  b.GuestID = ?
        ORDER BY b.CheckIn DESC
        """,
        (guest_id,),
    )
    return [Booking(**r) for r in rows]


async def _fetch_surveys(guest_id: str) -> list[Survey]:
    """Task 2.1.3 — Fetch survey responses including [FreeText]."""
    rows = await execute_query_params(
        """
        SELECT SurveyID, GuestID, BookingID, PropertyID,
               OverallRating, NPS, Cleanliness, Service, FoodBeverage,
               Spa, Activities, [FreeText], SubmittedDate
        FROM   dbo.Surveys
        WHERE  GuestID = ?
        ORDER BY SubmittedDate DESC
        """,
        (guest_id,),
    )
    return [Survey(**r) for r in rows]


async def _fetch_experiences() -> list[Experience]:
    """Task 2.1.4 — Fetch all available experiences across all properties."""
    rows = await execute_query(
        """
        SELECT e.ExperienceID, e.PropertyID,
               p.Name AS PropertyName,
               e.Name, e.Category, e.Description,
               e.Price, e.Duration, e.Available
        FROM   dbo.Experiences e
        JOIN   dbo.Properties  p ON e.PropertyID = p.PropertyID
        WHERE  e.Available = 1
        ORDER BY p.Name, e.Category, e.Name
        """
    )
    return [Experience(**r) for r in rows]


async def _fetch_inventory() -> list[InventorySlot]:
    """Task 2.1.4b — Fetch inventory with availability > 0."""
    rows = await execute_query(
        """
        SELECT i.PropertyID,
               p.Name AS PropertyName,
               i.Date, i.RoomType, i.TotalRooms, i.BookedRooms, i.Available
        FROM   dbo.Inventory   i
        JOIN   dbo.Properties   p ON i.PropertyID = p.PropertyID
        WHERE  i.Available > 0
        ORDER BY i.PropertyID, i.Date, i.RoomType
        """
    )
    return [InventorySlot(**r) for r in rows]


async def _fetch_service_requests(guest_id: str) -> list[ServiceRequest]:
    """Task 4.1.4 — Fetch last 24 months of service requests for this guest."""
    rows = await execute_query_params(
        """
        SELECT sr.RequestID, sr.GuestID, sr.BookingID, sr.PropertyID,
               sr.RequestedDate, sr.Department, sr.Category,
               sr.[Description], sr.Priority, sr.[Status],
               sr.AssignedTo, sr.CompletedDate, sr.ResponseMinutes,
               sr.ResolutionNotes, sr.GuestSatisfied
        FROM   dbo.ServiceRequests sr
        WHERE  sr.GuestID = ?
          AND  sr.RequestedDate >= DATEADD(MONTH, -36, GETDATE())
        ORDER BY sr.RequestedDate DESC
        """,
        (guest_id,),
    )
    return [ServiceRequest(**r) for r in rows]


# ---------------------------------------------------------------------------
# Public API — the Retrieval Agent entry point
# ---------------------------------------------------------------------------

async def retrieve_guest_context(guest_id: str) -> GuestContext:
    """
    Retrieval Agent: given a GuestID, return the complete GuestContext.

    Runs all 5 queries concurrently via asyncio.gather for minimum latency.
    Raises ValueError for invalid GuestID format.
    """
    _validate_guest_id(guest_id)
    log.info("Retrieval Agent started for GuestID=%s", guest_id)

    start = time.perf_counter()

    # Run all queries concurrently
    guest, bookings, surveys, experiences, inventory, service_requests = await asyncio.gather(
        _fetch_guest(guest_id),
        _fetch_bookings(guest_id),
        _fetch_surveys(guest_id),
        _fetch_experiences(),
        _fetch_inventory(),
        _fetch_service_requests(guest_id),
    )

    elapsed_ms = (time.perf_counter() - start) * 1000

    log.info(
        "Retrieval Agent completed in %.0fms — "
        "guest=%s, bookings=%d, surveys=%d, experiences=%d, inventory=%d, service_requests=%d",
        elapsed_ms,
        "found" if guest else "NOT_FOUND",
        len(bookings),
        len(surveys),
        len(experiences),
        len(inventory),
        len(service_requests),
    )

    return GuestContext(
        guest=guest,
        bookings=bookings,
        surveys=surveys,
        experiences=experiences,
        inventory=inventory,
        service_requests=service_requests,
        retrieval_ms=round(elapsed_ms, 1),
    )
