from urllib.parse import unquote
from dotenv import load_dotenv
import os

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

def get_all_cookie_profiles() -> list[list[dict]]:
    def _g(k: str, suffix: str = "") -> str:
        v = os.getenv(f"{k}{suffix}")
        return v if v else ""

    profiles = []
    
    # Check default account (no suffix)
    if os.getenv("FB_C_USER") and os.getenv("FB_XS"):
        xs_raw = os.getenv("FB_XS") or ""
        try:
            xs = unquote(xs_raw) if xs_raw else ""
        except Exception:
            xs = xs_raw
            
        profiles.append([
            {"name": "c_user", "value": _g("FB_C_USER"), "domain": ".facebook.com", "path": "/"},
            {"name": "xs",     "value": xs,               "domain": ".facebook.com", "path": "/"},
            {"name": "datr",   "value": _g("FB_DATR"),  "domain": ".facebook.com", "path": "/"},
            {"name": "fr",     "value": _g("FB_FR"),    "domain": ".facebook.com", "path": "/"},
            {"name": "sb",     "value": _g("FB_SB"),    "domain": ".facebook.com", "path": "/"},
        ])
        
    # Check backup accounts (suffix _1, _2, _3...)
    for i in range(1, 10):
        suffix = f"_{i}"
        if os.getenv(f"FB_C_USER{suffix}") and os.getenv(f"FB_XS{suffix}"):
            xs_raw = os.getenv(f"FB_XS{suffix}") or ""
            try:
                xs = unquote(xs_raw) if xs_raw else ""
            except Exception:
                xs = xs_raw
                
            profiles.append([
                {"name": "c_user", "value": _g("FB_C_USER", suffix), "domain": ".facebook.com", "path": "/"},
                {"name": "xs",     "value": xs,               "domain": ".facebook.com", "path": "/"},
                {"name": "datr",   "value": _g("FB_DATR", suffix),  "domain": ".facebook.com", "path": "/"},
                {"name": "fr",     "value": _g("FB_FR", suffix),    "domain": ".facebook.com", "path": "/"},
                {"name": "sb",     "value": _g("FB_SB", suffix),    "domain": ".facebook.com", "path": "/"},
            ])
            
    return profiles


def get_all_cookie_profiles_labeled() -> list[dict]:
    """Return cookie profiles with human-readable labels and env suffixes.
    
    Each item is a dict with:
        - cookies: list[dict] — the cookie dicts for Playwright
        - label: str — human-readable label e.g. "Account 1 (Primary)"
        - env_suffix: str — the env var suffix e.g. "" for primary, "_1" for backup 1
    """
    def _g(k: str, suffix: str = "") -> str:
        v = os.getenv(f"{k}{suffix}")
        return v if v else ""

    profiles = []

    # Check default account (no suffix)
    if os.getenv("FB_C_USER") and os.getenv("FB_XS"):
        from urllib.parse import unquote
        xs_raw = os.getenv("FB_XS") or ""
        try:
            xs = unquote(xs_raw) if xs_raw else ""
        except Exception:
            xs = xs_raw

        profiles.append({
            "cookies": [
                {"name": "c_user", "value": _g("FB_C_USER"), "domain": ".facebook.com", "path": "/"},
                {"name": "xs",     "value": xs,               "domain": ".facebook.com", "path": "/"},
                {"name": "datr",   "value": _g("FB_DATR"),  "domain": ".facebook.com", "path": "/"},
                {"name": "fr",     "value": _g("FB_FR"),    "domain": ".facebook.com", "path": "/"},
                {"name": "sb",     "value": _g("FB_SB"),    "domain": ".facebook.com", "path": "/"},
            ],
            "label": "Account 1 (Primary)",
            "env_suffix": "",
        })

    # Check backup accounts (suffix _1, _2, _3...)
    for i in range(1, 10):
        suffix = f"_{i}"
        if os.getenv(f"FB_C_USER{suffix}") and os.getenv(f"FB_XS{suffix}"):
            from urllib.parse import unquote
            xs_raw = os.getenv(f"FB_XS{suffix}") or ""
            try:
                xs = unquote(xs_raw) if xs_raw else ""
            except Exception:
                xs = xs_raw

            profiles.append({
                "cookies": [
                    {"name": "c_user", "value": _g("FB_C_USER", suffix), "domain": ".facebook.com", "path": "/"},
                    {"name": "xs",     "value": xs,                       "domain": ".facebook.com", "path": "/"},
                    {"name": "datr",   "value": _g("FB_DATR", suffix),  "domain": ".facebook.com", "path": "/"},
                    {"name": "fr",     "value": _g("FB_FR", suffix),    "domain": ".facebook.com", "path": "/"},
                    {"name": "sb",     "value": _g("FB_SB", suffix),    "domain": ".facebook.com", "path": "/"},
                ],
                "label": f"Account {i + 1} (Backup #{i})",
                "env_suffix": suffix,
            })

    return profiles