"""
sentiment.py — Sentiment Agent (Epic 2.2)

Takes the GuestContext produced by the Retrieval Agent and analyses survey
data to produce a SentimentResult with per-property sentiment breakdowns.

Two-stage analysis:
  1. Rule-based ratings analysis (pure Python) — computes per-property
     average scores for all rating categories, flags highs and lows.
  2. LLM free-text mining (gpt-4o-mini) — one batched call extracts
     positive themes, negative themes, and cross-property preferences
     from all FreeText survey entries.

Results are written back to context.sentiment.
"""

import json
import logging
import time
from collections import defaultdict
from statistics import mean
from typing import Optional

from llm import chat_completion
from models import GuestContext, PropertySentiment, SentimentResult, ServicePattern, Survey

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# System prompt for free-text theme extraction
# ---------------------------------------------------------------------------

_FREETEXT_SYSTEM_PROMPT = """\
You are a hotel guest experience analyst. You will receive post-stay survey
free-text comments from a single guest across one or more hotel properties.

Your job: extract structured sentiment insights from these comments.

Return ONLY a JSON object with this exact structure (no markdown, no extra text):
{
  "properties": {
    "<PropertyID>": {
      "positive_themes": ["theme1", "theme2"],
      "negative_themes": ["theme1", "theme2"]
    }
  },
  "overall_preferences": ["preference1", "preference2"]
}

Rules:
- Keep each theme concise (3-8 words).
- Only include themes supported by the text — do not invent.
- positive_themes: things the guest praised or enjoyed.
- negative_themes: things the guest criticised or disliked.
- overall_preferences: cross-property patterns (amenities, room types,
  service styles the guest consistently values or avoids).
- If a property has no free-text, omit it from "properties".
- If there are no cross-property preferences, return an empty list.
"""


# ---------------------------------------------------------------------------
# Rule-based ratings analysis (Task 2.2.1)
# ---------------------------------------------------------------------------

def _compute_property_averages(surveys: list[Survey]) -> dict[str, dict]:
    """
    Group surveys by PropertyID and compute average numeric ratings.
    Returns a dict keyed by PropertyID.
    """
    grouped: dict[str, list[Survey]] = defaultdict(list)
    for s in surveys:
        grouped[s.PropertyID].append(s)

    result = {}
    for prop_id, prop_surveys in grouped.items():
        def avg(values: list[Optional[int]]) -> Optional[float]:
            valid = [v for v in values if v is not None]
            return round(mean(valid), 2) if valid else None

        result[prop_id] = {
            "survey_count":    len(prop_surveys),
            "avg_overall":     avg([s.OverallRating for s in prop_surveys]),
            "avg_nps":         avg([s.NPS for s in prop_surveys]),
            "avg_cleanliness": avg([s.Cleanliness for s in prop_surveys]),
            "avg_service":     avg([s.Service for s in prop_surveys]),
            "avg_food_beverage": avg([s.FoodBeverage for s in prop_surveys]),
            "avg_spa":         avg([s.Spa for s in prop_surveys]),
            "avg_activities":  avg([s.Activities for s in prop_surveys]),
        }

    return result


# ---------------------------------------------------------------------------
# LLM free-text mining (Task 2.2.2)
# ---------------------------------------------------------------------------

def _build_freetext_user_message(surveys: list[Survey], property_names: dict[str, str]) -> str:
    """
    Construct the user message for the LLM: all FreeText entries grouped
    by property with enough context for theme extraction.
    """
    freetext_surveys = [s for s in surveys if s.FreeText and s.FreeText.strip()]
    if not freetext_surveys:
        return ""

    lines = []
    for s in freetext_surveys:
        pname = property_names.get(s.PropertyID, s.PropertyID)
        lines.append(
            f"[PropertyID: {s.PropertyID} | Property: {pname} | "
            f"Date: {s.SubmittedDate} | Overall: {s.OverallRating}/10]\n"
            f"\"{s.FreeText.strip()}\""
        )

    return "\n\n".join(lines)


async def _extract_freetext_themes(
    surveys: list[Survey],
    property_names: dict[str, str],
) -> tuple[dict[str, dict], list[str]]:
    """
    Call gpt-4o-mini to extract themes from free-text.
    Returns (per_property_themes, overall_preferences).
    """
    user_msg = _build_freetext_user_message(surveys, property_names)
    if not user_msg:
        log.info("Sentiment Agent: no free-text surveys — skipping LLM call")
        return {}, []

    log.info("Sentiment Agent: calling LLM for free-text mining (%d chars)", len(user_msg))
    raw = await chat_completion(_FREETEXT_SYSTEM_PROMPT, user_msg, max_tokens=600)

    # Parse JSON response
    try:
        data = json.loads(raw)
        per_property = data.get("properties", {})
        preferences = data.get("overall_preferences", [])
        log.info(
            "Sentiment Agent: LLM returned themes for %d properties, %d preferences",
            len(per_property),
            len(preferences),
        )
        return per_property, preferences
    except json.JSONDecodeError:
        log.warning("Sentiment Agent: LLM response was not valid JSON — %s", raw[:200])
        return {}, []


# ---------------------------------------------------------------------------
# Service request pattern analysis (Task 4.1.5)
# ---------------------------------------------------------------------------

_SLOW_RESPONSE_MINUTES: int = 60          # threshold: > 60 min on High/Urgent = slow
_REPEAT_THRESHOLD: int = 2                # ≥2 occurrences = a pattern
_SLOW_PRIORITIES: frozenset[str] = frozenset({"High", "Urgent"})


