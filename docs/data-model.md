# Data Model — Contoso Resorts

All data lives in a Microsoft Fabric SQL Database provisioned by `azd up`. The dataset is AI-generated and designed to support the three demo personas specifically, while being large enough to feel realistic for ad-hoc queries.

The connection code is identical to Azure SQL Database — only the server hostname differs between the two endpoints.

## Tables

### Properties
The Contoso Resorts chain — 5 locations with distinct personalities.

| Column | Type | Description |
|--------|------|-------------|
| PropertyID | nvarchar | PK (e.g., 'P-001') |
| Name | nvarchar | e.g., 'Contoso Park City Resort & Spa' |
| ShortName | nvarchar | e.g., 'Park City' |
| Location | nvarchar | City, State |
| Description | nvarchar | Property personality / selling points |
| Tier | nvarchar | Luxury, Premium, Select |
| RoomCount | int | Total rooms |

**Seed data — 5 properties:**

| Property | Location | Tier | Rooms | Personality |
|----------|----------|------|-------|-------------|
| Contoso Park City Resort & Spa | Park City, UT | Luxury | 120 | Ski-in/ski-out, signature spa, après-ski dining |
| Contoso Myrtle Beach Oceanfront | Myrtle Beach, SC | Premium | 280 | Beachfront, recently renovated, water sports |
| Contoso Orlando Family Resort | Orlando, FL | Premium | 350 | Family-focused, kids club, near theme parks |
| Contoso New York Grand Central | New York, NY | Select | 85 | Urban boutique, rooftop bar, theater district |
| Contoso Gatlinburg Mountain Lodge | Gatlinburg, TN | Select | 60 | Mountain retreat, hiking, Southern charm |

### Guests
Chain-wide guest master — loyalty spans all properties.

| Column | Type | Description |
|--------|------|-------------|
| GuestID | nvarchar | PK (e.g., 'G-0001234') |
| FirstName | nvarchar | |
| LastName | nvarchar | |
| Email | nvarchar | |
| Phone | nvarchar | |
| HomeCity | nvarchar | |
| Country | nvarchar | |
| LoyaltyTier | nvarchar | Platinum, Gold, Silver, Member |
| LoyaltyPoints | int | |
| MemberSince | date | |
| Preferences | nvarchar | JSON — room temp, pillow type, dietary, etc. |

**Volume: ~5,000 guests**

### Bookings
Cross-property stay history — the connective tissue.

| Column | Type | Description |
|--------|------|-------------|
| BookingID | nvarchar | PK |
| GuestID | nvarchar | FK → Guests |
| PropertyID | nvarchar | FK → Properties |
| CheckIn | date | |
| CheckOut | date | |
| RoomType | nvarchar | Standard, Deluxe, Suite, Family Suite, Penthouse |
| RoomNumber | nvarchar | |
| RatePerNight | decimal | |
| TotalAmount | decimal | |
| Status | nvarchar | Completed, Upcoming, Cancelled |
| SpecialRequests | nvarchar | Free text — crib, late checkout, etc. |
| BookedDate | date | When the reservation was made |

**Volume: ~12,000 bookings**

### Experiences
Per-property offerings — spa, dining, activities.

| Column | Type | Description |
|--------|------|-------------|
| ExperienceID | nvarchar | PK |
| PropertyID | nvarchar | FK → Properties |
| Name | nvarchar | e.g., 'Alpine Sunset Spa Package' |
| Category | nvarchar | Spa, Dining, Activity, Kids, Wellness |
| Description | nvarchar | |
| Price | decimal | |
| Duration | nvarchar | e.g., '90 min' |
| Available | bit | Currently bookable |

**Volume: ~200 experiences** (~40 per property)

### Inventory
Simple availability grid for the date-change scenario.

| Column | Type | Description |
|--------|------|-------------|
| PropertyID | nvarchar | FK → Properties |
| Date | date | |
| RoomType | nvarchar | |
| TotalRooms | int | |
| BookedRooms | int | |
| Available | int | Computed: TotalRooms - BookedRooms |

**Volume: ~2,250 rows** (90 days × 5 properties × 5 room types)

**Important:** Orlando must be sold out on Victor Storr's requested dates to support Persona 3.

