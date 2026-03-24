# AI Customer Personas

Three selectable personas power the demo. Each represents a different customer service scenario and exercises different capabilities of the Azure SQL + AI stack.

At demo start, the presenter chooses a persona from a modal. This determines:
- Who the AI customer is (voice, personality, situation)
- What data the AI customer "knows" about themselves (grounded in Azure SQL)
- What the AI recommendation engine surfaces to help the agent

---

## Persona 1: "The Loyal VIP — Last-Minute Trip"

### Customer Profile
- **Name:** Dana Lakehouse
- **Loyalty Tier:** Platinum
- **History:** 12+ stays across 3 Contoso properties over 4 years
- **Survey pattern:** Consistently high NPS (9-10), loves the spa at Contoso Park City, rated Contoso Myrtle Beach 4/10 ("room was outdated")

### Scenario
Dana calls to book a last-minute getaway this weekend. She doesn't have a specific property in mind — "somewhere relaxing, you know what I like."

### What the agent sees (AI-powered)
- Guest profile populates instantly: Platinum, 12 stays, favorite property Park City
- Survey analysis: loves spa experiences, had a negative experience at Myrtle Beach
- AI recommends: Contoso Park City (availability confirmed), suggests pre-booking the signature spa package she rated 5/5 last visit
- AI warns: do NOT suggest Myrtle Beach — survey sentiment is negative

### What this proves
**Personalization at speed.** The AI assembles a complete picture of a high-value customer in seconds — including sentiment signals from survey data that would never surface in a standard PMS lookup.

### Visible SQL moment
```sql
SELECT b.PropertyID, p.Name, COUNT(*) as Stays, AVG(s.OverallRating) as AvgRating
FROM Bookings b
JOIN Properties p ON b.PropertyID = p.PropertyID
JOIN Surveys s ON b.BookingID = s.BookingID
WHERE b.GuestID = 'G-0001001'
GROUP BY b.PropertyID, p.Name
ORDER BY AvgRating DESC
```
Audience sees: "The AI wrote this query to find her favorite property by cross-referencing bookings with survey scores."

---

## Persona 2: "The Planner — Special Request Before Arrival"

### Customer Profile
- **Name:** Anne Thropic
- **Loyalty Tier:** Gold
- **History:** 3 stays, all at Contoso Orlando; always books family suite
- **Survey pattern:** 7/10 overall, free text mentions "loved the pillow menu" and "kids club was fantastic" but "room service was slow"

### Scenario
Anne calls about her upcoming reservation next month at Contoso Orlando. Her daughter has a birthday during the stay, and she wants to arrange something special. She also asks if there's anything new at the property.

### What the agent sees (AI-powered)
- Upcoming reservation details auto-loaded
- Past special requests: crib (previous stay), extra towels, late checkout
- Survey insight: "kids club was fantastic" → AI recommends the new Kids' Birthday Experience package
- Survey insight: "room service was slow" → AI flags this so agent can proactively address: "We've upgraded our in-room dining — your meals will arrive within 20 minutes"
- AI suggests: pillow menu preference from survey free text → "Would you like us to pre-set your preferred pillow selection?"

### What this proves
**Cross-system intelligence.** Survey free-text mining surfaces preferences that aren't captured anywhere in the PMS. The AI turns unstructured feedback into proactive service actions.

### Visible SQL moment
```sql
SELECT s.FreeText, s.OverallRating, s.Service, s.FoodBeverage
FROM Surveys s
JOIN Bookings b ON s.BookingID = b.BookingID
WHERE s.GuestID = 'G-0002001' AND s.FreeText IS NOT NULL
ORDER BY s.SubmittedDate DESC
```
Audience sees: "The AI queried survey free-text comments to mine for preferences — this is the Medallia data most companies never connect to their PMS."

---

## Persona 3: "The Reluctant Mover — Forced Property Change"

### Customer Profile
- **Name:** Victor Storr
- **Loyalty Tier:** Silver
- **History:** 2 stays: Contoso New York Grand Central (rated 9/10, "loved the rooftop bar — intimate, actual conversation possible") and Contoso Orlando (rated 6/10, "too crowded, pool area was chaotic")
- **Survey pattern:** Values intimate atmosphere and quiet; dislikes large, noisy resort environments

### Scenario
Victor has a confirmed booking at Contoso New York he is looking forward to. His firm has since mandated he attend a client conference in Orlando on the exact same dates. He calls to move his New York booking to Contoso Orlando — a property he did not enjoy last time. He will accept Orlando only if the agent can demonstrate there is something comparable to what he loved in New York: a quiet, intimate bar where you can have a real conversation. If the agent just offers a pool-view upgrade, he walks.

### What the agent sees (AI-powered)
- Confirmed New York reservation + request to move to Orlando (same dates)
- Survey history: New York 9/10 (rooftop bar callout), Orlando 6/10 (crowding complaint)
- AI recommendation (the differentiator):
  - Standard PMS: "Booking moved. Here is your Orlando confirmation."
  - With AI: "Victor rated Orlando 6/10 citing crowds. His New York survey specifically praised the rooftop bar's intimate atmosphere. **Before confirming the move, surface the Eighteen Sky Bar at Contoso Orlando** — low-key lounge on the 18th floor, no DJ, reservation-only. Match the language from his survey: quiet, intimate, conversation-friendly. Do NOT lead with pool views or family packages."
- AI also flags: Victor's tone will soften if the rooftop bar equivalent is named specifically — watch for the shift as a buying signal

### What this proves
**AI-powered retention.** A reluctant move becomes a recovered experience. The AI reads sentiment signals from past surveys to predict what will turn a resigned customer into a satisfied one — something no standard PMS lookup would surface.

### Visible SQL moment
```sql
SELECT p.Name, p.Tier, i.RoomType, i.Available,
       COALESCE(AVG(s.OverallRating), 0) as GuestRatingAtProperty
FROM Inventory i
JOIN Properties p ON i.PropertyID = p.PropertyID
LEFT JOIN Bookings b ON b.PropertyID = p.PropertyID AND b.GuestID = 'G-0003001'
LEFT JOIN Surveys s ON s.BookingID = b.BookingID
WHERE i.Date BETWEEN '2026-03-15' AND '2026-03-18' AND i.Available > 0
GROUP BY p.Name, p.Tier, i.RoomType, i.Available
ORDER BY GuestRatingAtProperty DESC
```
Audience sees: "The AI didn't just check availability — it joined inventory with this guest's survey history to rank properties by personal preference."

---

## Persona Selection UX

On page load, a modal presents three cards:

| Card | Title | Subtitle | Visual cue |
|------|-------|----------|------------|
| 1 | Dana Lakehouse | Platinum · Last-minute getaway | Gold/platinum accent |
| 2 | Anne Thropic | Gold · Birthday special request | Family icon |
| 3 | Victor Storr | Silver · Date change — sold out | Calendar icon |

Clicking a card:
1. Dismisses the modal
2. Triggers the incoming call simulation with that persona's voice and scenario
3. Pre-loads the guest context into the GPT Realtime system prompt
4. Populates the CRM with that guest's profile once the agent searches
