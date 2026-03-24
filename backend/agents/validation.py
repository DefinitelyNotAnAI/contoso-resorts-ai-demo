"""
validation.py — Validation Agent (Epic 2.4)

The fourth and final agent in the sequential pipeline. Acts as a responsible AI
guardrail on the Reasoning Agent's output, running two independent checks:

  1. Quality check (2.4.1) — Surgical mode
     Removes any recommendation where the property's avg_overall sentiment
     score is below the quality threshold (< 5.0).  This is the enforcement
     layer: the Reasoning Agent tries to self-censor, the Validation Agent
     guarantees it.  Each removal is recorded as a ValidationFlag with
     severity="blocked".

  2. Content Safety check (2.4.2 / 2.4.3) — Nuclear mode
     Passes all LLM-generated text (headline + narrative from every surviving
     recommendation) through the Azure Content Safety API.  If any text is
     flagged, ALL recommendation narratives are replaced with a safe fallback
     message and safe_fallback_used=True is set on the ValidationResult.

Failure modes:
  - Quality:          Surgical — strip individual bad recs, preserve good ones.
  - Content Safety:   Nuclear — if flagged, replace ALL narratives.
  - API error:        Graceful — log, add a warning flag, do NOT block recs.

ADRs:
  - ADR-002: DefaultAzureCredential for all Azure auth (no keys)
  - ADR-004: Content Safety on all AI-generated text
"""

import asyncio
import logging
import os
import time
from typing import Optional

from azure.ai.contentsafety import ContentSafetyClient
from azure.ai.contentsafety.models import AnalyzeTextOptions, TextCategory
from azure.core.exceptions import HttpResponseError
from azure.identity import DefaultAzureCredential

from models import (
    GuestContext,
    GuestIntelligence,
    Recommendation,
    ValidationFlag,
    ValidationResult,
)

log = logging.getLogger("validation")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Properties below this avg_overall score are disqualified from recommendations.
# Matches the Reasoning Agent's "NEVER recommend avg_overall < 5" prompt rule.
_QUALITY_THRESHOLD: float = 5.0

# Text length limit for Content Safety API (max 10,000 chars per call)
_CONTENT_SAFETY_MAX_CHARS: int = 9_000

# Safe fallback text used when Content Safety flags any narrative
_SAFE_FALLBACK_NARRATIVE = (
    "We are preparing a personalized recommendation for you. "
    "Please contact your dedicated concierge for tailored suggestions."
)
_SAFE_FALLBACK_HEADLINE = "Your concierge is ready to assist"

# ---------------------------------------------------------------------------
# Content Safety client (lazy singleton, synchronous SDK)
# ---------------------------------------------------------------------------

_content_safety_client: Optional[ContentSafetyClient] = None


def _get_content_safety_client() -> Optional[ContentSafetyClient]:
    """
    Return a ContentSafetyClient, or None if the endpoint is not configured.
    Singleton — created once per process.
    """
    global _content_safety_client
    if _content_safety_client is not None:
        return _content_safety_client

    endpoint = os.getenv("CONTENT_SAFETY_ENDPOINT", "").strip()
    if not endpoint:
        log.warning("CONTENT_SAFETY_ENDPOINT not set — skipping Content Safety scan")
        return None

    credential = DefaultAzureCredential()
    _content_safety_client = ContentSafetyClient(endpoint=endpoint, credential=credential)
    log.info("ContentSafetyClient initialised → %s", endpoint)
    return _content_safety_client


# ---------------------------------------------------------------------------
# Job 1 — Quality check
# ---------------------------------------------------------------------------

def _check_quality(
    context: GuestContext,
    flags: list[ValidationFlag],
) -> list[Recommendation]:
    """
    Remove recommendations for properties with avg_overall < _QUALITY_THRESHOLD.

    Returns: list of surviving Recommendation objects.
    Mutates: flags list (adds ValidationFlag for each removed property).
    """
    if not context.reasoning or not context.reasoning.recommendations:
        return []

    # Build a lookup: property_id → avg_overall (from sentiment)
    scores: dict[str, Optional[float]] = {}
    if context.sentiment:
        for ps in context.sentiment.properties:
            scores[ps.property_id] = ps.avg_overall

    surviving: list[Recommendation] = []
    for rec in context.reasoning.recommendations:
        avg = scores.get(rec.property_id)
        if avg is not None and avg < _QUALITY_THRESHOLD:
            msg = (
                f"Removed recommendation for '{rec.property_name}' "
                f"(property_id={rec.property_id}): avg_overall={avg:.1f} "
                f"is below quality threshold {_QUALITY_THRESHOLD}"
            )
            log.warning(msg)
            flags.append(
                ValidationFlag(
                    field="property_id",
                    message=msg,
                    severity="blocked",
                )
            )
        else:
            surviving.append(rec)

    return surviving


