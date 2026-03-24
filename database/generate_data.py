#!/usr/bin/env python3
"""
generate_data.py — Contoso Resorts AI demo data generation script

Generates bulk CSVs for all 6 tables. Does NOT generate the 3 demo persona records
(those are hand-authored in tasks 1.1.3–1.1.5 and merged at load time).

Output: database/seed/*.csv
Usage:  python database/generate_data.py [--seed 42]

No external dependencies — stdlib only.
"""

import argparse
import csv
import json
import os
import random
from datetime import date, datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

# GuestIDs reserved for hand-authored persona records (never generated)
PERSONA_GUEST_IDS = {"G-0001001", "G-0002001", "G-0003001"}

# Persona booking IDs to skip (reserved ranges)
PERSONA_BOOKING_PREFIX = {"BK-0001", "BK-0002", "BK-0003"}

# Victor Storr (G-0003001) requested date change to these Orlando dates.
# Orlando (P-003) must be sold out (Available=0) for all room types on this window.
VICTOR_BLOCKED_START = date(2026, 3, 14)
VICTOR_BLOCKED_END   = date(2026, 3, 21)

# Target volumes
TARGET_GUESTS    = 5_000
TARGET_BOOKINGS  = 12_000
TARGET_SURVEYS   = 4_000    # ~33% response rate on completed bookings
TARGET_SERVICE_REQUESTS = 500  # ~1 per 8 stays, excluding persona guests
EXPERIENCES_PER_PROPERTY = 40
INVENTORY_DAYS   = 90

PROPERTIES = [
    {"PropertyID": "P-001", "ShortName": "Park City",    "RoomCount": 120},
    {"PropertyID": "P-002", "ShortName": "Myrtle Beach", "RoomCount": 280},
    {"PropertyID": "P-003", "ShortName": "Orlando",      "RoomCount": 350},
    {"PropertyID": "P-004", "ShortName": "New York",     "RoomCount":  85},
    {"PropertyID": "P-005", "ShortName": "Gatlinburg",   "RoomCount":  60},
]

ROOM_TYPES = ["Standard", "Deluxe", "Suite", "Family Suite", "Penthouse"]

# Rooms per type per property  (must sum to RoomCount)
ROOM_TYPE_COUNTS = {
    "P-001": {"Standard": 49, "Deluxe": 40, "Suite": 17, "Family Suite": 10, "Penthouse": 4},
    "P-002": {"Standard": 130, "Deluxe": 90, "Suite": 38, "Family Suite": 18, "Penthouse": 4},
    "P-003": {"Standard": 160, "Deluxe": 110, "Suite": 50, "Family Suite": 26, "Penthouse": 4},
    "P-004": {"Standard": 34, "Deluxe": 26, "Suite": 14, "Family Suite": 8, "Penthouse": 3},
    "P-005": {"Standard": 24, "Deluxe": 20, "Suite": 10, "Family Suite": 4, "Penthouse": 2},
}

# Booking weight per property (higher = more bookings allocated there)
PROPERTY_BOOKING_WEIGHT = {
    "P-001": 0.15,
    "P-002": 0.28,
    "P-003": 0.32,
    "P-004": 0.12,
    "P-005": 0.13,
}

# Nightly rate ranges per room type
RATE_RANGES = {
    "Standard":     (149, 249),
    "Deluxe":       (219, 349),
    "Suite":        (389, 649),
    "Family Suite": (449, 749),
    "Penthouse":    (799, 1499),
}

# Survey rating distributions: (overall_min, overall_max, nps_min, nps_max)
# Myrtle Beach intentionally skews negative to support Dana's "do not recommend" signal
SURVEY_CONFIG = {
    "P-001": {"overall": (8, 10), "nps": (8, 10), "service": (4, 5), "food": (4, 5), "spa_prob": 0.6},
    "P-002": {"overall": (4,  7), "nps": (3,  6), "service": (3, 4), "food": (3, 5), "spa_prob": 0.3},
    "P-003": {"overall": (6,  9), "nps": (6,  9), "service": (4, 5), "food": (3, 5), "spa_prob": 0.2},
    "P-004": {"overall": (7, 10), "nps": (7, 10), "service": (4, 5), "food": (4, 5), "spa_prob": 0.1},
    "P-005": {"overall": (7, 10), "nps": (6, 10), "service": (4, 5), "food": (4, 5), "spa_prob": 0.2},
}

LOYALTY_TIERS = ["Platinum", "Gold", "Silver", "Member"]
LOYALTY_WEIGHTS = [0.05, 0.20, 0.35, 0.40]

