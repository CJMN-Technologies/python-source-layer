import os
import re
from typing import Optional
from google import genai

# In-memory cache so client.models.list() is only called once per scraper process
_CACHED_MODELS: Optional[list[str]] = None

EXCLUDED_MODEL_PATTERNS = {
    "tts",
    "transcribe",
    "embedding",
    "robotics",
    "live",
    "audio",
    "customtools",
    "computer-use",
}


def _extract_version_tuple(model_name: str) -> tuple:
    """Extract numerical version from model name for sorting (e.g. 'gemini-3.5-flash' -> (3, 5))."""
    match = re.search(r"gemini-(\d+)(?:\.(\d+))?", model_name)
    if match:
        major = int(match.group(1))
        minor = int(match.group(2)) if match.group(2) else 0
        return (major, minor)
    return (0, 0)


def get_gemini_models(client: Optional[genai.Client] = None, api_key: Optional[str] = None, task: str = "general") -> list[str]:
    """
    Dynamically discover and return available Gemini models suitable for text or multimodal tasks.
    Prioritizes:
      1. Explicit environment variable overrides (GEMINI_MODEL / GEMINI_OCR_MODEL)
      2. Official forward-compatible aliases ('gemini-flash-latest', 'gemini-flash-lite-latest')
      3. Live models discovered from Google API via client.models.list(), sorted newest to oldest
      4. Safe fallback baseline models

    Prevents silent breakages when Google sunsets specific model versions.
    """
    global _CACHED_MODELS

    # 1. Environment variable override
    env_override = None
    if task == "vision":
        env_override = os.getenv("GEMINI_OCR_MODEL") or os.getenv("GEMINI_MODEL")
    else:
        env_override = os.getenv("GEMINI_MODEL")

    if env_override:
        return [m.strip() for m in env_override.split(",") if m.strip()]

    # 2. Return cached list if already discovered in this process
    if _CACHED_MODELS:
        return list(_CACHED_MODELS)

    candidates: list[str] = []

    # Safe baseline forward-compatible aliases
    baseline_aliases = ["gemini-flash-latest", "gemini-flash-lite-latest"]

    # 3. Dynamic discovery via client.models.list()
    active_client = client
    if not active_client and api_key:
        try:
            active_client = genai.Client(api_key=api_key)
        except Exception:
            active_client = None

    if active_client:
        try:
            discovered_raw = active_client.models.list()
            discovered_flash: list[str] = []

            for m in discovered_raw:
                raw_name = m.name or ""
                clean_name = raw_name.replace("models/", "")
                name_lower = clean_name.lower()

                # Filter for Flash models
                if "flash" in name_lower and not any(ex in name_lower for ex in EXCLUDED_MODEL_PATTERNS):
                    discovered_flash.append(clean_name)

            # Sort discovered models from newest version to older (e.g. 3.8, 3.7, 3.6, 3.5, 2.5)
            # Aliases without version numbers go first, followed by versioned models descending
            versioned = [m for m in discovered_flash if not m.endswith("-latest")]
            versioned.sort(key=_extract_version_tuple, reverse=True)

            aliases = [m for m in discovered_flash if m.endswith("-latest")]

            # Combine: preferred aliases first, then newest versioned models
            ordered = []
            for a in baseline_aliases:
                if a in aliases and a not in ordered:
                    ordered.append(a)
            for a in aliases:
                if a not in ordered:
                    ordered.append(a)
            for v in versioned:
                if v not in ordered:
                    ordered.append(v)

            if ordered:
                candidates = ordered
        except Exception as e:
            print(f"  [ModelResolver] Note: Dynamic model discovery had an issue ({e}). Using resilient fallback list.")

    if not candidates:
        candidates = [
            "gemini-flash-latest",
            "gemini-flash-lite-latest",
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-3.5-flash",
            "gemini-3.5-flash-lite",
        ]

    _CACHED_MODELS = candidates
    return list(_CACHED_MODELS)