### Surveys
Post-stay feedback — the "Medallia" data that proves cross-system value.

| Column | Type | Description |
|--------|------|-------------|
| SurveyID | nvarchar | PK |
| GuestID | nvarchar | FK → Guests |
| BookingID | nvarchar | FK → Bookings |
| PropertyID | nvarchar | FK → Properties |
| OverallRating | int | 1-10 |
| NPS | int | 0-10 |
| Cleanliness | int | 1-5 |
| Service | int | 1-5 |
| FoodBeverage | int | 1-5 |
| Spa | int | 1-5 (null if not used) |
| Activities | int | 1-5 (null if not used) |
| FreeText | nvarchar | Open-ended feedback — the gold mine |
| SubmittedDate | date | |

**Volume: ~4,000 surveys** (~33% response rate)

### ServiceRequests
In-stay operations requests — mirrors an Amadeus HotSOS export (Epic 5.1). In production, HotSOS is a separate system that reservations agents cannot access; this table simulates the integration. Covers housekeeping, engineering, F&B, front desk, and guest services requests made after check-in.

| Column | Type | Description |
|--------|------|-------------|
| RequestID | nvarchar | PK (e.g., 'SR-0001001') |
| GuestID | nvarchar | FK → Guests |
| BookingID | nvarchar | FK → Bookings (stay context) |
| PropertyID | nvarchar | FK → Properties |
| RequestedDate | datetime | When the guest made the request |
| Department | nvarchar | Housekeeping / Engineering / F&B / Front Desk / Guest Services |
| Category | nvarchar | e.g., Extra Linens, AC Issue, Noise Complaint, Birthday Setup, Extra Pillows |
| Description | nvarchar(500) | Free text — what the guest actually asked for |
| Priority | nvarchar | Low / Medium / High / Urgent |
| Status | nvarchar | Open / InProgress / Completed / Escalated / Cancelled |
| AssignedTo | nvarchar | Staff name or department (nullable) |
| CompletedDate | datetime | Nullable — null if never resolved |
| ResponseMinutes | int | Time from request to first action (nullable) — key AI signal |
| ResolutionNotes | nvarchar(500) | What was done (nullable, often blank in real HotSOS exports) |
| GuestSatisfied | bit | Did the guest confirm resolution? (nullable) |

**Volume: ~500 requests** across all 5 properties (~1 request per 8 stays, weighted toward persona records)

**AI pipeline usage:**
- **Sentiment Agent** — groups requests by `Department`; flags repeated categories across stays (≥2 same category = pattern); flags slow response times (`ResponseMinutes > 60` on High/Urgent priority = negative signal)
- **Reasoning Agent** — consumes `service_patterns` field to generate `proactive_flags` on recommendations (e.g. *"Anne had two slow F&B responses at Orlando — acknowledge before she raises it"*)

**Key demo moment (Anne Thropic):** Two slow room-service calls during her last Orlando stay (45 min and 70 min response on High priority) surface as a proactive flag in the agent CRM panel — the AI predicts the complaint before she says a word.

**Source system note:** In production this data would be sourced from HotSOS via scheduled API export or Fabric Mirroring into OneLake, then reflected into Azure SQL for the AI pipeline. For the demo it is loaded directly into Azure SQL alongside the other tables.

## Persona Seed Data

The three demo personas (Dana Lakehouse, Anne Thropic, Victor Storr) require hand-authored records. Their bookings, surveys, and preferences must align precisely with the demo script and produce the expected AI recommendations.

All other records are AI-generated to provide realistic volume and variety.

## Mirroring Narrative

In the demo narrative, the survey data is framed as: *"This data comes from Medallia. In production, Fabric Mirroring brings it into OneLake automatically — no ETL, no pipelines."* The actual demo loads survey data directly into Azure SQL Database. The architecture diagram shows the Mirroring connection as the production path.

## Data Generation & Deployment

- **Generation:** Python script produces CSVs with realistic distributions
- **Persona records:** Hand-authored in separate seed files, merged with generated data
- **Load method:** Bulk insert via pyodbc into Azure SQL Database
- **Reset:** Re-run the generation script to restore demo data to known state
- **Database:** Azure SQL Basic tier (~$5/month) — Fabric migration planned (ADR-011)