FIRST_NAMES = [
    "James", "Emma", "Liam", "Olivia", "Noah", "Ava", "William", "Sophia", "Benjamin",
    "Isabella", "Lucas", "Mia", "Henry", "Charlotte", "Alexander", "Amelia", "Mason",
    "Harper", "Ethan", "Evelyn", "Daniel", "Abigail", "Michael", "Emily", "Matthew",
    "Elizabeth", "Aiden", "Mila", "Jackson", "Ella", "Sebastian", "Avery", "Jack",
    "Sofia", "Owen", "Camila", "Samuel", "Aria", "David", "Scarlett", "Joseph",
    "Victoria", "Carter", "Madison", "Wyatt", "Luna", "John", "Grace", "Oliver",
    "Chloe", "Dylan", "Penelope", "Luke", "Layla", "Gabriel", "Riley", "Anthony",
    "Zoey", "Isaac", "Nora", "Grayson", "Lily", "Julian", "Eleanor", "Levi",
    "Hannah", "Caleb", "Aubrey", "Ryan", "Zoe", "Nathan", "Stella", "Adam",
    "Hazel", "Tyler", "Violet", "Austin", "Aurora", "Andrew", "Savannah", "Connor",
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
    "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson",
    "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson", "Walker",
    "Young", "Allen", "King", "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores",
    "Green", "Adams", "Nelson", "Baker", "Hall", "Rivera", "Campbell", "Mitchell",
    "Carter", "Roberts", "Phillips", "Evans", "Turner", "Parker", "Collins", "Stewart",
    "Morris", "Rogers", "Reed", "Cook", "Morgan", "Bell", "Murphy", "Bailey", "Cooper",
    "Richardson", "Cox", "Howard", "Ward", "Peterson", "Gray", "James", "Watson",
]

HOME_CITIES = [
    "New York, NY", "Los Angeles, CA", "Chicago, IL", "Houston, TX", "Phoenix, AZ",
    "Philadelphia, PA", "San Antonio, TX", "San Diego, CA", "Dallas, TX", "San Jose, CA",
    "Austin, TX", "Jacksonville, FL", "Fort Worth, TX", "Columbus, OH", "Charlotte, NC",
    "Indianapolis, IN", "San Francisco, CA", "Seattle, WA", "Denver, CO", "Nashville, TN",
    "Oklahoma City, OK", "Las Vegas, NV", "Louisville, KY", "Memphis, TN", "Portland, OR",
    "Baltimore, MD", "Milwaukee, WI", "Albuquerque, NM", "Tucson, AZ", "Fresno, CA",
    "Sacramento, CA", "Kansas City, MO", "Atlanta, GA", "Colorado Springs, CO", "Raleigh, NC",
    "Miami, FL", "Minneapolis, MN", "Omaha, NE", "Cleveland, OH", "Tampa, FL",
]

