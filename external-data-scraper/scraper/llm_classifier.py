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
    event_code: Optional[str] = None
    is_cancellation: Optional[bool] = False
    cancellation_target_code: Optional[str] = None


def classify_post_llm(post_text: str, image_text: str = None) -> dict:
    """
    Passes the post text and image OCR text to Gemini to classify and extract event details.
    Returns a dict with 'category', 'event_name', 'event_date', 'event_code', 'is_cancellation', and 'cancellation_target_code'.
    """
    caption_content = (post_text or "").strip()
    image_content = (image_text or "").strip()

    if not caption_content and not image_content:
        return {"category": None, "event_name": None, "event_date": None, "event_code": None, "is_cancellation": False, "cancellation_target_code": None}

    from datetime import datetime, timezone
    ref_now = datetime.now(timezone.utc)
    ref_date_str = ref_now.strftime('%A, %B %d, %Y')
    ref_year = ref_now.year

    combined_input = f"POST CAPTION / TEXT:\n{caption_content or '[None]'}\n\nIMAGE OCR / GRAPHIC TEXT:\n{image_content or '[None]'}"

    prompt = f"""
You are a highly accurate data extraction assistant for an LRT-2 ridership impact events pipeline in the Philippines.

=== CURRENT DATE & REFERENCE YEAR ===
Today's date is: {ref_date_str}. The current reference year is: {ref_year}.

Analyze the following Facebook post caption AND image graphic OCR text from a university, student council, or local government unit (LGU).

CRITICAL CONTEXT & DISCRIMINATION RULES:

1. IMAGE TYPE DISCRIMINATION (OFFICIAL GRAPHIC vs REAL-WORLD SCENE SNAPSHOT):
   - TYPE A: OFFICIAL ANNOUNCEMENT / ADVISORY GRAPHIC (Formal Poster/Card)
     * Formal graphics published by schools/LGUs with titles like "ADVISORY", "ANNOUNCEMENT", "WALANG PASOK", "CLASS SUSPENSION", "NOTICE", official seals, or clean template typography.
     * Dates inside Type A graphics ARE valid event dates.
   - TYPE B: REAL-WORLD SCENE / STREET / LOCATION SNAPSHOT (Photographs)
     * Real-life photographs of flooded roads, city hall surroundings, building facades, crowds, or traffic.
     * STRICT MANDATORY RULE FOR TYPE B SNAPSHOTS: IGNORE background commercial text, mall banners (e.g. "SALE AUG 14-16"), store signs, storefront ads, or street signs inside real-world scene photos. They are background artifacts, NOT event dates!
     * For Type B scene snapshots, extract event_date EXCLUSIVELY from the post caption text or default to the current reference date ({ref_year}).

2. HIGH-IMPACT CONCISE EVENT NAMING (event_name):
   - Extract a CONCISE, HIGH-IMPACT summary for event_name (STRICT MAXIMUM: 5 to 8 words).
   - Ensure the user immediately understands the exact incident/advisory on first read without reading long paragraphs.
   - Good examples: "Manila City Hall Vicinity Flood Update", "Class Suspension: All Levels (Manila)", "PAGASA Heavy Rainfall Warning".
   - Bad examples: "Manila City Hall Vicinity Flood Update and Weather Advisory in View of Continued Heavy Rainfall associated with Southwest Monsoon".

3. CURRENT YEAR ENFORCEMENT:
   - When an announcement mentions a month and day without an explicit year (e.g. "August 13" or "Thursday, August 13"), ALWAYS set event_date using the current reference year ({ref_year}, e.g. "{ref_year}-08-13").
   - NEVER output past years (e.g. 2024 or 2025) for freshly scraped current advisories unless the post text explicitly states that past year.

4. CANCELLATION / RESUMPTION DETECTION:
   - If an announcement mentions that an event/activity/strike/exam/suspension is CALLED OFF, CANCELLED, LIFTED, POSTPONED, or RESUMED:
     Set is_cancellation = true
     Set cancellation_target_code to the target event_code (e.g. TRANSPORT_STRIKE, CLASS_SUSPENSION, EXAM_WEEK, FRESHMEN_ORIENTATION).

5. Standardize event_code for all events:
   - TRANSPORT_STRIKE (jeepney strike, tigil pasada, transport disruption)
   - CLASS_SUSPENSION (walang pasok, suspended classes, shift to online)
   - RESUMPTION_CLASSES (resumption of classes/work)
   - EXAM_WEEK (midterms, finals, departmental exams)
   - FRESHMEN_ORIENTATION (Thomasian welcome, freshmen week, onboarding)
   - CIVIC_MAINTENANCE (tree trimming, road clearance, pruning)
   - WEATHER_ADVISORY (pagasa warning, habagat, monsoon, typhoon)

6. TRUNCATED CAPTION HANDLING & RESILIENCE:
   - If the post caption text or OCR text ends abruptly or appears cut off with ellipses ('...') or trailing truncated words (e.g. "classes at al…"), evaluate available headline keywords (e.g. "Advisory", "Memorandum Circular No.", "In view of"), official organization name, and available image text.
   - Do NOT reject an advisory as null simply because a post caption cuts off before completing a sentence. If the post signals an official school/LGU advisory or class/work adjustment, output category ("academic" or "lgu") and a concise 5-8 word event_name based on the core advisory intent.

7. STUDENT COUNCIL ADVOCACY & POLITICAL CRITIQUES vs OFFICIAL SUSPENSIONS:
   - If a post from a student council (e.g. USC, Student Council) is a political statement, press release, commentary on governance/flood control, or petition asking for accountability/leniency (even if it uses rhetorical slogans like 'Walang Pasok dahil sa korapsyon' or 'Panawagan'), do NOT classify it as CLASS_SUSPENSION.
   - If it does not announce a confirmed, declared suspension by the University administration or LGU, output category = null.

=== FRICTION INDEX REFERENCE (what affects LRT-2 ridership) ===
The following trigger types are relevant and SHOULD be classified:

CATEGORY "lgu":
  - Transport Strike: Tigil Pasada, Jeepney Strike, Welga ng Drivers
  - Mid-Day / Full Class Suspension announced by LGU
  - Torrential Rain / Orange or Red PAGASA Warning / Signal No. 2+
  - Tree Trimming / Road Clearance / Obstruction
  - Major Arena / Concert Event (Smart Araneta, PhilSports, MOA Arena)
  - LRT-2 Code Yellow / Service Delay / Degraded Headway
  - State of Calamity / Disaster Declarations
  - Weather advisories (Habagat, Amihan, Monsoon, Baha)

CATEGORY "academic":
  - Class Suspension / Holiday (school-specific or campus-wide)
  - Cancellation of Examinations / Medical Clearances
  - Resumption of Classes
  - Active University Exam Week (Midterms, Finals — only if exams are ACTUALLY being held, NOT cancelled)
  - Enrollment / Registration Period
  - Graduation / Commencement Ceremonies
  - School Holidays / Academic Breaks
  - Walang Pasok, Walang Klase, Suspendido ang Klase

CATEGORY "academic_calendar":
  - A post explicitly announcing or sharing a full Academic Calendar schedule document.

CATEGORY null (reject — not relevant):
  - Student council political commentary, statements of solidarity, corruption critiques, or petitions lacking official university administrative declaration.
  - Generic greetings, food/merchandise promos, job ads, alumni news with no commuter impact.

=== EXTRACTION RULES ===
- For "academic" and "lgu": extract event_name (5-8 words max summary), event_date (YYYY-MM-DD or YYYY-MM-DD to YYYY-MM-DD), event_code, is_cancellation (boolean), and cancellation_target_code.
- Output ONLY valid JSON matching the schema.

=== INPUT CONTENT ===
{combined_input}
"""

    keys = _get_gemini_keys()
    if not keys:
        print("Warning: No Gemini API keys found for LLM classification.")
        return {"category": None, "event_name": None, "event_date": None}

    # Model fallback: start with cheapest to conserve quota
    models_to_try = ["gemini-3.5-flash-lite", "gemini-3.5-flash"]

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

    return {"category": None, "event_name": None, "event_date": None, "llm_failed": True}