# ---------------------------------------------------------------------------
# Job 2 — Content Safety scan (synchronous SDK, wrapped for async)
# ---------------------------------------------------------------------------

def _build_scan_text(recommendations: list[Recommendation]) -> str:
    """Concatenate all LLM-generated text from surviving recommendations."""
    parts: list[str] = []
    for rec in recommendations:
        if rec.headline:
            parts.append(rec.headline)
        if rec.narrative:
            parts.append(rec.narrative)
    return "\n\n".join(parts)


def _run_content_safety_scan(text: str) -> tuple[bool, str]:
    """
    Synchronous Content Safety scan.  Called via asyncio.to_thread.

    Returns (passed: bool, detail: str).
    """
    client = _get_content_safety_client()
    if client is None:
        return True, "Content Safety client not configured — scan skipped"

    # Truncate to API limit
    if len(text) > _CONTENT_SAFETY_MAX_CHARS:
        log.warning(
            "Content Safety: text truncated from %d to %d chars",
            len(text),
            _CONTENT_SAFETY_MAX_CHARS,
        )
        text = text[:_CONTENT_SAFETY_MAX_CHARS]

    response = client.analyze_text(
        AnalyzeTextOptions(
            text=text,
            categories=[
                TextCategory.HATE,
                TextCategory.VIOLENCE,
                TextCategory.SELF_HARM,
                TextCategory.SEXUAL,
            ],
        )
    )

    for item in response.categories_analysis:
        if item.severity is not None and item.severity > 0:
            detail = f"Flagged: category={item.category} severity={item.severity}"
            log.warning("Content Safety: %s", detail)
            return False, detail

    return True, "All categories clean"


# ---------------------------------------------------------------------------
# Nuclear fallback — replace all narratives when Content Safety fires
# ---------------------------------------------------------------------------

def _apply_safe_fallback(recommendations: list[Recommendation]) -> list[Recommendation]:
    """Return copies of each recommendation with narratives replaced by safe fallback."""
    safe: list[Recommendation] = []
    for rec in recommendations:
        safe.append(
            rec.model_copy(
                update={
                    "headline": _SAFE_FALLBACK_HEADLINE,
                    "narrative": _SAFE_FALLBACK_NARRATIVE,
                }
            )
        )
    return safe


# ---------------------------------------------------------------------------
# Intelligence Content Safety helpers (Epic 3.3)
# ---------------------------------------------------------------------------

_SAFE_FALLBACK_BRIEFING = (
    "Guest intelligence is available. Please review the guest profile "
    "for personalized service recommendations."
)


def _build_intelligence_scan_text(intelligence: GuestIntelligence) -> str:
    """Concatenate all LLM-generated text from intelligence output."""
    parts: list[str] = [intelligence.briefing] if intelligence.briefing else []
    for insight in intelligence.insights:
        if insight.title:
            parts.append(insight.title)
        if insight.detail:
            parts.append(insight.detail)
    return "\n\n".join(parts)