# Survey free-text pools — curated per property for realistic output
FREE_TEXT_POOL = {
    "P-001": [
        "The spa was absolutely incredible — best massage I've ever had.",
        "Ski-in/ski-out access made the trip completely effortless. Will definitely return.",
        "Mountain views from every room. Truly a luxury experience.",
        "The après-ski lounge has the best fondue I've tasted.",
        "Spa and restaurant both exceeded expectations. Staff were impeccable.",
        "Loved the fireplace suite. Perfect romantic getaway.",
        "The facial at the spa was worth every penny.",
        "Concierge arranged a private ski lesson — absolutely seamless.",
        "Best New Year's Eve I've ever spent. The event was magical.",
        "Signature spa package was the highlight of our trip.",
        "Staff remembered our preferences from last year. That's real luxury.",
        "The mountain air and the spa together are unbeatable.",
        None,
    ],
    "P-002": [
        "Room felt a bit dated despite the renovation — disappointed.",
        "Beach access was great but the room didn't match the price.",
        "Staff were friendly but the property really needs updating.",
        "Loved the ocean views but the bathroom fixtures are old.",
        "Seafood restaurant was excellent — the room itself was not.",
        "I've stayed at better resorts for this price point.",
        "The pool area was very crowded on weekends.",
        "The oceanview terrace is lovely. The room quality was not.",
        "Check-in was slow and the room wasn't ready until 5pm.",
        "Water sports center was a highlight. Room quality was a letdown.",
        "Expected more for a premium resort. Rooms need a serious refresh.",
        None,
        None,  # more None = fewer with free text, aligned with lower engagement
    ],
    "P-003": [
        "Kids club is incredible — my kids absolutely didn't want to leave.",
        "Character dining is a must-do with little ones. Perfect experience.",
        "Lazy river pool is ideal for families of all ages.",
        "Great location for the parks. Hotel itself is solid.",
        "Kids' Birthday Experience was an absolute hit for my daughter.",
        "The pillow menu is a delightful and thoughtful touch.",
        "Family suite was spacious and very well-equipped.",
        "Room service was a bit slow but the food quality was good.",
        "Proximity to the theme parks genuinely can't be beat.",
        "Would recommend for any family traveling with children.",
        "Kids club staff were incredibly attentive and creative.",
        None,
    ],
    "P-004": [
        "Rooftop bar has stunning skyline views — worth every minute.",
        "Perfect location for theater and dining. Will absolutely be back.",
        "Boutique feel in the heart of Manhattan — exactly what I wanted.",
        "The curated local experience package was a genuinely great touch.",
        "Small but well-appointed rooms. The location more than makes up for it.",
        "Staff went above and beyond recommending local restaurants.",
        "Grand Central adjacency is so convenient for business travel.",
        "Clean, stylish, and walkable everywhere. My New York go-to.",
        "Rooftop happy hour is a definite highlight.",
        "The intimate atmosphere you don't find in big chain hotels.",
        None,
    ],
    "P-005": [
        "The hiking concierge planned the most perfect day in the Smokies.",
        "Farm-to-table dinner was one of the best meals of my life.",
        "Southern hospitality is absolutely real here. Everyone was so warm.",
        "Intimate setting — felt like a private mountain retreat.",
        "Perfect base for Smoky Mountain exploration.",
        "Cozy cabin-adjacent atmosphere. Beautifully designed property.",
        "Loved the emphasis on local produce at breakfast.",
        "Quiet, peaceful, and exactly what we needed to recharge.",
        "Staff arranged a private trail guide — completely unforgettable.",
        "The firepit evenings with s'mores were a magical touch.",
        None,
    ],
}

# ---------------------------------------------------------------------------
# Experience name templates  (8 per category × 5 categories = 40 per property)
# ---------------------------------------------------------------------------
EXPERIENCE_TEMPLATES = {
    "Spa": [
        "{p} Signature Massage",
        "{p} Deep Tissue Therapy",
        "{p} Aromatherapy Facial",
        "{p} Couples Retreat Package",
        "{p} Hot Stone Treatment",
        "{p} Hydrating Body Wrap",
        "{p} Express 30-Min Facial",
        "{p} Full Wellness Journey",
    ],
    "Dining": [
        "{p} Chef's Tasting Menu",
        "{p} Wine & Pairing Dinner",
        "{p} Sunset Terrace Dining",
        "{p} Private Dining Experience",
        "{p} Sunday Brunch Buffet",
        "{p} Cocktail & Tapas Hour",
        "{p} Seasonal Farm Dinner",
        "{p} In-Room Romance Package",
    ],
    "Activity": [
        "{p} Adventure Package",
        "{p} Nature Walk & Tour",
        "{p} Photography Experience",
        "{p} Guided Excursion",
        "{p} Outdoor Fitness Session",
        "{p} Local Cultural Tour",
        "{p} Evening Stargazing",
        "{p} Half-Day Exploration",
    ],
    "Kids": [
        "{p} Kids Club Day Pass",
        "{p} Junior Chef Workshop",
        "{p} Kids Movie Night",
        "{p} Treasure Hunt Adventure",
        "{p} Arts & Crafts Studio",
        "{p} Mini Spa for Kids",
        "{p} Kids' Birthday Experience",
        "{p} Family Scavenger Hunt",
    ],
    "Wellness": [
        "{p} Sunrise Yoga Session",
        "{p} Meditation & Mindfulness",
        "{p} Pilates Studio Class",
        "{p} Sound Bath Experience",
        "{p} Guided Breathwork",
        "{p} Nutrition Workshop",
        "{p} Forest Bathing Walk",
        "{p} Recovery & Stretch Class",
    ],
}

EXPERIENCE_PRICE_RANGE = {
    "Spa":      (85, 350),
    "Dining":   (65, 450),
    "Activity": (45, 295),
    "Kids":     (25, 125),
    "Wellness": (35, 150),
}

EXPERIENCE_DURATION = {
    "Spa":      ["60 min", "90 min", "120 min"],
    "Dining":   ["90 min", "2 hours", "3 hours"],
    "Activity": ["2 hours", "3 hours", "Half day", "Full day"],
    "Kids":     ["60 min", "90 min", "2 hours", "All day"],
    "Wellness": ["45 min", "60 min", "75 min"],
}

