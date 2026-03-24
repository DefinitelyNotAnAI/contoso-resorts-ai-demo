"""
api.py — Contoso Resorts AI Customer Intelligence Backend

Endpoints:
  GET  /api/health      — Database connectivity check
  POST /api/query       — Natural language -> SQL -> Azure SQL -> results

Auth: DefaultAzureCredential throughout (Azure CLI locally, Managed Identity deployed).
No API keys, no passwords in code.

Run:
  cd backend
  .venv\\Scripts\\python.exe -m uvicorn api:app --reload --port 8000
"""

import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs

from dotenv import load_dotenv
import asyncio

import websockets
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Load .env from parent directory (repo root)
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

from db import check_health, execute_query
from llm import generate_sql
from agents.data_agent import USE_DATA_AGENT, query_data_agent
from agents.retrieval import retrieve_guest_context
from agents.sentiment import analyze_sentiment
from agents.reasoning import generate_intelligence, generate_recommendations
from agents.validation import validate_recommendations
from models import GuestContext

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("api")


# ---------------------------------------------------------------------------
# JSON serialization helpers
# ---------------------------------------------------------------------------
def _serialize(value: Any) -> Any:
    """Convert types that are not JSON-serializable by default."""
    if hasattr(value, "model_dump"):          # Pydantic v2 model instance
        return value.model_dump(mode="json")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _serialize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: _serialize(v) for k, v in row.items()} for row in rows]


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class QueryRequest(BaseModel):
    question: str
    max_rows: int = 200   # safety cap — Basic 5 DTU is slow


class QueryResponse(BaseModel):
    question: str
    sql: str
    results: list[dict[str, Any]]
    row_count: int
    truncated: bool = False


class AnalyzeRequest(BaseModel):
    guest_id: str


# ---------------------------------------------------------------------------
# App lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting Contoso Resorts AI backend on port 8000")
    # Validate DB connectivity at startup (non-fatal if it fails — health
    # endpoint will report the error rather than crashing startup)
    health = check_health()
    if health["status"] == "healthy":
        log.info("Database connection OK → %s", health.get("database"))
    else:
        log.warning("Database connection FAILED at startup: %s", health.get("error"))
    yield
    log.info("Backend shutting down")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Contoso Resorts AI",
    description="Customer Intelligence demo backend — GPT-writes-SQL pipeline",
    version="3.1.0",
    lifespan=lifespan,
)

# CORS — allow all origins for local dev; tighten for production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Static files — serves the CRM frontend (Epic 1.3)
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir, html=True), name="static")


# ---------------------------------------------------------------------------
# Root redirect  GET /
# ---------------------------------------------------------------------------
@app.get("/")
async def root():
    """Redirect to the CRM frontend."""
    return RedirectResponse(url="/static/index.html")


# ---------------------------------------------------------------------------
# Health check  GET /api/health
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health():
    """
    Validate Azure SQL connectivity.
    Returns: {"status": "healthy"|"unhealthy", "database": "...", "server": "..."}
    """
    result = check_health()
    if result["status"] != "healthy":
        raise HTTPException(status_code=503, detail=result)
    return result