def _analyze_service_requests(context: GuestContext) -> list[ServicePattern]:
    """
    Rule-based analysis of service requests.

    Groups by (department, category) across all requests in context,
    flags repeated categories and slow responses.
    Returns a list of ServicePattern objects — only noteworthy patterns
    (repeat category OR any slow response).
    """
    if not context.service_requests:
        return []

    # Build a lookup of PropertyID → PropertyName from bookings
    prop_names: dict[str, str] = {}
    for b in context.bookings:
        prop_names[b.PropertyID] = b.PropertyName
    for e in context.experiences:
        prop_names[e.PropertyID] = e.PropertyName

    # Group by (department, category)
    from collections import defaultdict
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for sr in context.service_requests:
        groups[(sr.Department, sr.Category)].append(sr)

    patterns: list[ServicePattern] = []
    for (dept, cat), requests in groups.items():
        slow = [
            r for r in requests
            if r.ResponseMinutes is not None
            and r.Priority in _SLOW_PRIORITIES
            and r.ResponseMinutes > _SLOW_RESPONSE_MINUTES
        ]
        count = len(requests)
        is_repeat = count >= _REPEAT_THRESHOLD
        is_slow = len(slow) > 0

        if not (is_repeat or is_slow):
            continue   # only surface meaningful patterns

        response_times = [
            r.ResponseMinutes for r in requests if r.ResponseMinutes is not None
        ]
        avg_resp = round(sum(response_times) / len(response_times), 1) if response_times else None

        # Collect unique property names where this occurred
        seen_props = sorted({
            prop_names.get(r.PropertyID, r.PropertyID) for r in requests
        })

        # Build a concise human-readable flag message
        parts: list[str] = []
        if is_slow:
            parts.append(
                f"Slow {dept} response on {len(slow)} request(s) "
                f"({', '.join(seen_props)})"
            )
        if is_repeat:
            parts.append(
                f"‘{cat}’ requested {count}x across stays"
            )
        flag_msg = " — ".join(parts)

        patterns.append(ServicePattern(
            department=dept,
            category=cat,
            occurrence_count=count,
            slow_responses=len(slow),
            avg_response_minutes=avg_resp,
            is_repeat=is_repeat,
            is_slow=is_slow,
            property_names=seen_props,
            flag_message=flag_msg,
        ))

    # Sort: slow-and-repeat first, then slow only, then repeat only
    patterns.sort(key=lambda p: (not (p.is_slow and p.is_repeat), not p.is_slow, not p.is_repeat))
    log.info("Sentiment Agent: %d service pattern(s) detected", len(patterns))
    return patterns


# ---------------------------------------------------------------------------
# Public API — the Sentiment Agent entry point (Task 2.2.3)
# ---------------------------------------------------------------------------

async def analyze_sentiment(context: GuestContext) -> SentimentResult:
    """
    Sentiment Agent: analyse surveys in the GuestContext and return a
    SentimentResult with per-property breakdowns and cross-property
    preferences.

    Mutates context.sentiment in-place and also returns the result.
    """
    surveys = context.surveys
    log.info("Sentiment Agent started — %d surveys", len(surveys))

    if not surveys:
        log.info("Sentiment Agent: no surveys — returning empty result")
        result = SentimentResult()
        context.sentiment = result
        return result

    start = time.perf_counter()

    # Build property name lookup from bookings (and surveys if available)
    property_names: dict[str, str] = {}
    for b in context.bookings:
        property_names[b.PropertyID] = b.PropertyName
    for e in context.experiences:
        property_names[e.PropertyID] = e.PropertyName

    # Run ratings analysis (pure Python) and LLM free-text mining concurrently
    import asyncio
    ratings_task = asyncio.to_thread(_compute_property_averages, surveys)
    themes_task  = _extract_freetext_themes(surveys, property_names)
    patterns_task = asyncio.to_thread(_analyze_service_requests, context)

    (ratings_by_prop, (prop_themes, overall_preferences), service_patterns) = await asyncio.gather(
        ratings_task,
        themes_task,
        patterns_task,
    )

    # Build PropertySentiment objects — merge ratings + LLM themes (Task 2.2.3)
    property_sentiments: list[PropertySentiment] = []
    for prop_id, ratings in ratings_by_prop.items():
        themes = prop_themes.get(prop_id, {})
        ps = PropertySentiment(
            property_id=prop_id,
            property_name=property_names.get(prop_id, prop_id),
            **ratings,
            positive_themes=themes.get("positive_themes", []),
            negative_themes=themes.get("negative_themes", []),
        )
        property_sentiments.append(ps)

    # Sort by avg_overall descending (best-rated first)
    property_sentiments.sort(
        key=lambda p: p.avg_overall if p.avg_overall is not None else 0,
        reverse=True,
    )

    elapsed_ms = (time.perf_counter() - start) * 1000
    log.info(
        "Sentiment Agent completed in %.0fms — %d properties, %d preferences, %d service patterns",
        elapsed_ms,
        len(property_sentiments),
        len(overall_preferences),
        len(service_patterns),
    )

    result = SentimentResult(
        properties=property_sentiments,
        overall_preferences=overall_preferences,
        service_patterns=service_patterns,
        sentiment_ms=round(elapsed_ms, 1),
    )
    context.sentiment = result
    return result