PROP_PREFIX = {
    "P-001": "Alpine",
    "P-002": "Oceanside",
    "P-003": "Sunshine",
    "P-004": "Midtown",
    "P-005": "Smoky",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def rand_date(start: date, end: date) -> date:
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, delta))


def rand_member_since(tier: str) -> date:
    """Platinum members older on average than Members."""
    today = date.today()
    if tier == "Platinum":
        return rand_date(today - timedelta(days=365 * 8), today - timedelta(days=365 * 2))
    elif tier == "Gold":
        return rand_date(today - timedelta(days=365 * 5), today - timedelta(days=365 * 1))
    elif tier == "Silver":
        return rand_date(today - timedelta(days=365 * 3), today - timedelta(days=180))
    else:
        return rand_date(today - timedelta(days=365 * 2), today - timedelta(days=30))


def rand_loyalty_points(tier: str) -> int:
    ranges = {
        "Platinum": (50_000, 250_000),
        "Gold":     (15_000,  50_000),
        "Silver":   (2_500,   15_000),
        "Member":   (0,        2_500),
    }
    lo, hi = ranges[tier]
    return random.randint(lo, hi)


def make_preferences() -> str:
    prefs = {
        "pillow_type":  random.choice(["firm", "soft", "medium", "down", "memory foam"]),
        "room_temp":    random.choice(["cool", "moderate", "warm"]),
        "dietary":      random.choice(["none", "vegetarian", "vegan", "gluten-free", "none", "none"]),
        "floor_pref":   random.choice(["low", "high", "no preference"]),
        "extra_towels": random.choice([True, False]),
    }
    return json.dumps(prefs)


def date_range(start: date, days: int):
    return [start + timedelta(days=i) for i in range(days)]


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

def generate_guests(rng: random.Random, count: int) -> list[dict]:
    guests = []
    used_ids = set(PERSONA_GUEST_IDS)
    used_emails = set()
    i = 1
    seq = 4000  # start IDs above persona range

    while len(guests) < count:
        gid = f"G-{seq:07d}"
        seq += 1
        if gid in used_ids:
            continue
        used_ids.add(gid)

        tier = rng.choices(LOYALTY_TIERS, weights=LOYALTY_WEIGHTS)[0]
        fn = rng.choice(FIRST_NAMES)
        ln = rng.choice(LAST_NAMES)

        email_base = f"{fn.lower()}.{ln.lower()}{rng.randint(1, 999)}"
        domain = rng.choice(["gmail.com", "outlook.com", "yahoo.com", "icloud.com", "hotmail.com"])
        email = f"{email_base}@{domain}"
        if email in used_emails:
            email = f"{email_base}{rng.randint(1000, 9999)}@{domain}"
        used_emails.add(email)

        area = rng.randint(200, 999)
        phone = f"({area}) {rng.randint(200,999)}-{rng.randint(1000,9999)}"

        guests.append({
            "GuestID":      gid,
            "FirstName":    fn,
            "LastName":     ln,
            "Email":        email,
            "Phone":        phone,
            "HomeCity":     rng.choice(HOME_CITIES),
            "Country":      "US",
            "LoyaltyTier":  tier,
            "LoyaltyPoints": rand_loyalty_points(tier),
            "MemberSince":  rand_member_since(tier).isoformat(),
            "Preferences":  make_preferences(),
        })
    return guests


def generate_experiences() -> list[dict]:
    """Generate 40 experiences per property (8 per category).
    Special IDs:
      EXP-P001-SPA-001 — Dana's Park City spa package (referenced in persona data)
      EXP-P003-KID-007 — Anne's Kids' Birthday Experience
    """
    experiences = []
    for prop in PROPERTIES:
        pid = prop["PropertyID"]
        prefix = PROP_PREFIX[pid]
        cat_idx = {cat: 0 for cat in EXPERIENCE_TEMPLATES}

        for cat, templates in EXPERIENCE_TEMPLATES.items():
            for j, tmpl in enumerate(templates):
                cat_idx[cat] += 1
                cat_code = cat[:3].upper()
                eid = f"EXP-{pid}-{cat_code}-{cat_idx[cat]:03d}"
                name = tmpl.format(p=prefix)

                # Special overrides for persona-critical experiences
                if pid == "P-001" and cat == "Spa" and j == 0:
                    eid = "EXP-P001-SPA-001"
                    name = "Alpine Signature Spa Package"
                if pid == "P-003" and cat == "Kids" and j == 6:
                    eid = "EXP-P003-KID-007"
                    name = "Sunshine Kids' Birthday Experience"

                lo, hi = EXPERIENCE_PRICE_RANGE[cat]
                price = round(random.uniform(lo, hi), 2)
                duration = random.choice(EXPERIENCE_DURATION[cat])

                experiences.append({
                    "ExperienceID": eid,
                    "PropertyID":   pid,
                    "Name":         name,
                    "Category":     cat,
                    "Description":  f"{name} — a curated {cat.lower()} offering at Contoso {prop['ShortName']}.",
                    "Price":        price,
                    "Duration":     duration,
                    "Available":    1,
                })
    return experiences