def _apply_intelligence_safe_fallback(intelligence: GuestIntelligence) -> GuestIntelligence:
    """Replace intelligence text with safe fallback and mark flags."""
    return intelligence.model_copy(
        update={
            "briefing": _SAFE_FALLBACK_BRIEFING,
            "insights": [],
            "safe_fallback_used": True,
            "content_safety_passed": False,
        }
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def validate_recommendations(context: GuestContext) -> ValidationResult:
    """
    Run the Validation Agent.

    Reads context.reasoning (must be populated) and writes a ValidationResult
    to context.validation.

    Returns: ValidationResult
    """
    t0 = time.perf_counter()
    guest_label = (
        f"{context.guest.FirstName} {context.guest.LastName}"
        if context.guest
        else "unknown"
    )
    log.info("Validation Agent starting for %s", guest_label)

    flags: list[ValidationFlag] = []

    # --- Job 1: Quality check ---
    surviving = _check_quality(context, flags)
    removed_count = (
        len(context.reasoning.recommendations) - len(surviving)
        if context.reasoning
        else 0
    )
    log.info(
        "Quality check complete — %d/%d recommendations survived (%d removed)",
        len(surviving),
        len(context.reasoning.recommendations) if context.reasoning else 0,
        removed_count,
    )

    # --- Job 2: Content Safety scan (on surviving recs only) ---
    content_safety_passed = True
    safe_fallback_used = False
    final_recommendations = surviving

    if surviving:
        scan_text = _build_scan_text(surviving)
        try:
            passed, detail = await asyncio.to_thread(_run_content_safety_scan, scan_text)
            content_safety_passed = passed
            log.info("Content Safety scan (recommendations): passed=%s — %s", passed, detail)

            if not passed:
                # Nuclear mode: replace all narratives
                final_recommendations = _apply_safe_fallback(surviving)
                safe_fallback_used = True
                flags.append(
                    ValidationFlag(
                        field="narrative",
                        message=f"Content Safety flagged recommendation text: {detail}",
                        severity="blocked",
                    )
                )
        except HttpResponseError as exc:
            log.error(
                "Content Safety API error (HTTP %s): %s — recommendations not blocked",
                exc.status_code,
                exc.message,
            )
            flags.append(
                ValidationFlag(
                    field="content_safety",
                    message=f"Content Safety API returned HTTP {exc.status_code}: {exc.message}",
                    severity="warning",
                )
            )
        except Exception as exc:
            log.error(
                "Content Safety scan failed unexpectedly: %s — recommendations not blocked",
                exc,
            )
            flags.append(
                ValidationFlag(
                    field="content_safety",
                    message=f"Content Safety scan error: {exc}",
                    severity="warning",
                )
            )

    # --- Job 3: Content Safety scan on intelligence output (Epic 3.3) ---
    if context.intelligence and context.intelligence.briefing:
        intelligence_scan_text = _build_intelligence_scan_text(context.intelligence)
        try:
            intel_passed, intel_detail = await asyncio.to_thread(
                _run_content_safety_scan, intelligence_scan_text
            )
            log.info("Content Safety scan (intelligence): passed=%s — %s", intel_passed, intel_detail)

            if not intel_passed:
                # Nuclear mode for intelligence: replace with safe fallback
                context.intelligence = _apply_intelligence_safe_fallback(context.intelligence)
                content_safety_passed = False
                flags.append(
                    ValidationFlag(
                        field="intelligence.briefing",
                        message=f"Content Safety flagged intelligence text: {intel_detail}",
                        severity="blocked",
                    )
                )
            else:
                context.intelligence = context.intelligence.model_copy(
                    update={"content_safety_passed": True}
                )
        except HttpResponseError as exc:
            log.error(
                "Content Safety API error scanning intelligence (HTTP %s): %s — not blocked",
                exc.status_code,
                exc.message,
            )
            flags.append(
                ValidationFlag(
                    field="intelligence.content_safety",
                    message=f"Content Safety API error scanning intelligence: {exc.message}",
                    severity="warning",
                )
            )
        except Exception as exc:
            log.error("Content Safety intelligence scan failed: %s — not blocked", exc)
            flags.append(
                ValidationFlag(
                    field="intelligence.content_safety",
                    message=f"Content Safety intelligence scan error: {exc}",
                    severity="warning",
                )
            )

    # --- Determine overall pass/fail ---
    blocked_flags = [f for f in flags if f.severity == "blocked"]
    # Content Safety failure is the only "total fail" condition;
    # quality flag removes bad recs but the remaining are still valid.
    passed_overall = content_safety_passed and not safe_fallback_used

    elapsed = (time.perf_counter() - t0) * 1000
    log.info(
        "Validation Agent complete — passed=%s  flags=%d  recs=%d  content_safety=%s  %.0fms",
        passed_overall,
        len(flags),
        len(final_recommendations),
        content_safety_passed,
        elapsed,
    )

    result = ValidationResult(
        passed=passed_overall,
        flags=flags,
        filtered_recommendations=final_recommendations,
        content_safety_passed=content_safety_passed,
        safe_fallback_used=safe_fallback_used,
        validation_ms=elapsed,
    )
    context.validation = result
    return result
