"""
reasoning.py — Reasoning Agent (Epic 2.3)

Consumes a GuestContext (with sentiment already populated) and generates
personalized property + experience recommendations via gpt-4o-mini.

Auto-infers one of three scenarios:
  enrich_upcoming  — Guest has a confirmed upcoming booking; suggest add-ons.
  date_change      — Upcoming booking is at a low-rated property; suggest alt.
  new_trip         — No upcoming booking; recommend a new destination.

Decisions (ADR-011/012/013):
  - Output shape: property + experiences + narrative.
  - Context size: 14-day inventory window + relevant properties only.
  - Scenario: auto-inferred from booking + sentiment data.
"""

import json
import logging
import time
from datetime import date, timedelta
from typing import Optional

from llm import chat_completion
from models import (
    ExperienceRecommendation,
    GuestContext,
    GuestInsight,
    GuestIntelligence,
    ProactiveFlag,
    Recommendation,
    ReasoningResult,
)

log = logging.getLogger("reasoning")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Properties with avg_overall below this score are "negative" (trigger date_change)
_NEGATIVE_THRESHOLD: float = 6.0

# Inventory look-ahead window (days from today)
_INVENTORY_WINDOW_DAYS: int = 14

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a luxury hotel concierge AI for Contoso Resorts, an elite multi-property resort brand.
Your role is to generate personalized travel recommendations based on a guest's history, preferences, and sentiment signals.

You will receive a JSON payload with:
- guest:                 Profile, loyalty tier, preferences
- scenario:              One of enrich_upcoming | date_change | new_trip
- upcoming_booking:      Guest's next confirmed stay (if any)
- sentiment:             Per-property avg scores + positive/negative themes
- available_properties:  Properties with room availability in the next 14 days
- relevant_experiences:  Curated experiences at relevant properties

Rules:
- NEVER recommend a property with avg_overall < 5.
- For enrich_upcoming: Recommend 2-3 experiences at the already-booked property. Do NOT suggest a different property.
- For date_change:      Recommend exactly 1 alternative property with better sentiment + 2 experiences there. Politely explain why the alternative is a better fit.
- For new_trip:         Recommend 1-2 top-rated properties with availability, plus 2-3 experiences each.
- Personalize with the guest's name, loyalty tier, and known preferences/themes.
- Keep each narrative to 2-4 warm, aspirational sentences.