def generate_bookings(rng: random.Random, guests: list[dict], count: int) -> list[dict]:
    bookings = []
    today = date.today()
    history_start = today - timedelta(days=365 * 4)
    future_end    = today + timedelta(days=180)

    # Weighted property selection list
    prop_ids = [p["PropertyID"] for p in PROPERTIES]
    prop_weights = [PROPERTY_BOOKING_WEIGHT[pid] for pid in prop_ids]

    # Pre-build guest pool weighted by stay frequency (Platinum/Gold book more)
    tier_stay_weight = {"Platinum": 6, "Gold": 3, "Silver": 2, "Member": 1}
    guest_pool = []
    for g in guests:
        w = tier_stay_weight[g["LoyaltyTier"]]
        guest_pool.extend([g["GuestID"]] * w)

    seq = 10000  # booking sequence, leaves BK-0001xxx to BK-0003xxx for personas
    used_ids: set[str] = set()

    while len(bookings) < count:
        seq += 1
        bid = f"BK-{seq:07d}"
        if bid in used_ids:
            continue
        used_ids.add(bid)

        gid = rng.choice(guest_pool)
        pid = rng.choices(prop_ids, weights=prop_weights)[0]
        room_type = rng.choice(ROOM_TYPES)

        # Determine status and date window
        status_roll = rng.random()
        if status_roll < 0.70:
            status = "Completed"
            checkin = rand_date(history_start, today - timedelta(days=1))
        elif status_roll < 0.90:
            status = "Upcoming"
            checkin = rand_date(today + timedelta(days=1), future_end)
        else:
            status = "Cancelled"
            checkin = rand_date(history_start, future_end)

        nights = rng.randint(2, 7)
        checkout = checkin + timedelta(days=nights)

        lo, hi = RATE_RANGES[room_type]
        rate = round(rng.uniform(lo, hi), 2)
        total = round(rate * nights, 2)

        special_requests_pool = [
            None, None, None,  # most have none
            "Late checkout requested",
            "Extra pillows please",
            "Crib needed",
            "Quiet room away from elevator",
            "High floor preferred",
            "Allergy-free bedding required",
            "Anniversary — room decoration appreciated",
        ]
        booked_date = checkin - timedelta(days=rng.randint(1, 90))

        bookings.append({
            "BookingID":      bid,
            "GuestID":        gid,
            "PropertyID":     pid,
            "CheckIn":        checkin.isoformat(),
            "CheckOut":       checkout.isoformat(),
            "RoomType":       room_type,
            "RoomNumber":     f"{rng.randint(1, 12)}{rng.randint(0,9):02d}",
            "RatePerNight":   rate,
            "TotalAmount":    total,
            "Status":         status,
            "SpecialRequests": rng.choice(special_requests_pool),
            "BookedDate":     booked_date.isoformat(),
        })

    return bookings


def generate_inventory(bookings: list[dict]) -> list[dict]:
    """
    Build 90-day availability grid derived from actual booking data.
    Constraint: Orlando (P-003) Available=0 for all room types on Victor's blocked dates.
    """
    today = date.today()
    days = date_range(today, INVENTORY_DAYS)

    # Count booked rooms per (property, date, room_type) from Upcoming bookings
    booked_counts: dict[tuple, int] = {}
    for b in bookings:
        if b["Status"] == "Upcoming":
            checkin  = date.fromisoformat(b["CheckIn"])
            checkout = date.fromisoformat(b["CheckOut"])
            pid = b["PropertyID"]
            rt  = b["RoomType"]
            d = checkin
            while d < checkout:
                if today <= d < today + timedelta(days=INVENTORY_DAYS):
                    key = (pid, d, rt)
                    booked_counts[key] = booked_counts.get(key, 0) + 1
                d += timedelta(days=1)

    inventory = []
    for prop in PROPERTIES:
        pid = prop["PropertyID"]
        counts = ROOM_TYPE_COUNTS[pid]
        for d in days:
            for rt in ROOM_TYPES:
                total = counts[rt]
                booked = booked_counts.get((pid, d, rt), 0)
                available = max(0, total - booked)

                # Hard constraint: Orlando sold out on Victor's blocked window
                if pid == "P-003" and VICTOR_BLOCKED_START <= d <= VICTOR_BLOCKED_END:
                    booked = total
                    available = 0

                inventory.append({
                    "PropertyID":  pid,
                    "Date":        d.isoformat(),
                    "RoomType":    rt,
                    "TotalRooms":  total,
                    "BookedRooms": booked,
                    "Available":   available,
                })

    return inventory


