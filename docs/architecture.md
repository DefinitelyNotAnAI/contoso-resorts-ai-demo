# Architecture — Contoso Resorts AI Customer Intelligence Demo

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Browser (Single HTML)                     │
│  ┌──────────────────────┐  ┌──────────────────────────────────┐ │
│  │   Guest Profile &    │  │   Conversation / Call Panel      │ │
│  │   AI Insights Panel  │  │   (GPT Realtime WebSocket)       │ │
│  └──────────┬───────────┘  └──────────────┬───────────────────┘ │
│             │                             │                      │
└─────────────┼─────────────────────────────┼──────────────────────┘
              │ REST API                    │ WebSocket (Phase 3)
              ▼                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    FastAPI Backend (Python 3.11)                  │
│                                                                   │
│  ┌─────────────┐  ┌────────────┐  ┌───────────┐  ┌───────────┐ │
│  │  Retrieval   │→│  Sentiment  │→│  Reasoning │→│ Validation │ │
│  │   Agent      │  │   Agent    │  │   Agent   │  │   Agent   │ │
│  └──────┬──────┘  └────────────┘  └─────┬─────┘  └─────┬─────┘ │
│         │                               │               │        │
│         ▼                               ▼               ▼        │
│  ┌─────────────┐                 ┌─────────────┐ ┌───────────┐  │
│  │ SQL Query   │                 │ AI Foundry  │ │  Content   │  │
│  │ Tool (pyodbc)│                │   Client    │ │  Safety    │  │
│  └──────┬──────┘                 └──────┬──────┘ └───────────┘  │
│         │                               │                        │
└─────────┼───────────────────────────────┼────────────────────────┘
          │ TDS (SQL)                     │ HTTPS
          ▼                               ▼
┌──────────────────┐            ┌──────────────────┐
│  Fabric SQL       │            │  Azure OpenAI     │
│  Database         │            │  (gpt-4o-mini)    │
│                    │            │                    │
│  7 tables         │            │  GPT Realtime      │
│  ~25K rows        │            │  (gpt-4o-realtime) │
└──────────────────┘            └──────────────────┘
```

## Component Details

### Frontend — CRM UI
- **Technology:** Single HTML file, vanilla JavaScript, CSS
- **Layout:** Two-column — guest profile + AI insights (left), conversation/call (right)
- **Persona picker:** Modal on load with 3 persona cards
- **API calls:** REST to backend for queries and AI pipeline
- **Voice (Phase 3):** WebSocket to GPT Realtime API for voice call simulation
- **No build step.** No npm, no bundler, no framework.

### Backend — FastAPI
- **Runtime:** Python 3.11, uvicorn
- **Auth:** Azure AD / managed identity (DefaultAzureCredential)
- **Endpoints:**
  - `POST /api/query` — natural language → SQL → Fabric SQL → results
  - `POST /api/analyze` — triggers full 4-agent pipeline for a guest
  - `GET /api/health` — Fabric SQL connectivity check
- **Static serving:** `/static/` serves the HTML frontend

### AI Pipeline — 4 Agents
Sequential pipeline, each agent receives output of the previous:

| Agent | Input | Output | External Call |
|-------|-------|--------|---------------|
| Retrieval | GuestID | Guest profile, bookings, surveys, experiences | Fabric SQL (pyodbc) |
| Sentiment | Retrieval context | Sentiment summary, themes, preferences | LLM (light) |
| Reasoning | Retrieval + Sentiment | Personalized recommendations with reasoning | AI Foundry (gpt-4o-mini) |
| Validation | Recommendations | Validated, safe recommendations | Content Safety API |

### SQL Query Tool
- **Approach:** GPT-writes-SQL (ADR-001) — primary path; Data Agent (`USE_DATA_AGENT=true`) available behind feature flag (ADR-006)
- **Schema injection:** Table DDL included in LLM system prompt so GPT generates correct SQL
- **Execution:** pyodbc → Fabric SQL Database via TDS protocol
- **Auth:** Azure AD token (no SQL passwords)
- **Visibility:** Generated SQL returned to frontend for display

### Data Layer — Fabric SQL Database
- **Workspace:** Contoso Resorts AI (Fabric workspace, F64 capacity)
- **Database:** ContosoResortsDemo (~25K rows, 7 tables)
- **Capacity:** F64 (East US 2) — suspend between demos to control cost (ADR-018)
- **Connection:** TDS (standard SQL Server protocol) via pyodbc — identical to Azure SQL (ADR-017)
- **Auth:** Azure AD authentication (DefaultAzureCredential → access token)

### AI Services — Azure OpenAI
- **Primary resource:** azd-provisioned Azure OpenAI resource (East US)
- **Model:** gpt-4o-mini (30K TPM) — used by Sentiment, Reasoning agents and GPT-writes-SQL
- **Realtime resource:** azd-provisioned Azure OpenAI Realtime resource (East US 2)
- **GPT Realtime model:** gpt-4o-realtime-preview — used for voice simulation
- **Content Safety:** Azure Content Safety API — validates all AI outputs

## Authentication Flow

```
Browser → FastAPI → DefaultAzureCredential
                      ├── Managed Identity (deployed)
                      └── Azure CLI credential (local dev)
                            ├── Fabric SQL: AD token for TDS
                            ├── Azure OpenAI: AD token for inference
                            └── Content Safety: AD token
```

No API keys stored in code. No connection strings with passwords.

## Operational Resources

### Azure Automation Account — Nightly Fabric Suspend
- Deployed as part of `azd up` alongside the Fabric capacity (see ADR-021)
- **Runbook:** `suspend-fabric` (PowerShell) — calls the ARM Fabric suspend REST API
- **Schedule:** `nightly-midnight-et` — daily at 05:00 UTC (midnight ET)
- **Auth:** System-assigned managed identity with Contributor on the Fabric capacity resource
- **Purpose:** Safety net to prevent ~\$167/day cost overrun if the capacity is left running after a demo
- **Suspend-only:** No auto-resume. Presenter manually resumes before each demo via Azure Portal or CRM UI controls

## Data Flow — Demo Sequence

```
1. Presenter selects persona (e.g., Dana Lakehouse)
2. [Phase 3] Incoming call simulation — GPT Realtime speaks as Dana
3. Agent searches for guest → POST /api/query
4. Backend: GPT generates SQL → executes against Fabric SQL → returns profile
5. CRM displays guest profile with visible SQL
6. Agent clicks "Analyze" → POST /api/analyze
7. Backend runs 4-agent pipeline:
   a. Retrieval: queries Fabric SQL for full guest context (parameterized SQL, ADR-013)
   b. Sentiment: analyzes survey data
   c. Reasoning: generates recommendations via Azure OpenAI (gpt-4o-mini)
   d. Validation: checks quality + content safety
8. CRM displays AI insights progressively
9. Agent uses insights to serve the customer
```

## Feature-Flagged Capabilities

Already implemented, toggled via environment variable:

| Feature | Flag | Status | Notes |
|---------|------|--------|-------|
| Data Agent (Fabric) | `USE_DATA_AGENT=true` | Available | Requires published Fabric Data Agent in workspace |
| GPT-writes-SQL | `USE_DATA_AGENT=false` | Default | Active path — validated for all 3 personas |

Future (narrative only, not yet wired):
- **Mirroring** — "In production, Fabric Mirroring brings survey data from Medallia automatically" (ADR-003)