Respond ONLY with valid JSON — no markdown, no code fences — in this exact schema:
{
  "scenario": "enrich_upcoming|date_change|new_trip",
  "recommendations": [
    {
      "property_id": "P-XXX",
      "property_name": "...",
      "scenario": "enrich_upcoming|date_change|new_trip",
      "headline": "One-line teaser (max 100 chars)",
      "narrative": "Concierge-style 2-4 sentence recommendation",
      "booking_id": "B-XXXXX or null",
      "experiences": [
        {
          "experience_id": "E-XXX",
          "name": "...",
          "category": "...",
          "description": "...",
          "price": 0.00
        }
      ]
    }
  ]
}"""


# ---------------------------------------------------------------------------
# Scenario inference
# ---------------------------------------------------------------------------

def _infer_scenario(context: GuestContext) -> tuple[str, Optional[object]]:
    """
    Determine which of the three scenarios applies for this guest.

    Returns (scenario_name, upcoming_booking_or_None).
    """
    today = date.today()
    upcoming = [
        b for b in context.bookings
        if b.Status == "Confirmed" and b.CheckIn >= today
    ]
    if not upcoming:
        return "new_trip", None

    # Nearest upcoming stay
    upcoming.sort(key=lambda b: b.CheckIn)
    next_booking = upcoming[0]

    # Check sentiment for that property
    if context.sentiment:
        prop_sentiment = next(
            (
                p for p in context.sentiment.properties
                if p.property_id == next_booking.PropertyID
            ),
            None,
        )
        if (
            prop_sentiment is not None
            and prop_sentiment.avg_overall is not None
            and prop_sentiment.avg_overall < _NEGATIVE_THRESHOLD
        ):
            return "date_change", next_booking

    return "enrich_upcoming", next_booking


# ---------------------------------------------------------------------------
# Context serialization (strips bulk data, keeps signals)
# ---------------------------------------------------------------------------

def _build_payload(context: GuestContext) -> dict:
    """
    Build the filtered JSON payload sent to gpt-4o-mini.

    Filtering strategy (ADR-012):
    - Inventory: next 14 days only, grouped by property (date list, no per-room-type rows)
    - Experiences: only properties that are relevant to the inferred scenario
    - Sentiment: exclude properties with avg_overall < 5 entirely
    """
    today = date.today()
    window_end = today + timedelta(days=_INVENTORY_WINDOW_DAYS)

    scenario, upcoming_booking = _infer_scenario(context)

    # --- Inventory: 14-day window, one entry per property with available date list ---
    prop_dates: dict[str, dict] = {}
    for slot in context.inventory:
        if today <= slot.Date <= window_end and slot.Available > 0:
            pid = slot.PropertyID
            if pid not in prop_dates:
                prop_dates[pid] = {
                    "property_id": pid,
                    "property_name": slot.PropertyName,
                    "available_dates": set(),
                }
            prop_dates[pid]["available_dates"].add(slot.Date.isoformat())

    available_properties = [
        {
            "property_id": v["property_id"],
            "property_name": v["property_name"],
            "available_dates": sorted(v["available_dates"]),
        }
        for v in prop_dates.values()
    ]

    # --- Sentiment: only well-rated properties (avg_overall >= 5) ---
    good_sentiments = []
    if context.sentiment:
        for ps in context.sentiment.properties:
            if ps.avg_overall is None or ps.avg_overall >= 5:
                good_sentiments.append({
                    "property_id": ps.property_id,
                    "property_name": ps.property_name,
                    "avg_overall": ps.avg_overall,
                    "avg_nps": ps.avg_nps,
                    "positive_themes": ps.positive_themes,
                    "negative_themes": ps.negative_themes,
                })

    # --- Experiences: limit to relevant property IDs ---
    relevant_pids: set[str] = set()
    if upcoming_booking:
        relevant_pids.add(upcoming_booking.PropertyID)
    # Include top-2 by avg_overall for new_trip / date_change fallback
    sorted_props = sorted(
        [s for s in good_sentiments if s["avg_overall"] is not None],
        key=lambda s: s["avg_overall"],
        reverse=True,
    )
    for s in sorted_props[:2]:
        relevant_pids.add(s["property_id"])

    relevant_experiences = [
        {
            "experience_id": e.ExperienceID,
            "property_id": e.PropertyID,
            "property_name": e.PropertyName,
            "name": e.Name,
            "category": e.Category,
            "description": e.Description,
            "price": float(e.Price),
        }
        for e in context.experiences
        if e.PropertyID in relevant_pids and e.Available
    ]

    # --- Guest summary ---
    guest_summary = None
    if context.guest:
        guest_summary = {
            "first_name": context.guest.FirstName,
            "last_name": context.guest.LastName,
            "loyalty_tier": context.guest.LoyaltyTier,
            "home_city": context.guest.HomeCity,
            "preferences": context.guest.Preferences,
        }

    # --- Upcoming booking summary ---
    upcoming_summary = None
    if upcoming_booking:
        upcoming_summary = {
            "booking_id": upcoming_booking.BookingID,
            "property_id": upcoming_booking.PropertyID,
            "property_name": upcoming_booking.PropertyName,
            "check_in": upcoming_booking.CheckIn.isoformat(),
            "check_out": upcoming_booking.CheckOut.isoformat(),
            "room_type": upcoming_booking.RoomType,
            "special_requests": upcoming_booking.SpecialRequests,
        }

    return {
        "guest": guest_summary,
        "scenario": scenario,
        "upcoming_booking": upcoming_summary,
        "sentiment": good_sentiments,
        "available_properties": available_properties,
        "relevant_experiences": relevant_experiences,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def generate_recommendations(context: GuestContext) -> ReasoningResult:
    """
    Run the Reasoning Agent.

    Reads GuestContext (retrieval + sentiment must be populated) and writes
    a ReasoningResult back to context.reasoning.

    Returns the ReasoningResult.
    """
    t0 = time.perf_counter()
    guest_label = (
        f"{context.guest.FirstName} {context.guest.LastName}"
        if context.guest
        else "unknown"
    )
    log.info("Reasoning Agent starting for %s", guest_label)

    payload = _build_payload(context)
    scenario = payload["scenario"]
    log.info(
        "Scenario=%s  properties=%d  experiences=%d",
        scenario,
        len(payload["available_properties"]),
        len(payload["relevant_experiences"]),
    )

    user_message = json.dumps(payload, ensure_ascii=False, default=str)

    # --- Call gpt-4o-mini ---
    raw = await chat_completion(
        system_prompt=_SYSTEM_PROMPT,
        user_message=user_message,
        max_tokens=1500,
    )

    # --- Parse JSON response ---
    result_data: dict = {}
    try:
        cleaned = raw.strip()
        # Strip code fences in case the model disobeyed the "no markdown" rule
        if cleaned.startswith("```"):
            parts = cleaned.split("```")
            cleaned = parts[1] if len(parts) > 1 else parts[0]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        result_data = json.loads(cleaned)
    except json.JSONDecodeError:
        log.warning(
            "Reasoning Agent: JSON parse failed, returning empty result. raw=%r",
            raw[:300],
        )
        elapsed = (time.perf_counter() - t0) * 1000
        result = ReasoningResult(scenario=scenario, reasoning_ms=elapsed)
        context.reasoning = result
        return result

    # --- Map to Pydantic models ---
    recommendations: list[Recommendation] = []
    for r in result_data.get("recommendations", []):
        experiences = [
            ExperienceRecommendation(
                experience_id=e.get("experience_id", ""),
                name=e.get("name", ""),
                category=e.get("category", ""),
                description=e.get("description", ""),
                price=e.get("price", 0.0),
            )
            for e in r.get("experiences", [])
        ]
        recommendations.append(
            Recommendation(
                property_id=r.get("property_id", ""),
                property_name=r.get("property_name", ""),
                scenario=r.get("scenario", scenario),
                headline=r.get("headline", ""),
                narrative=r.get("narrative", ""),
                booking_id=r.get("booking_id"),
                experiences=experiences,
            )
        )

    elapsed = (time.perf_counter() - t0) * 1000
    log.info(
        "Reasoning Agent complete — scenario=%s  recs=%d  %.0fms",
        scenario,
        len(recommendations),
        elapsed,
    )

    result = ReasoningResult(
        scenario=scenario,
        recommendations=recommendations,
        reasoning_ms=elapsed,
    )
    context.reasoning = result
    return result


# ---------------------------------------------------------------------------
# Intelligence Agent (Epic 3.3) — pattern analysis, not property recommendations
# ---------------------------------------------------------------------------

_INTELLIGENCE_SYSTEM_PROMPT = """\
You are a hotel agent intelligence analyst for Contoso Resorts.
Your role is NOT to recommend properties or experiences.
Your role is to analyze a guest's history across bookings, surveys, requests, and service incidents,
then produce a concise intelligence briefing and categorized insight cards for the
agent who is about to speak with this guest.