def generate_surveys(rng: random.Random, bookings: list[dict]) -> list[dict]:
    """
    ~33% response rate on Completed bookings. Rating distributions skewed per property.
    Myrtle Beach (P-002) intentionally skews negative to support Dana's validation check.
    """
    completed = [b for b in bookings if b["Status"] == "Completed"]
    survey_targets = rng.sample(completed, min(TARGET_SURVEYS, len(completed)))

    surveys = []
    seq = 10001  # starts at SV-0010001; persona range SV-0001001–SV-0009999 reserved
    for b in survey_targets:
        pid = b["PropertyID"]
        cfg = SURVEY_CONFIG[pid]

        overall  = rng.randint(*cfg["overall"])
        nps      = rng.randint(*cfg["nps"])
        clean    = rng.randint(3, 5) if overall >= 7 else rng.randint(1, 4)
        service  = rng.randint(*cfg["service"])
        food     = rng.randint(*cfg["food"])
        spa      = rng.randint(1, 5) if rng.random() < cfg["spa_prob"] else None
        activity = rng.randint(1, 5) if rng.random() < 0.3 else None

        text_pool = FREE_TEXT_POOL[pid]
        free_text = rng.choice(text_pool)  # may be None

        checkin = date.fromisoformat(b["CheckIn"])
        submitted = checkin + timedelta(days=rng.randint(1, 14))

        surveys.append({
            "SurveyID":     f"SV-{seq:07d}",
            "GuestID":      b["GuestID"],
            "BookingID":    b["BookingID"],
            "PropertyID":   pid,
            "OverallRating": overall,
            "NPS":          nps,
            "Cleanliness":  clean,
            "Service":      service,
            "FoodBeverage": food,
            "Spa":          spa,
            "Activities":   activity,
            "FreeText":     free_text,
            "SubmittedDate": submitted.isoformat(),
        })
        seq += 1

    return surveys


# ---------------------------------------------------------------------------
# Service request generation (Epic 4.1 — HotSOS mirror)
# ---------------------------------------------------------------------------

_SR_DEPARTMENTS = ["Housekeeping", "Engineering", "F&B", "Front Desk", "Guest Services"]

_SR_CATEGORIES: dict[str, list[str]] = {
    "Housekeeping": ["Extra Towels", "Extra Linens", "Housekeeping Turndown", "Extra Pillows", "Crib Setup", "Deep Clean Request"],
    "Engineering":  ["AC Issue", "TV Not Working", "Plumbing Issue", "Safe Not Opening", "Light Bulb Out", "Noise from HVAC"],
    "F&B":          ["Room Service", "In-Room Dining Order", "Mini Bar Restock", "Special Dietary Request", "Coffee Setup"],
    "Front Desk":   ["Late Checkout", "Early Check-In", "Room Change", "Extra Key Card", "Luggage Storage"],
    "Guest Services": ["Transportation Request", "Concierge Booking", "Special Occasion Setup", "Package Delivery", "Wake-Up Call"],
}

_SR_PRIORITIES = ["Low", "Medium", "High", "Urgent"]
_SR_PRIORITY_WEIGHTS = [0.35, 0.40, 0.18, 0.07]

_SR_DEPT_WEIGHTS = [0.30, 0.20, 0.22, 0.18, 0.10]  # housekeeping most common

_SR_STATUSES = ["Completed", "Completed", "Completed", "Completed", "Cancelled", "InProgress"]


def _make_response_minutes(rng: random.Random, priority: str, status: str) -> int | None:
    """Generate a realistic ResponseMinutes value based on priority."""
    if status == "Cancelled":
        return None
    if priority == "Urgent":
        return rng.randint(5, 45)
    elif priority == "High":
        return rng.randint(15, 80)   # occasionally slow (>60)
    elif priority == "Medium":
        return rng.randint(20, 90)
    else:  # Low
        return rng.randint(30, 120)


