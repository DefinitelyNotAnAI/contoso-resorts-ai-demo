# Contoso Resorts AI — Customer Intelligence Demo

A deployable demo showing how **Microsoft Fabric SQL + Azure AI Foundry** transform hotel customer service — connecting siloed data (bookings, loyalty, surveys) and surfacing personalized AI recommendations to frontline staff in real time.

Uses a fictional hotel chain (Contoso Resorts) as the vehicle. The message is universal: any company with customer data and a service team can use this pattern.

> *"Your data already knows your customers. Fabric SQL + Azure AI connects the dots — so your team can deliver personalized service in real time, using natural language."*

## What It Does

A customer service agent selects a guest from the CRM. The AI pipeline:

1. **Retrieves** guest profile, booking history, survey feedback, and available experiences from Fabric SQL
2. **Analyzes sentiment** across past stays to extract preferences and pain points
3. **Reasons** over the full context to generate personalized upsell and service recommendations
4. **Validates** all outputs through Azure Content Safety before surfacing them

The agent can also have a live voice conversation with an AI that already knows the guest — powered by GPT-4o Realtime.

## Architecture

```
┌─────────────────────────────────────────────┐
│            Browser (CRM UI)                  │
│   Single HTML file, vanilla JavaScript       │
└────────────────┬────────────────────────────┘
                 │ REST / WebSocket
┌────────────────▼────────────────────────────┐
│         FastAPI Backend (Python 3.11)        │
│                                              │
│  Retrieval → Sentiment → Reasoning → Validation
│     ↓                        ↓          ↓
│  Fabric SQL           AI Foundry   Content Safety
└──────────────────────────────────────────────┘
```

**Full architecture details:** [docs/architecture.md](docs/architecture.md)

## Quick Start

**Prerequisites:** Azure subscription, [Azure Developer CLI](https://aka.ms/azd), Python 3.11, [ODBC Driver 18 for SQL Server](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server)

```powershell
# 1. Clone
git clone https://github.com/microsoft/contoso-resorts-ai-demo
cd contoso-resorts-ai-demo

# 2. Log in
azd auth login
az login

# 3. Deploy everything (Fabric, OpenAI, App Service, seed data — ~15 min)
azd up

# 4. Open the deployed URL shown at the end of azd up
```

**Step-by-step guide:** [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)

## Documentation

| Doc | Description |
|-----|-------------|
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Full deployment guide — prerequisites, `azd up`, verify, cost control, teardown |
| [docs/architecture.md](docs/architecture.md) | System design, 4-agent pipeline, component details |
| [docs/personas.md](docs/personas.md) | Three demo personas and their scenarios |
| [docs/demo-script.md](docs/demo-script.md) | Step-by-step demo walkthrough script |
| [docs/data-model.md](docs/data-model.md) | 6-table Fabric SQL schema and seed data |

## Demo Personas

| Persona | Loyalty | Scenario |
|---------|---------|----------|
| Dana Lakehouse | Platinum | Last-minute trip — AI recommends based on history |
| Anne Thropic | Gold | Special request — AI mines survey free-text for preferences |
| Victor Storr | Silver | Date change, sold out — AI finds the right alternative property |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Single HTML file, vanilla JavaScript, no framework |
| Backend | Python 3.11, FastAPI, uvicorn |
| Data | Microsoft Fabric SQL Database (~25K rows, 6 tables) |
| AI (text) | Azure OpenAI — gpt-4o-mini (SQL generation + reasoning) |
| AI (voice) | Azure OpenAI — gpt-4o-realtime-preview (WebSocket voice) |
| Safety | Azure Content Safety — validates all AI outputs |
| Auth | DefaultAzureCredential — no API keys, no passwords |
| Infra | Azure App Service P0v3, Fabric Capacity F64 |
| Cost control | Azure Automation — auto-suspends Fabric nightly |

## Cost Estimate

| Resource | Cost | Notes |
|----------|------|-------|
| Fabric F64 Capacity | ~$7/hr (~$167/day) | **Auto-suspended when not in use** |
| Azure OpenAI (gpt-4o-mini) | Pay-per-use | Low volume for demos |
| App Service P0v3 | ~$0.076/hr | ~$55/month |
| Content Safety | Pay-per-use | Low volume for demos |

> **Important:** Fabric capacity is automatically suspended after each `azd up` and nightly via Automation Account. You pay only when actively running the demo. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for resume/suspend instructions.

## License

MIT

