"""
models.py — Pydantic models for the AI agent pipeline

These models define the contract between the 4-agent sequential pipeline:
  Retrieval → Sentiment → Intelligence → Validation

The GuestContext is the top-level aggregate produced by the Retrieval Agent
and consumed by all downstream agents.

Epic 3.3 adds the Guest Intelligence layer:
  GuestInsight       — a single pattern card (likes / pain_points / request_patterns / cross_property)
  GuestIntelligence  — briefing + insight cards, replaces property recommendations as primary AI output

The original Recommendation / ReasoningResult models are preserved for the
on-demand /api/recommend endpoint (secondary "Recommendations" button in the UI).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Individual record models (mirror Azure SQL tables)
# ---------------------------------------------------------------------------

class GuestProfile(BaseModel):
    """Guest record from dbo.Guests."""
    GuestID: str
    FirstName: str
    LastName: str
    Email: str
    Phone: Optional[str] = None
    HomeCity: Optional[str] = None
    Country: str = "US"
    LoyaltyTier: str
    LoyaltyPoints: int = 0
    MemberSince: date
    Preferences: Optional[str] = None  # JSON string


class Booking(BaseModel):
    """Booking record from dbo.Bookings joined with dbo.Properties."""
    BookingID: str
    GuestID: str
    PropertyID: str
    PropertyName: str  # from JOIN with dbo.Properties
    CheckIn: date
    CheckOut: date
    RoomType: str
    RoomNumber: Optional[str] = None
    RatePerNight: Decimal
    TotalAmount: Decimal
    Status: str
    SpecialRequests: Optional[str] = None
    BookedDate: date


class Survey(BaseModel):
    """Survey record from dbo.Surveys."""
    SurveyID: str
    GuestID: str
    BookingID: str
    PropertyID: str
    OverallRating: int
    NPS: int
    Cleanliness: int
    Service: int
    FoodBeverage: int
    Spa: Optional[int] = None
    Activities: Optional[int] = None
    FreeText: Optional[str] = None
    SubmittedDate: date


class Experience(BaseModel):
    """Experience record from dbo.Experiences."""
    ExperienceID: str
    PropertyID: str
    PropertyName: str  # from JOIN with dbo.Properties
    Name: str
    Category: str
    Description: str
    Price: Decimal
    Duration: Optional[str] = None
    Available: bool


class InventorySlot(BaseModel):
    """Inventory record from dbo.Inventory."""
    PropertyID: str
    PropertyName: str  # from JOIN with dbo.Properties
    Date: date
    RoomType: str
    TotalRooms: int
    BookedRooms: int
    Available: int


class ServiceRequest(BaseModel):
    """Service request record from dbo.ServiceRequests (Epic 4.1 — HotSOS mirror)."""
    RequestID: str
    GuestID: str
    BookingID: Optional[str] = None
    PropertyID: str
    RequestedDate: datetime
    Department: str          # Housekeeping / Engineering / F&B / Front Desk / Guest Services
    Category: str            # Extra Linens, AC Issue, Noise Complaint, Room Service, etc.
    Description: Optional[str] = None
    Priority: str            # Low / Medium / High / Urgent
    Status: str              # Open / InProgress / Completed / Escalated / Cancelled
    AssignedTo: Optional[str] = None
    CompletedDate: Optional[datetime] = None
    ResponseMinutes: Optional[int] = None   # key AI signal: time to first action
    ResolutionNotes: Optional[str] = None
    GuestSatisfied: Optional[bool] = None


# ---------------------------------------------------------------------------
# Sentiment Agent models (Epic 2.2)
# ---------------------------------------------------------------------------

class PropertySentiment(BaseModel):
    """Sentiment summary for a single property, produced by the Sentiment Agent."""
    property_id: str
    property_name: str
    survey_count: int

    # Average numeric ratings (None if no surveys with that category)
    avg_overall: Optional[float] = None
    avg_nps: Optional[float] = None
    avg_cleanliness: Optional[float] = None
    avg_service: Optional[float] = None
    avg_food_beverage: Optional[float] = None
    avg_spa: Optional[float] = None        # None if not rated
    avg_activities: Optional[float] = None  # None if not rated

    # LLM-extracted themes from FreeText
    positive_themes: list[str] = Field(default_factory=list)
    negative_themes: list[str] = Field(default_factory=list)


class ServicePattern(BaseModel):
    """
    A service request pattern detected by the Sentiment Agent (Epic 4.1).

    Produced by grouping ServiceRequests by department/category and flagging:
      - repeat_category: same category appeared ≥2 times across stays
      - slow_response:   ResponseMinutes > 60 on High/Urgent priority requests
    """
    department: str              # e.g. "F&B"
    category: str                # e.g. "Room Service"
    occurrence_count: int        # how many times this category appeared
    slow_responses: int = 0      # requests with ResponseMinutes > 60 at High/Urgent priority
    avg_response_minutes: Optional[float] = None
    is_repeat: bool = False      # True if occurrence_count >= 2
    is_slow: bool = False        # True if any slow_responses > 0
    property_names: list[str] = Field(default_factory=list)  # properties where this occurred
    flag_message: str = ""       # human-readable signal, e.g. "Slow F&B response on 2 stays"


class SentimentResult(BaseModel):
    """Top-level output of the Sentiment Agent — added to GuestContext."""
    properties: list[PropertySentiment] = Field(default_factory=list)
    overall_preferences: list[str] = Field(default_factory=list)  # cross-property patterns
    service_patterns: list[ServicePattern] = Field(default_factory=list)  # Epic 4.1
    sentiment_ms: float = 0.0


# ---------------------------------------------------------------------------
# Reasoning Agent models (Epic 2.3)
# ---------------------------------------------------------------------------

class ExperienceRecommendation(BaseModel):
    """A single experience recommended within a property recommendation."""
    experience_id: str
    name: str
    category: str
    description: str
    price: Decimal


class Recommendation(BaseModel):
    """A single property recommendation produced by the Reasoning Agent."""
    property_id: str
    property_name: str
    scenario: str           # "enrich_upcoming" | "date_change" | "new_trip"
    headline: str           # one-line teaser shown in the UI
    narrative: str          # concierge-style 2-4 sentence explanation
    booking_id: Optional[str] = None  # linked booking (enrich_upcoming / date_change)
    experiences: list[ExperienceRecommendation] = Field(default_factory=list)


class ReasoningResult(BaseModel):
    """Top-level output of the Reasoning Agent — added to GuestContext."""
    scenario: str           # auto-inferred: "enrich_upcoming" | "date_change" | "new_trip"
    recommendations: list[Recommendation] = Field(default_factory=list)
    reasoning_ms: float = 0.0


# ---------------------------------------------------------------------------
# Validation Agent models (Epic 2.4)
# ---------------------------------------------------------------------------

class ValidationFlag(BaseModel):
    """A single issue found by the Validation Agent."""
    field: str                  # e.g. "property_id", "narrative"
    message: str                # human-readable description
    severity: str               # "info" | "warning" | "blocked"


class ValidationResult(BaseModel):
    """Top-level output of the Validation Agent — added to GuestContext."""
    passed: bool                        # True only if no blocked flags AND content safety passed
    flags: list[ValidationFlag] = Field(default_factory=list)

    # Quality-checked recommendations (bad properties stripped)
    filtered_recommendations: list[Recommendation] = Field(default_factory=list)

    # Content Safety outcome (nuclear mode: if False, safe_fallback_used=True)
    content_safety_passed: bool = True
    safe_fallback_used: bool = False

    validation_ms: float = 0.0


# ---------------------------------------------------------------------------
# Intelligence Agent models (Epic 3.3)
# ---------------------------------------------------------------------------

class GuestInsight(BaseModel):
    """
    A single pattern insight card extracted from the guest's cross-system history.

    Four types:
      likes            — What the guest consistently enjoys and rates highly
      pain_points      — Recurring frustrations, low scores, or negative themes
      request_patterns — Special requests that repeat across stays
      cross_property   — Patterns that span multiple properties (correlations)
    """
    type: str  # "likes" | "pain_points" | "request_patterns" | "cross_property"
    title: str  # Short heading, e.g. "Consistently books spa-access rooms"
    detail: str  # 1-2 sentences of supporting evidence
    sources: list[str] = Field(default_factory=list)  # e.g. ["4 bookings", "3 surveys"]


class ProactiveFlag(BaseModel):
    """
    A proactive alert for the agent surfaced from service request history (Epic 4.1).

    Generated by the Intelligence Agent when service_patterns from the Sentiment
    Agent reveal repeated issues or slow responses the guest has not yet raised.
    """
    severity: str        # "info" | "warning" | "critical"
    department: str      # e.g. "F&B"
    message: str         # agent-facing text, e.g. "Slow room service on last 2 Orlando stays—acknowledge proactively"
    occurrences: int = 0  # how many times this pattern was observed
    properties: list[str] = Field(default_factory=list)  # affected property names


class GuestIntelligence(BaseModel):
    """
    Pattern-based guest intelligence produced by the Intelligence Agent.

    Replaces the property-recommendation output as the primary AI panel content.
    The briefing is a natural-language agent-facing summary; insights are
    categorized pattern cards for drill-down.

    Epic 4.1 adds proactive_flags — service request alerts surfaced before
    the guest raises them.
    """
    briefing: str  # 2-3 sentence concise agent briefing
    insights: list[GuestInsight] = Field(default_factory=list)
    proactive_flags: list[ProactiveFlag] = Field(default_factory=list)  # Epic 4.1
    content_safety_passed: bool = True
    safe_fallback_used: bool = False
    intelligence_ms: float = 0.0


# ---------------------------------------------------------------------------
# Top-level aggregate — the contract between all agents
# ---------------------------------------------------------------------------

class GuestContext(BaseModel):
    """
    Complete guest context produced by the Retrieval Agent.

    This is the single data object passed through the pipeline:
      Retrieval (creates) → Sentiment (reads) → Intelligence (reads) → Validation (reads)

    The Reasoning Agent (generate_recommendations) is available on-demand
    via /api/recommend but is no longer part of the primary pipeline.
    """
    guest: Optional[GuestProfile] = None
    bookings: list[Booking] = []
    surveys: list[Survey] = []
    experiences: list[Experience] = []
    inventory: list[InventorySlot] = []
    service_requests: list[ServiceRequest] = []  # Epic 4.1 — HotSOS service request history

    # Metadata
    retrieval_ms: float = 0.0  # total retrieval time in milliseconds

    # Populated by Sentiment Agent (Epic 2.2)
    sentiment: Optional[SentimentResult] = None

    # Populated by Intelligence Agent (Epic 3.3) — primary AI output
    intelligence: Optional[GuestIntelligence] = None

    # Populated by Reasoning Agent (Epic 2.3) — on-demand only via /api/recommend
    reasoning: Optional[ReasoningResult] = None

    # Populated by Validation Agent (Epic 2.4)
    validation: Optional[ValidationResult] = None