def generate_service_requests(
    rng: random.Random,
    bookings: list[dict],
    count: int,
) -> list[dict]:
    """
    Generate ~count service requests from completed stays, excluding persona guests.
    ~1 request per 8 completed stays, distributed across all properties.
    """
    completed_non_persona = [
        b for b in bookings
        if b["Status"] == "Completed" and b["GuestID"] not in PERSONA_GUEST_IDS
    ]
    # Sample bookings that will get a service request (~1 in 8)
    sample_size = min(count, len(completed_non_persona))
    targets = rng.sample(completed_non_persona, sample_size)

    service_requests = []
    seq = 10000  # starts at SR-0010000; persona range SR-0001001–SR-0009999 reserved

    for b in targets:
        dept = rng.choices(_SR_DEPARTMENTS, weights=_SR_DEPT_WEIGHTS)[0]
        cat  = rng.choice(_SR_CATEGORIES[dept])
        priority = rng.choices(_SR_PRIORITIES, weights=_SR_PRIORITY_WEIGHTS)[0]
        status   = rng.choice(_SR_STATUSES)

        checkin   = datetime.fromisoformat(b["CheckIn"] + "T00:00:00")
        # Request happens 1-4 days into the stay
        stay_days = max(1, (date.fromisoformat(b["CheckOut"]) - date.fromisoformat(b["CheckIn"])).days)
        offset_hours = rng.randint(4, min(stay_days * 24 - 2, 96))
        req_dt = checkin + timedelta(hours=offset_hours)
        req_dt = req_dt.replace(hour=rng.randint(7, 22), minute=rng.randint(0, 59))

        resp_min = _make_response_minutes(rng, priority, status)

        completed_dt = None
        if resp_min is not None and status == "Completed":
            completed_dt = (req_dt + timedelta(minutes=resp_min + rng.randint(5, 60))).isoformat()

        service_requests.append({
            "RequestID":       f"SR-{seq:07d}",
            "GuestID":         b["GuestID"],
            "BookingID":       b["BookingID"],
            "PropertyID":      b["PropertyID"],
            "RequestedDate":   req_dt.isoformat(),
            "Department":      dept,
            "Category":        cat,
            "Description":     f"{cat} request during stay at Contoso {b.get('PropertyID', 'resort')}.",
            "Priority":        priority,
            "Status":          status,
            "AssignedTo":      None,
            "CompletedDate":   completed_dt,
            "ResponseMinutes": resp_min,
            "ResolutionNotes": None,
            "GuestSatisfied":  1 if status == "Completed" and rng.random() > 0.15 else None,
        })
        seq += 1

    return service_requests


# ---------------------------------------------------------------------------
# CSV writers
# ---------------------------------------------------------------------------

