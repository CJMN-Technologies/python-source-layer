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
    Category can be 'academic', 'lgu', 'academic_calendar', or None.
    """
    if not post_text or not post_text.strip():
        return {"category": None, "event_name": None, "event_date": None}

    prompt = f"""
You are a highly accurate data extraction assistant for an events pipeline.
Analyze the following Facebook post text from a university, student council, or local government unit in the Philippines.

Your task is to classify the post into one of the following categories, AND extract the event details if applicable.

Categories:
1. "academic" - Event related to class suspensions, resumptions, schedule changes, school holidays, or ANY campus-specific events (e.g. enrollment schedules, orientations, exams, graduation, activities).
2. "lgu" - Event related to local government unit announcements (e.g., city-wide suspensions, typhoons, road closures) that affect the public.
3. "academic_calendar" - A post explicitly announcing or sharing an Academic Calendar, School Calendar, or Collegiate Calendar.
4. null - If the post does NOT fit any of the above (e.g., general greetings, generic news, intramurals without class suspension, etc.).

Extraction Rules:
- If category is "academic" or "lgu", extract the `event_name` (e.g., "Class Suspension due to Typhoon", "Enrollment Period") and `event_date` (e.g., "2026-06-25" or "June 25-26, 2026").
- If category is "academic_calendar", leave `event_name` and `event_date` as null.
- If category is null, leave everything as null.
- Always output JSON matching the requested schema.

Post Text:
\"\"\"{post_text}\"\"\"
"""

    keys = _get_gemini_keys()
    if not keys:
        print("Warning: No Gemini API keys found for LLM classification.")
        return {"category": None, "event_name": None, "event_date": None}

    # Attempt to cycle through keys if quota is exceeded
    for _ in range(2):
        for api_key in keys:
            try:
                client = genai.Client(api_key=api_key)
                response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt,
                    config={
                        'response_mime_type': 'application/json',
                        'response_schema': PostClassification,
                        'temperature': 0.1,
                    },
                )
                if response.text:
                    try:
                        data = json.loads(response.text)
                        # Normalize "null" string to actual None just in case
                        for k, v in data.items():
                            if isinstance(v, str) and v.lower() == "null":
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
                if "429" in err_str or "quota" in err_str.lower():
                    continue # Try next key
                else:
                    print(f"Error calling Gemini for classification: {e}")
                    
        time.sleep(2) # brief delay before retry if all keys failed

    return {"category": None, "event_name": None, "event_date": None}
