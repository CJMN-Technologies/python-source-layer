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


def classify_post_llm(post_text: str, image_text: str = None) -> dict:
    """
    Passes the post text and image OCR text to Gemini to classify and extract event details.
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
    caption_content = (post_text or "").strip()
    image_content = (image_text or "").strip()

    if not caption_content and not image_content:
        return {"category": None, "event_name": None, "event_date": None}

    combined_input = f"POST CAPTION / TEXT:\n{caption_content or '[None]'}\n\nIMAGE OCR / GRAPHIC TEXT:\n{image_content or '[None]'}"

    prompt = f"""
You are a highly accurate data extraction assistant for an LRT-2 ridership impact events pipeline in the Philippines.

Analyze the following Facebook post caption AND image graphic OCR text from a university, student council, or local government unit (LGU).

CRITICAL CONTEXT & CANCELLATION RULES:
1. BOTH the Post Caption and Image Graphic Text MUST be evaluated together.
2. If an announcement mentions "CANCELLATION OF EXAMINATIONS", "SUSPENSION OF EXAMS", "SUSPENDED TRANSACTIONS", or "WALANG PASOK / CLASS SUSPENSION", you MUST classify it under "academic" as a Class Suspension / Holiday, NOT as an active exam week!
3. If an LGU post mentions "Tree Trimming", "Road Clearance", "Infrastructure Maintenance", or "Pruning" (even if Habagat/Monsoon is mentioned as background context), extract the event_name as the specific maintenance activity (e.g. "Tree trimming activity due to HabagatPH"), category "lgu".

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
  - Generic greetings, food/merchandise promos, job ads, alumni news with no commuter impact.

=== EXTRACTION RULES ===
- For "academic" and "lgu": extract event_name (concise, e.g. "Class Suspension due to Typhoon Carina", "Cancellation of Medical Examinations", "Tree trimming activity due to HabagatPH")
  and event_date. The event_date MUST be formatted as YYYY-MM-DD. If it spans multiple days, format as "YYYY-MM-DD to YYYY-MM-DD". If the date is unclear, write "Not specified".
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