def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate(guests, bookings, inventory, surveys) -> list[str]:
    issues = []

    # No persona IDs in generated data
    guest_ids = {g["GuestID"] for g in guests}
    leaked = guest_ids & PERSONA_GUEST_IDS
    if leaked:
        issues.append(f"FAIL: Persona IDs leaked into guests: {leaked}")
    else:
        print(f"  ✓ No persona IDs in generated guests")

    # Orlando sold out on Victor's blocked dates
    orlando_blocked = [
        row for row in inventory
        if row["PropertyID"] == "P-003"
        and VICTOR_BLOCKED_START.isoformat() <= row["Date"] <= VICTOR_BLOCKED_END.isoformat()
    ]
    if any(int(row["Available"]) > 0 for row in orlando_blocked):
        issues.append("FAIL: Orlando has availability on Victor's blocked dates")
    else:
        print(f"  ✓ Orlando sold out {VICTOR_BLOCKED_START} to {VICTOR_BLOCKED_END} ({len(orlando_blocked)} rows)")

    # Myrtle Beach avg overall rating < 6
    mb_surveys = [s for s in surveys if s["PropertyID"] == "P-002"]
    if mb_surveys:
        avg = sum(int(s["OverallRating"]) for s in mb_surveys) / len(mb_surveys)
        if avg >= 6.0:
            issues.append(f"FAIL: Myrtle Beach avg rating is {avg:.2f} — should be < 6.0 for Dana signal")
        else:
            print(f"  ✓ Myrtle Beach avg overall rating: {avg:.2f} (target < 6.0)")

    # Dana's spa experience exists
    return issues


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Contoso Resorts demo data")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    random.seed(args.seed)  # for experience prices (uses module-level random)

    out_dir = Path(__file__).parent / "seed"
    print(f"\nContoso Resorts — Data Generation (seed={args.seed})")
    print(f"Output: {out_dir}\n")

    # --- Guests ---
    bulk_count = TARGET_GUESTS - len(PERSONA_GUEST_IDS)
    print(f"Generating {bulk_count:,} guests...")
    guests = generate_guests(rng, bulk_count)
    write_csv(out_dir / "guests.csv", guests, [
        "GuestID", "FirstName", "LastName", "Email", "Phone",
        "HomeCity", "Country", "LoyaltyTier", "LoyaltyPoints", "MemberSince", "Preferences"
    ])
    print(f"  → {len(guests):,} rows written to guests.csv")

    # --- Experiences ---
    print(f"Generating experiences ({EXPERIENCES_PER_PROPERTY} per property)...")
    experiences = generate_experiences()
    write_csv(out_dir / "experiences.csv", experiences, [
        "ExperienceID", "PropertyID", "Name", "Category", "Description", "Price", "Duration", "Available"
    ])
    print(f"  → {len(experiences):,} rows written to experiences.csv")

    # --- Bookings ---
    bulk_bookings = TARGET_BOOKINGS - 60  # reserve ~60 for persona bookings
    print(f"Generating {bulk_bookings:,} bookings...")
    bookings = generate_bookings(rng, guests, bulk_bookings)
    write_csv(out_dir / "bookings.csv", bookings, [
        "BookingID", "GuestID", "PropertyID", "CheckIn", "CheckOut",
        "RoomType", "RoomNumber", "RatePerNight", "TotalAmount",
        "Status", "SpecialRequests", "BookedDate"
    ])
    print(f"  → {len(bookings):,} rows written to bookings.csv")

    # --- Inventory ---
    print(f"Generating inventory ({INVENTORY_DAYS} days × 5 properties × 5 room types)...")
    inventory = generate_inventory(bookings)
    write_csv(out_dir / "inventory.csv", inventory, [
        "PropertyID", "Date", "RoomType", "TotalRooms", "BookedRooms", "Available"
    ])
    print(f"  → {len(inventory):,} rows written to inventory.csv")

    # --- Surveys ---
    print(f"Generating surveys (~{TARGET_SURVEYS:,} at 33% response rate)...")
    surveys = generate_surveys(rng, bookings)
    write_csv(out_dir / "surveys.csv", surveys, [
        "SurveyID", "GuestID", "BookingID", "PropertyID",
        "OverallRating", "NPS", "Cleanliness", "Service", "FoodBeverage",
        "Spa", "Activities", "FreeText", "SubmittedDate"
    ])
    print(f"  → {len(surveys):,} rows written to surveys.csv")

    # --- Service Requests ---
    print(f"Generating ~{TARGET_SERVICE_REQUESTS:,} service requests (Epic 4.1)...")
    service_requests = generate_service_requests(rng, bookings, TARGET_SERVICE_REQUESTS)
    write_csv(out_dir / "service_requests.csv", service_requests, [
        "RequestID", "GuestID", "BookingID", "PropertyID",
        "RequestedDate", "Department", "Category", "Description",
        "Priority", "Status", "AssignedTo", "CompletedDate",
        "ResponseMinutes", "ResolutionNotes", "GuestSatisfied",
    ])
    print(f"  → {len(service_requests):,} rows written to service_requests.csv")

    # --- Validation ---
    print("\nValidation checks:")
    issues = validate(guests, bookings, inventory, surveys)

    if issues:
        print("\n⚠  Issues found:")
        for issue in issues:
            print(f"   {issue}")
    else:
        print("\n✓ All constraints satisfied.")

    print("\nSummary:")
    print(f"  Guests:      {len(guests):>6,}  (+ 3 persona records hand-authored separately)")
    print(f"  Experiences: {len(experiences):>6,}  ({EXPERIENCES_PER_PROPERTY} per property)")
    print(f"  Bookings:    {len(bookings):>6,}  (+ ~60 persona bookings)")
    print(f"  Inventory:   {len(inventory):>6,}  rows")
    print(f"  Surveys:     {len(surveys):>6,}  (~33% response rate)")
    print(f"  Service Requests: {len(service_requests):>4,}  (Epic 4.1 — excl. persona records)")
    print(f"\nNext step: hand-author persona seed files (tasks 1.1.3, 1.1.4, 1.1.5 + 4.1.2)")


if __name__ == "__main__":
    main()
