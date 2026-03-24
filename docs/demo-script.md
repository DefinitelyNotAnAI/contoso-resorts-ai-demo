# Demo Script — Contoso Resorts AI (Epic 3.3)

> **Goal:** Prove the a-ha moment — Azure SQL + AI can cross-reference a guest's stay history, survey feedback, and experience bookings in real time, and surface patterns that a human agent could never find manually.

---

## Setup

1. Start the backend: `cd backend; .venv\Scripts\python.exe -m uvicorn api:app --reload --port 8000`
2. Open `http://localhost:8000` in a browser  
3. Have the Azure portal open on a second screen if relevant  
4. Know your three persona IDs:
   - Dana Lakehouse → `G-0001001` (Platinum, 12+ stays)
   - Anne Thropic → `G-0002001` (Gold, 3 stays, survey free-text)
   - Victor Storr → `G-0003001` (Silver, date-change scenario)

---

## Persona 1 — Dana Lakehouse (Platinum, Last-Minute Trip)

**Talking point:** *"Platinum guest, calls to book a last-minute getaway. No property preference — just 'somewhere relaxing, you know what I like.'"*

### Step 1 — Launch the demo
- Click **Dana Lakehouse** on the launcher screen
- The PMS loads instantly with Dana's profile

**Say:** *"As soon as the call is answered, the system pulls Dana's profile from Azure SQL — 12 stays, Platinum status, $47K lifetime spend. No form-filling."*

### Step 2 — Watch the Guest Intelligence panel auto-run
- Pipeline steps light up: **Retrieval → Sentiment → Intelligence → Validation**
- Timing badge shows each agent completing in milliseconds

**Say:** *"Four AI agents fire in sequence — automatically. No button click. By the time the agent says hello, the AI has already read her full history."*

### Step 3 — Read the briefing aloud
- Point to the **Agent Briefing** in the Guest Intelligence panel

**Say:** *"This isn't a list of recommendations — this is a brief. Two sentences that tell the agent everything they need to know before opening their mouth."*

### Step 4 — Show the insight cards
- Expand each insight card: Likes / Pain Points / Request Patterns / Cross-Property

**Say:** *"Spa consistently rated 5/5. Negative experience at Myrtle Beach — the AI knows not to suggest it. These aren't guesses — every card cites its evidence sources."*

### Step 5 — (Optional) Click View Recommendations
- Click the **View Recommendations** button
- Property cards appear below the insight cards

**Say:** *"If the agent wants to get specific about properties, one click runs a deeper recommendation pass. This is separate from the intelligence — we didn't want to bury the insights under a list."*

---

## Persona 2 — Anne Thropic (Gold, Special Request Before Arrival)

**Talking point:** *"Gold guest, upcoming reservation next month. Her daughter has a birthday during the stay — she wants something special. This is about survey free-text mining."*

### Step 1 — Launch Anne's profile
- Click **Anne Thropic** on the launcher or reload and select persona
- Watch auto-trigger on profile load

### Step 2 — Scroll to Sentiment Summary (expand it)
- Point to the per-category breakdown: Service / Cleanliness / Food & Bev / Spa / Activities

**Say:** *"This is the Medallia data. Most teams look at the overall score. The AI looks at all five categories — and you can see Food & Bev scores lower than everything else. That becomes an insight card."*

### Step 3 — Point to the Request Patterns insight card
- Find the `request_patterns` card in Guest Intelligence

**Say:** *"Three surveys mention the pillow menu. Once. The AI found a preference buried in free text that was never captured in the PMS. The agent can now proactively offer the pillow pre-set before Anne even asks."*

### Step 4 — Point to the Pain Points card
- Find the `pain_points` card

**Say:** *"Room service was slow — it's right here. The agent can get ahead of it: 'We've upgraded in-room dining since your last stay.' That's the difference between reactive and proactive service."*

---

## Persona 3 — Victor Storr (Silver, Date Change / Sold Out)

**Talking point:** *"Silver guest, needs to move his reservation — but his original dates are now sold out at his preferred property. This is about cross-property pattern analysis."*

### Step 1 — Launch Victor's profile
- Watch Guest Intelligence auto-trigger

### Step 2 — Point to the Cross-Property insight card
- Find the `cross_property` card

**Say:** *"Victor's stayed at three different properties. The AI compared his sentiment scores across all three and identified which one he rated highest when the amenity set matched his preferences. That's the alternative to suggest."*

### Step 3 — Show Alerts & Warnings
- Expand the **Alerts & Warnings** section
- Point to the Content Safety badge

**Say:** *"Every AI output goes through Azure AI Content Safety before it reaches the agent. If anything gets flagged, the recommendation is blocked and replaced with a safe fallback — automatically. No guardrail configuration required by the team."*

---

## Closing Statement

> *"What you've seen is four AI agents — retrieval, sentiment, intelligence, and validation — running in under 10 seconds against real Azure SQL data. No mock data. No hardcoded responses. The AI reads the guest history, mines the survey free-text, and produces a pattern briefing that makes every agent look like they've known the guest for years."*

---

## Common Questions

| Question | Answer |
|----------|--------|
| Is this real data? | Yes — generated but structurally authentic Azure SQL data, same schema as production |
| How long does the pipeline take? | 6–12 seconds end-to-end; the SSE stream means the UI updates as each agent completes |
| What if an agent fails? | Non-fatal failures show an inline warning; the pipeline continues; a Retry button appears for fatal failures |
| What model is this? | gpt-4o-mini on Azure OpenAI — 30K TPM, no data leaves the tenant |
| Can this be connected to real Medallia / HotSOS? | Yes — the SQL schema is designed to match those data shapes; it's an ETL problem, not an AI problem |
