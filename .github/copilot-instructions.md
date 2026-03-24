# Copilot Instructions for Contoso Resorts AI Demo

## Documentation

- `docs/architecture.md` — System design and component details
- `docs/data-model.md` — Database schema and seed data
- `docs/personas.md` — The 3 demo personas and their scenarios
- `docs/demo-script.md` — Step-by-step demo walkthrough
- `docs/DEPLOYMENT.md` — Deployment guide (`azd up`)

---

## Design Principles

**Don't over-engineer. This is a demo, not a production system.**

- ❌ Cosmos DB, Redis, Azure AI Search — not needed
- ❌ Mock data — use real Fabric SQL data only
- ❌ JavaScript frameworks — vanilla JS only
- ✅ Keep the surface area small and the demo reliable

---

## Architecture

### 4 Agents (Sequential Pipeline)
```
Guest Search → Retrieval → Sentiment → Reasoning → Validation → Recommendations
                 ↓            ↓           ↓            ↓
              (Fabric)    (LLM light)  (AI Foundry)  (Content Safety)
```

### 6 Data Tables (Fabric SQL Database)
| Table | Purpose |
|-------|---------|
| Properties | 5 Contoso Resorts locations |
| Guests | ~5,000 guest profiles with loyalty |
| Bookings | ~12,000 cross-property stay history |
| Experiences | ~200 per-property offerings (spa, dining, activities) |
| Inventory | 90-day availability grid |
| Surveys | ~4,000 post-stay feedback (the "Medallia" data) |

### 3 Demo Personas
| Persona | Scenario |
|---------|----------|
| Dana Lakehouse (Platinum) | Last-minute trip — AI recommends based on history |
| Anne Thropic (Gold) | Special request — AI mines survey free-text for preferences |
| Victor Storr (Silver) | Date change, sold out — AI finds right alternative property |

---

## Azure Resources (provisioned by `azd up`)

| Resource | Purpose | Env Var |
|----------|---------|---------|
| Resource Group | `rg-{AZURE_ENV_NAME}` | `AZURE_RESOURCE_GROUP` |
| Azure OpenAI (primary) | gpt-4o-mini for SQL gen + reasoning | `AI_FOUNDRY_ENDPOINT` |
| Azure OpenAI (realtime) | gpt-4o-realtime-preview for voice | `REALTIME_ENDPOINT` |
| Content Safety | Validates AI outputs | `CONTENT_SAFETY_ENDPOINT` |
| Fabric Capacity (F64) | Hosts Fabric SQL Database | `FABRIC_CAPACITY_NAME` |
| App Service (P0v3) | Hosts FastAPI backend | `APP_SERVICE_NAME` |
| Automation Account | Nightly Fabric suspend (cost control) | `AUTOMATION_ACCOUNT_NAME` |

---

## Commands

```powershell
# Deploy everything
azd up

# Run backend locally (after azd up)
cd backend; .venv\Scripts\python.exe -m uvicorn api:app --reload --port 8000
```

---

## Coding Standards

### Python
- Type hints on all functions
- `async/await` for I/O
- No mock code — real Fabric SQL data only
- Structured logging (not print)
- `DefaultAzureCredential` for all Azure auth (no API keys)

### Frontend
- Single HTML file, vanilla JavaScript
- No npm, no bundler, no framework
- CSS embedded or in separate file (no build step)

### Error Handling
- Try-catch for async operations
- Log errors with context
- Never swallow errors

---

*This file is read by GitHub Copilot at session start.*