You will receive a JSON payload with:
- guest:            Profile, loyalty tier, home city, preferences, total stays
- bookings:         Chronological stay history (property, dates, room type, special requests)
- surveys:          Post-stay feedback (numeric ratings by category + free text comments)
- sentiment:        LLM-extracted themes and cross-property preferences (if available)
- service_patterns: Rule-detected service request patterns (repeat categories, slow responses)

Your output must contain:
1. A "briefing" — 2-3 sentence natural-language summary an agent reads before picking up the phone.
   Focus on: who this guest is, what matters most to them, the biggest risk or opportunity right now.
   If there are proactive flags, MENTION the most critical one in the briefing.
   Do NOT say "I recommend..." or suggest properties. Write it as a briefing to the agent.

2. An "insights" array — 4 to 8 insight cards, covering all four types:
   - "likes":            What the guest consistently enjoys and rates highly (evidence from bookings/surveys)
   - "pain_points":      Recurring frustrations, low scores, negative free-text themes, or a declining NPS
   - "request_patterns": Special requests that appear repeatedly across stays
   - "cross_property":   Patterns that span multiple properties — what correlates with high vs low scores

3. A "proactive_flags" array — 0 to 3 alerts derived from service_patterns. Only include flags when
   there is a real pattern that the agent should proactively acknowledge with the guest.
   Each flag should be actionable: tell the agent what signal was detected and what to do.
   Severity: "critical" = slow repeated issue; "warning" = repeat only; "info" = minor pattern.

