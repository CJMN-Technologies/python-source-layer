import json
import time
import os
from google import genai
from pydantic import BaseModel
from typing import Optional

# Local imports
from fb_scraper import _get_gemini_keys


class PostClassification(BaseModel):
    category: Optional[str]
    event_name: Optional[str]
    event_date: Optional[str]


def classify_post_llm(post_text: str) -> dict:
    """
    Passes the post text to Gemini to classify and extract event details.
    Returns a dict with 'category', 'event_name', and 'event_date'.

    Category values:
      - 'academic'          — class suspensions, resumptions, school holidays,
                              exam weeks, enrollment, graduation, orientations
      - 'lgu'               — government advisories, weather events, transport
                              disruptions, road closures, concert/arena events,
                              strikes, LRT-2 service alerts
      - 'academic_calendar' — a post sharing a full academic calendar document
      - null                — not relevant to LRT-2 ridership (e.g. general news,
                              birthday greetings, food posts, generic promos)
    """
    if not post_text or not post_text.strip():
        return {"category": None, "event_name": None, "event_date": None}

    prompt = f"""
You are a highly accurate data extraction assistant for an LRT-2 ridership impact events pipeline in the Philippines.

Analyze the following Facebook post text from a university, student council, or local government unit (LGU) in the Philippines.
Your task is to CLASSIFY the post into one of the following categories AND extract event details if applicable.

=== FRICTION INDEX REFERENCE (what affects LRT-2 ridership) ===
The following trigger types are relevant and SHOULD be classified (do not reject them):

CATEGORY "lgu":
  - Full Suspension / Power Failure (LRT-2 not operating)
  - Transport Strike: Tigil Pasada, Jeepney Strike, Welga ng Drivers
  - Partial Line Suspension (e.g. Cubao-Antipolo only, provisionary service)
  - Mid-Day Class Suspension (LGU-announced, e.g. 12:00 PM suspension)
  - Torrential Rain / Orange or Red PAGASA Warning
  - Typhoon Signal No. 2 or higher
  - Typhoon Signal No. 1 (light)
  - Heavy Rain / Yellow PAGASA Warning
  - Major Arena / Concert Event (Smart Araneta, PhilSports, MOA Arena — causes ridership spike)
  - Code Yellow / Degraded Headway / Delayed Train (LRT-2 running slow)
  - Road Closure, Government Offices Closed, LGU Advisories
  - State of Calamity / Disaster Declarations
  - Weather advisories (Habagat, Amihan, Monsoon)
  - Baha / Flash Flood Advisories

CATEGORY "academic":
  - Class Suspension (school-specific or campus-wide)
  - Resumption of Classes
  - University Exam Week (Midterms, Finals — causes ridership change)
  - Enrollment / Registration Period
  - School Orientations / Back-to-School
  - Graduation / Commencement Ceremonies
  - Intramurals / University Week / Foundation Day (affects ridership)
  - School Holidays / Holiday Breaks
  - Asynchronous / Online / Modular Classes
  - Walang Pasok, Walang Klase, Suspendido ang Klase (Tagalog equivalents)
  - Schedule changes specific to one or more campuses

CATEGORY "academic_calendar":
  - A post explicitly announcing or sharing a full Academic Calendar, School Calendar,
    Collegiate Calendar, or University Calendar for an entire semester/year.
  - Only use this if the post is PRIMARILY a calendar schedule, NOT a specific event announcement.

CATEGORY null (reject — not relevant):
  - Generic greetings (Happy Birthday, Merry Christmas) with no event content
  - Promotional posts for merchandise, food, services
  - Generic motivational or inspirational quotes
  - Job postings or recruitment ads
  - News articles about topics unrelated to classes, transportation, or local government
  - Alumni events with no impact on current students or commuters

=== EXTRACTION RULES ===
- For "academic" and "lgu": extract event_name (concise, e.g. "Class Suspension due to Typhoon Carina")
  and event_date (e.g. "2026-06-25" or "June 25-26, 2026"). If date is unclear, write "Not specified".
- For "academic_calendar": leave event_name and event_date as null.
- For null: leave everything as null.
- Output ONLY valid JSON matching the schema. Do not add explanations.

=== POST TEXT ===
\"\"\"{post_text}\"\"\"
"""

    keys = _get_gemini_keys()
    if not keys:
        print("Warning: No Gemini API keys found for LLM classification.")
        return {"category": None, "event_name": None, "event_date": None}

    # Model fallback: start with cheapest to conserve quota
    models_to_try = ["gemini-2.5-flash-lite", "gemini-2.5-flash"]

    for attempt in range(3):  # up to 3 retry rounds (handles 503 spikes)
        for api_key in keys:
            for model_name in models_to_try:
                try:
                    client = genai.Client(api_key=api_key)
                    response = client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config={
                            "response_mime_type": "application/json",
                            "response_schema": PostClassification,
                            "temperature": 0.1,
                        },
                    )
                    if response.text:
                        try:
                            data = json.loads(response.text)
                            # Normalize "null" string to actual None
                            for k, v in data.items():
                                if isinstance(v, str) and v.lower() in ("null", "none", "n/a", ""):
                                    data[k] = None

                            # Validate category
                            valid_cats = ["academic", "lgu", "academic_calendar"]
                            if data.get("category") not in valid_cats:
                                data["category"] = None

                            return data
                        except json.JSONDecodeError:
                            pass
                except Exception as e:
                    err_str = str(e)
                    if "429" in err_str or "quota" in err_str.lower() or "RESOURCE_EXHAUSTED" in err_str:
                        # Quota exhausted on this key — try next key immediately
                        print(f"  [{model_name}] Quota exceeded on key, trying next...")
                        break  # break model loop, try next key
                    elif "503" in err_str or "UNAVAILABLE" in err_str:
                        # Temporary server spike — wait with backoff then retry same key/model
                        wait = 2 ** attempt
                        print(f"  [{model_name}] 503 server spike, retrying in {wait}s...")
                        time.sleep(wait)
                        continue
                    else:
                        print(f"  Error calling Gemini [{model_name}] for classification: {e}")

        if attempt < 2:
            time.sleep(2 ** attempt)  # backoff between full rounds

    return {"category": None, "event_name": None, "event_date": None}