# ---------------------------------------------------------------------------
# Query endpoint  POST /api/query
# ---------------------------------------------------------------------------
@app.post("/api/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    """
    Convert a natural language question to SQL, execute it, and return results.

    When USE_DATA_AGENT=true, routes through Fabric Data Agent (ADR-006).
    Otherwise, uses GPT-writes-SQL pipeline (ADR-001 fallback).

    Request:  { "question": "Show me Dana Lakehouse's stay history" }
    Response: { "question": "...", "sql": "SELECT ...", "results": [...], "row_count": N }
    """
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")

    log.info("Query request: %r (data_agent=%s)", req.question[:120], USE_DATA_AGENT)

    # --- Data Agent path (ADR-006) ---
    if USE_DATA_AGENT:
        try:
            result = await query_data_agent(req.question, max_rows=req.max_rows)
            serialized = _serialize_rows(result["results"])
            return QueryResponse(
                question  = req.question,
                sql       = result.get("sql", "(Fabric Data Agent)"),
                results   = serialized,
                row_count = len(serialized),
                truncated = len(serialized) >= req.max_rows,
            )
        except Exception as exc:
            log.warning("Data Agent failed, falling back to GPT-writes-SQL: %s", exc)
            # Fall through to GPT-writes-SQL below

    # --- GPT-writes-SQL fallback path (ADR-001) ---
    # 1 — Generate SQL from natural language
    try:
        sql = await generate_sql(req.question)
    except Exception as exc:
        log.error("LLM error: %s", exc)
        raise HTTPException(status_code=502, detail=f"SQL generation failed: {exc}") from exc

    if not sql.strip():
        raise HTTPException(status_code=502, detail="LLM returned empty SQL")

    # 2 — Inject the row cap (TOP N) if not already present
    sql_upper = sql.strip().upper()
    if not sql_upper.startswith("SELECT TOP") and sql_upper.startswith("SELECT"):
        sql = f"SELECT TOP {req.max_rows} " + sql[len("SELECT"):].lstrip()

    # 3 — Execute against Azure SQL
    try:
        rows = await execute_query(sql)
    except Exception as exc:
        log.error("SQL execution error: %s\nSQL: %s", exc, sql)
        # Return the bad SQL in the response so the UI can show it
        raise HTTPException(
            status_code=422,
            detail={"error": str(exc), "sql": sql},
        ) from exc

    # 4 — Serialize and return
    serialized = _serialize_rows(rows)
    truncated  = len(serialized) >= req.max_rows

    log.info("Returned %d rows (truncated=%s)", len(serialized), truncated)

    return QueryResponse(
        question  = req.question,
        sql       = sql,
        results   = serialized,
        row_count = len(serialized),
        truncated = truncated,
    )


# ---------------------------------------------------------------------------
# Analyze endpoint  POST /api/analyze  (Epic 2.1 + 2.2 pipeline)
# ---------------------------------------------------------------------------
@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest):
    """
    Run the AI agent pipeline for a guest.

    Pipeline (sequential):
      1. Retrieval Agent  (Epic 2.1) — fetch guest context from Azure SQL
      2. Sentiment Agent  (Epic 2.2) — rule-based ratings + LLM free-text mining
      3. Reasoning Agent  (Epic 2.3) — property + experience recommendations
      4. Validation Agent (Epic 2.4) — quality check + Content Safety scan

    Request:  { "guest_id": "G-0001001" }
    Response: GuestContext with retrieval data + sentiment analysis.
    """
    log.info("Analyze request for GuestID=%s", req.guest_id)

    # --- Agent 1: Retrieval ---
    try:
        context = await retrieve_guest_context(req.guest_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.error("Retrieval Agent failed for %s: %s", req.guest_id, exc)
        raise HTTPException(
            status_code=502, detail=f"Retrieval Agent error: {exc}"
        ) from exc

    # Guest not found — 404
    if context.guest is None:
        raise HTTPException(
            status_code=404,
            detail=f"Guest {req.guest_id} not found in database",
        )

    # --- Agent 2: Sentiment ---
    try:
        await analyze_sentiment(context)
    except Exception as exc:
        log.error("Sentiment Agent failed for %s: %s", req.guest_id, exc)
        # Non-fatal: return partial context rather than failing entirely
        log.warning("Returning context without sentiment due to Sentiment Agent error")

    # --- Agent 3: Intelligence --- (Epic 3.3: pattern analysis replaces property recommendations)
    try:
        await generate_intelligence(context)
    except Exception as exc:
        log.error("Intelligence Agent failed for %s: %s", req.guest_id, exc)
        log.warning("Returning context without intelligence due to Intelligence Agent error")

    # --- Agent 4: Validation ---
    try:
        await validate_recommendations(context)
    except Exception as exc:
        log.error("Validation Agent failed for %s: %s", req.guest_id, exc)
        # Non-fatal: return partial context rather than failing entirely
        log.warning("Returning context without validation due to Validation Agent error")

    log.info(
        "Analyze complete for %s — %d bookings, %d surveys, "
        "retrieval=%.0fms sentiment=%.0fms intelligence=%.0fms validation=%.0fms",
        req.guest_id,
        len(context.bookings),
        len(context.surveys),
        context.retrieval_ms,
        context.sentiment.sentiment_ms if context.sentiment else 0,
        context.intelligence.intelligence_ms if context.intelligence else 0,
        context.validation.validation_ms if context.validation else 0,
    )

    return context


# ---------------------------------------------------------------------------
# On-demand Recommendations  POST /api/recommend  (Epic 3.3 Task 3.3.7)
# ---------------------------------------------------------------------------
@app.post("/api/recommend")
async def recommend(req: AnalyzeRequest):
    """
    Run the full pipeline PLUS the Reasoning Agent to produce property/experience
    recommendations on demand.

    This is a secondary endpoint. The primary intelligence pipeline runs
    automatically via /api/analyze (or /api/analyze-stream in Epic 3.3.2).
    Call this when the presenter clicks the "Recommendations" button.

    Pipeline: Retrieval → Sentiment → Intelligence → Reasoning → Validation
    Returns: full GuestContext with reasoning populated.
    """
    log.info("Recommend request for GuestID=%s", req.guest_id)

    # --- Retrieval ---
    try:
        context = await retrieve_guest_context(req.guest_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.error("Retrieval Agent failed for %s: %s", req.guest_id, exc)
        raise HTTPException(status_code=502, detail=f"Retrieval Agent error: {exc}") from exc

    if context.guest is None:
        raise HTTPException(status_code=404, detail=f"Guest {req.guest_id} not found in database")

    # --- Sentiment ---
    try:
        await analyze_sentiment(context)
    except Exception as exc:
        log.error("Sentiment Agent failed for %s: %s", req.guest_id, exc)

    # --- Intelligence (run in parallel with Reasoning for efficiency) ---
    async def _run_intelligence():
        try:
            await generate_intelligence(context)
        except Exception as exc:
            log.error("Intelligence Agent failed for %s: %s", req.guest_id, exc)

    async def _run_recommendations():
        try:
            await generate_recommendations(context)
        except Exception as exc:
            log.error("Reasoning Agent failed for %s: %s", req.guest_id, exc)

    await asyncio.gather(_run_intelligence(), _run_recommendations())

    # --- Validation (covers both intelligence and recommendations) ---
    try:
        await validate_recommendations(context)
    except Exception as exc:
        log.error("Validation Agent failed for %s: %s", req.guest_id, exc)

    log.info(
        "Recommend complete for %s — recs=%d  intelligence=%.0fms  reasoning=%.0fms",
        req.guest_id,
        len(context.reasoning.recommendations) if context.reasoning else 0,
        context.intelligence.intelligence_ms if context.intelligence else 0,
        context.reasoning.reasoning_ms if context.reasoning else 0,
    )
    return context


# ---------------------------------------------------------------------------
# Guest Intelligence stream  GET /api/analyze-stream  (Epic 3.3 Task 3.3.2)
# ---------------------------------------------------------------------------
def _sse(event: str, data: Any) -> str:
    """Format a single SSE frame."""
    payload = json.dumps(data, default=_serialize)
    return f"event: {event}\ndata: {payload}\n\n"


@app.get("/api/analyze-stream")
async def analyze_stream(guest_id: str):
    """
    Server-Sent Events stream of the Guest Intelligence pipeline.

    Yields one SSE event per agent as it completes:
      event: start         — pipeline kicked off, guest_id echo
      event: retrieval     — GuestContext.guest + bookings + surveys + experiences
      event: sentiment     — GuestContext.sentiment
      event: intelligence  — GuestContext.intelligence (briefing + insight cards)
      event: validation    — GuestContext.validation
      event: done          — final GuestContext serialised in full
      event: error         — { "agent": "<name>", "message": "<str>" }

    Frontend subscribes with EventSource('/api/analyze-stream?guest_id=XXXX').
    Each event payload is valid JSON; parse with JSON.parse(event.data).
    """

    async def _stream():
        # Signal pipeline start immediately so the UI can show a spinner
        yield _sse("start", {"guest_id": guest_id})

        # ── Agent 1: Retrieval ────────────────────────────────────────────
        try:
            context: GuestContext = await retrieve_guest_context(guest_id)
        except ValueError as exc:
            yield _sse("error", {"agent": "retrieval", "message": str(exc)})
            return
        except Exception as exc:
            log.error("SSE retrieval failed for %s: %s", guest_id, exc)
            yield _sse("error", {"agent": "retrieval", "message": f"Retrieval error: {exc}"})
            return

        if context.guest is None:
            yield _sse("error", {"agent": "retrieval", "message": f"Guest {guest_id} not found"})
            return

        yield _sse("retrieval", {
            "guest": _serialize(context.guest),
            "bookings": [_serialize(b) for b in context.bookings],
            "experiences": [_serialize(e) for e in context.experiences],
            "surveys": [_serialize(s) for s in context.surveys],
            "service_requests_count": len(context.service_requests),  # Epic 4.1
            "retrieval_ms": context.retrieval_ms,
        })

        # ── Agent 2: Sentiment ────────────────────────────────────────────
        try:
            await analyze_sentiment(context)
        except Exception as exc:
            log.error("SSE sentiment failed for %s: %s", guest_id, exc)
            yield _sse("error", {"agent": "sentiment", "message": str(exc)})
            # Non-fatal — continue pipeline

        yield _sse("sentiment", {
            "sentiment": _serialize(context.sentiment),
        })

        # ── Agent 3: Intelligence ─────────────────────────────────────────
        try:
            await generate_intelligence(context)
        except Exception as exc:
            log.error("SSE intelligence failed for %s: %s", guest_id, exc)
            yield _sse("error", {"agent": "intelligence", "message": str(exc)})
            # Non-fatal — continue to validation

        yield _sse("intelligence", {
            "intelligence": _serialize(context.intelligence),
        })

        # ── Agent 4: Validation ───────────────────────────────────────────
        try:
            await validate_recommendations(context)
        except Exception as exc:
            log.error("SSE validation failed for %s: %s", guest_id, exc)
            yield _sse("error", {"agent": "validation", "message": str(exc)})

        yield _sse("validation", {
            "validation": _serialize(context.validation),
        })

        # ── Done — emit full context so frontend can cache it ────────────
        log.info(
            "SSE stream complete for %s — "
            "retrieval=%.0fms sentiment=%.0fms intelligence=%.0fms validation=%.0fms",
            guest_id,
            context.retrieval_ms,
            context.sentiment.sentiment_ms if context.sentiment else 0,
            context.intelligence.intelligence_ms if context.intelligence else 0,
            context.validation.validation_ms if context.validation else 0,
        )
        yield _sse("done", _serialize(context))

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # Disable nginx buffering if deployed behind proxy
        },
    )


# ---------------------------------------------------------------------------
# Realtime WebSocket proxy  WS /ws/realtime
# ---------------------------------------------------------------------------
# ADR-016 (revised): Backend proxies WebSocket to Azure OpenAI Realtime.
# disableLocalAuth=true on the resource blocks API key auth from the browser;
# backend authenticates with DefaultAzureCredential (Bearer token) and relays
# all frames bidirectionally. Browser connects to ws://localhost:8000/ws/realtime.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Turn-steering micro-instructions (injected via session.update after each
# AI customer response). These prevent the model from self-answering by
# resetting its focus to "listen and react" before each new turn.
# ---------------------------------------------------------------------------
_TURN_STOP = (
    " After your reply, STOP COMPLETELY. Do not say anything else. "
    "Do not add follow-up thoughts. Do not continue the conversation "
    "by yourself. Wait in silence for the agent to speak next."
)

_TURN_STEER_OPENING = (
    "The agent just greeted you. State why you called in one short sentence. "
    "Nothing more. Do not elaborate, do not explain your background, do not "
    "ask the agent a follow-up question." + _TURN_STOP
)
_TURN_STEER_DEVELOPING = (
    "The agent just spoke. Respond ONLY to what they said. "
    "If they asked a question, answer it — briefly. "
    "If they proposed something, react to it — one sentence. "
    "Do not raise new topics. Do not answer questions you were not asked. "
    "Do not resolve your own concerns. Do not anticipate what the agent "
    "will say next." + _TURN_STOP
)
_TURN_STEER_LATER = (
    "The agent just spoke. React to what they said — one or two sentences. "
    "Do not raise anything new unless the agent directly asked about it. "
    "Do not resolve your own request. Do not wrap up the call unless the "
    "agent wraps up first." + _TURN_STOP
)


def _get_turn_steering(turn_count: int) -> str:
    """Return the appropriate micro-instruction for the current turn."""
    if turn_count <= 1:
        return _TURN_STEER_OPENING
    elif turn_count <= 3:
        return _TURN_STEER_DEVELOPING
    else:
        return _TURN_STEER_LATER


@app.websocket("/ws/realtime")
async def realtime_ws_proxy(ws_browser: WebSocket):
    # Extract persona from query string (e.g. /ws/realtime?persona=dana)
    qs = parse_qs(ws_browser.scope.get("query_string", b"").decode())
    persona_id = (qs.get("persona", [""])[0]) or "unknown"

    await ws_browser.accept(subprotocol="realtime")

    endpoint    = os.getenv("REALTIME_ENDPOINT", "").rstrip("/")
    model       = os.getenv("REALTIME_MODEL", "gpt-realtime")
    api_version = os.getenv("REALTIME_API_VERSION", "2024-10-01-preview")

    if not endpoint:
        await ws_browser.close(code=1011, reason="REALTIME_ENDPOINT not configured")
        return

    from azure.identity import DefaultAzureCredential
    try:
        token = await asyncio.to_thread(
            lambda: DefaultAzureCredential().get_token("https://cognitiveservices.azure.com/.default").token
        )
    except Exception as exc:
        log.error("Realtime: token acquisition failed: %s", exc)
        await ws_browser.close(code=1011, reason="Token acquisition failed")
        return

    azure_url = (
        endpoint.replace("https://", "wss://")
        + f"/openai/realtime?api-version={api_version}&deployment={model}"
    )
    log.info("Realtime proxy: connecting to Azure, persona=%s, deployment=%s", persona_id, model)

    try:
        async with websockets.connect(
            azure_url,
            additional_headers={
                "Authorization": f"Bearer {token}",
            },
            subprotocols=["realtime"],
            open_timeout=15,
        ) as ws_azure:
            log.info("Realtime proxy: Azure WS connected")

            # --- Per-connection turn tracking state ---
            turn_count = 0
            human_spoke = False        # True once VAD detects agent speech
            base_instructions = ""    # captured from browser's session.update

            async def browser_to_azure():
                """Forward browser frames to Azure, capturing the initial instructions."""
                nonlocal base_instructions
                try:
                    async for msg in ws_browser.iter_text():
                        # Capture the base persona instructions from the first session.update
                        try:
                            parsed = json.loads(msg)
                            if (parsed.get("type") == "session.update"
                                    and not base_instructions):
                                base_instructions = (
                                    parsed.get("session", {})
                                    .get("instructions", "")
                                )
                                log.info(
                                    "Realtime proxy: captured base instructions "
                                    "(%d chars) for persona=%s",
                                    len(base_instructions), persona_id,
                                )
                        except (json.JSONDecodeError, AttributeError):
                            pass
                        await ws_azure.send(msg)
                except Exception:
                    try:
                        await ws_azure.close()
                    except Exception:
                        pass

            async def azure_to_browser():
                """Forward Azure frames to browser; inject session.update
                after each AI turn AND cancel self-talk."""
                nonlocal turn_count, human_spoke
                try:
                    async for msg in ws_azure:
                        # Forward to browser first (low latency)
                        await ws_browser.send_text(msg)

                        # Parse to detect turn boundaries
                        try:
                            evt = json.loads(msg)
                        except (json.JSONDecodeError, AttributeError):
                            continue

                        evt_type = evt.get("type", "")

                        # --------------------------------------------------
                        # Self-talk guard: track whether human spoke between
                        # AI responses. input_audio_buffer.speech_started is
                        # the earliest VAD signal that a human is talking.
                        # --------------------------------------------------
                        if evt_type in (
                            "input_audio_buffer.speech_started",
                            "input_audio_buffer.committed",
                        ):
                            human_spoke = True

                        elif evt_type == "response.created":
                            # If model starts a new response and no human
                            # spoke since the last AI turn, it is self-
                            # talking — cancel immediately.
                            if turn_count > 0 and not human_spoke:
                                cancel_msg = json.dumps({
                                    "type": "response.cancel",
                                })
                                await ws_azure.send(cancel_msg)
                                log.warning(
                                    "Realtime proxy: CANCELLED self-talk "
                                    "(turn %d, persona=%s)",
                                    turn_count, persona_id,
                                )

                        elif evt_type == "response.done":
                            # AI customer just finished speaking — inject
                            # turn-steering and reset the human-spoke flag.
                            turn_count += 1
                            human_spoke = False

                            steering = _get_turn_steering(turn_count)
                            combined = (
                                f"{base_instructions}\n\n"
                                f"TURN {turn_count} GUIDANCE: {steering}"
                            ) if base_instructions else steering

                            steer_msg = json.dumps({
                                "type": "session.update",
                                "session": {
                                    "instructions": combined,
                                },
                            })
                            await ws_azure.send(steer_msg)
                            log.info(
                                "Realtime proxy: injected turn %d steering "
                                "for persona=%s",
                                turn_count, persona_id,
                            )

                except Exception:
                    try:
                        await ws_browser.close()
                    except Exception:
                        pass

            await asyncio.gather(browser_to_azure(), azure_to_browser())

    except Exception as exc:
        log.error("Realtime proxy: Azure connection failed: %s", exc)
        try:
            await ws_browser.close(code=1011, reason=str(exc)[:123])
        except Exception:
            pass