Rules:
- Every insight must be evidence-based. Cite the source (e.g. "3 of 5 surveys", "mentioned in 2 stays").
- Keep "title" to 8 words or fewer — it is a card heading.
- Keep "detail" to 1-2 sentences maximum.
- "sources" should be a short list of evidence references, e.g. ["4 bookings", "3 surveys", "2 stays"].
- Do NOT invent data. If there is not enough data for a category, omit it rather than guess.
- Do NOT mention specific prices, room numbers, or future availability.
- Do NOT suggest a specific property, booking, or package.
- If service_patterns is empty, return an empty proactive_flags array.

Respond ONLY with valid JSON — no markdown, no code fences — in this exact schema:
{
  "briefing": "2-3 sentence agent briefing...",
  "insights": [
    {
      "type": "likes|pain_points|request_patterns|cross_property",
      "title": "Short card heading",
      "detail": "1-2 sentences of evidence-backed context.",
      "sources": ["N bookings", "N surveys"]
    }
  ],
  "proactive_flags": [
    {
      "severity": "critical|warning|info",
      "department": "F&B",
      "message": "Agent-facing text: what happened and what to say/do.",
      "occurrences": 2,
      "properties": ["Contoso Orlando Family Resort"]
    }
  ]
}"""


def _build_intelligence_payload(context: GuestContext) -> dict:
    """
    Build the filtered JSON payload sent to gpt-4o-mini for intelligence analysis.

    Includes booking history, survey data, and sentiment themes.
    Strips bulk inventory and experience data — not needed for pattern analysis.
    """
    guest_summary = None
    if context.guest:
        guest_summary = {
            "first_name": context.guest.FirstName,
            "last_name": context.guest.LastName,
            "loyalty_tier": context.guest.LoyaltyTier,
            "loyalty_points": context.guest.LoyaltyPoints,
            "home_city": context.guest.HomeCity,
            "preferences": context.guest.Preferences,
            "total_stays": len(context.bookings),
        }

    # Booking history — chronological, include room type and special requests
    booking_history = [
        {
            "property_name": b.PropertyName,
            "check_in": b.CheckIn.isoformat(),
            "check_out": b.CheckOut.isoformat(),
            "nights": (b.CheckOut - b.CheckIn).days,
            "room_type": b.RoomType,
            "status": b.Status,
            "special_requests": b.SpecialRequests or "",
        }
        for b in sorted(context.bookings, key=lambda b: b.CheckIn, reverse=True)
    ]

    # Survey history — include numeric categories + free text
    survey_history = [
        {
            "property_id": s.PropertyID,
            "date": s.SubmittedDate.isoformat(),
            "overall": s.OverallRating,
            "nps": s.NPS,
            "cleanliness": s.Cleanliness,
            "service": s.Service,
            "food_beverage": s.FoodBeverage,
            "spa": s.Spa,
            "activities": s.Activities,
            "free_text": s.FreeText or "",
        }
        for s in sorted(context.surveys, key=lambda s: s.SubmittedDate, reverse=True)
    ]

    # Sentiment themes (LLM-extracted, if available)
    sentiment_summary = None
    if context.sentiment:
        sentiment_summary = {
            "overall_preferences": context.sentiment.overall_preferences,
            "properties": [
                {
                    "property_name": ps.property_name,
                    "avg_overall": ps.avg_overall,
                    "survey_count": ps.survey_count,
                    "positive_themes": ps.positive_themes,
                    "negative_themes": ps.negative_themes,
                }
                for ps in context.sentiment.properties
            ],
        }

    # Service patterns (rule-detected, from Sentiment Agent Epic 4.1)
    service_patterns_summary = []
    if context.sentiment and context.sentiment.service_patterns:
        service_patterns_summary = [
            {
                "department": sp.department,
                "category": sp.category,
                "occurrence_count": sp.occurrence_count,
                "slow_responses": sp.slow_responses,
                "avg_response_minutes": sp.avg_response_minutes,
                "is_repeat": sp.is_repeat,
                "is_slow": sp.is_slow,
                "property_names": sp.property_names,
                "flag_message": sp.flag_message,
            }
            for sp in context.sentiment.service_patterns
        ]

    return {
        "guest": guest_summary,
        "bookings": booking_history,
        "surveys": survey_history,
        "sentiment": sentiment_summary,
        "service_patterns": service_patterns_summary,
    }


async def generate_intelligence(context: GuestContext) -> GuestIntelligence:
    """
    Run the Intelligence Agent (Epic 3.3).

    Reads GuestContext (retrieval + sentiment should be populated) and produces
    a GuestIntelligence object containing a briefing + insight cards.

    Writes result to context.intelligence and returns it.
    Does NOT produce property recommendations — use generate_recommendations() for that.
    """
    t0 = time.perf_counter()
    guest_label = (
        f"{context.guest.FirstName} {context.guest.LastName}"
        if context.guest
        else "unknown"
    )
    log.info("Intelligence Agent starting for %s", guest_label)

    payload = _build_intelligence_payload(context)
    log.info(
        "Intelligence payload: %d bookings, %d surveys",
        len(payload["bookings"]),
        len(payload["surveys"]),
    )

    user_message = json.dumps(payload, ensure_ascii=False, default=str)

    # --- Call gpt-4o-mini ---
    raw = await chat_completion(
        system_prompt=_INTELLIGENCE_SYSTEM_PROMPT,
        user_message=user_message,
        max_tokens=1200,
    )

    # --- Parse JSON response ---
    result_data: dict = {}
    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            parts = cleaned.split("```")
            cleaned = parts[1] if len(parts) > 1 else parts[0]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        result_data = json.loads(cleaned)
    except json.JSONDecodeError:
        log.warning(
            "Intelligence Agent: JSON parse failed, returning minimal result. raw=%r",
            raw[:300],
        )
        elapsed = (time.perf_counter() - t0) * 1000
        result = GuestIntelligence(
            briefing="Guest intelligence is being prepared. Please review the guest profile manually.",
            insights=[],
            intelligence_ms=elapsed,
        )
        context.intelligence = result
        return result

    # --- Map to Pydantic models ---
    insights: list[GuestInsight] = []
    for item in result_data.get("insights", []):
        insights.append(
            GuestInsight(
                type=item.get("type", "likes"),
                title=item.get("title", ""),
                detail=item.get("detail", ""),
                sources=item.get("sources", []),
            )
        )

    proactive_flags: list[ProactiveFlag] = []
    for flag in result_data.get("proactive_flags", []):
        proactive_flags.append(
            ProactiveFlag(
                severity=flag.get("severity", "info"),
                department=flag.get("department", ""),
                message=flag.get("message", ""),
                occurrences=flag.get("occurrences", 0),
                properties=flag.get("properties", []),
            )
        )

    elapsed = (time.perf_counter() - t0) * 1000
    log.info(
        "Intelligence Agent complete — briefing=%d chars  insights=%d  flags=%d  %.0fms",
        len(result_data.get("briefing", "")),
        len(insights),
        len(proactive_flags),
        elapsed,
    )

    result = GuestIntelligence(
        briefing=result_data.get("briefing", ""),
        insights=insights,
        proactive_flags=proactive_flags,
        intelligence_ms=elapsed,
    )
    context.intelligence = result
    return result
