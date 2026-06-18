from urllib.parse import unquote
from dotenv import load_dotenv
import os

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

def get_cookies():
    def _g(k: str) -> str:
        v = os.getenv(k)
        if not v:
            return ""
        return v

    xs_raw = os.getenv("FB_XS") or ""
    try:
        xs = unquote(xs_raw) if xs_raw else ""
    except Exception:
        xs = xs_raw

    return [
        {"name": "c_user", "value": _g("FB_C_USER"), "domain": ".facebook.com", "path": "/"},
        {"name": "xs",     "value": xs,               "domain": ".facebook.com", "path": "/"},
        {"name": "datr",   "value": _g("FB_DATR"),  "domain": ".facebook.com", "path": "/"},
        {"name": "fr",     "value": _g("FB_FR"),    "domain": ".facebook.com", "path": "/"},
        {"name": "sb",     "value": _g("FB_SB"),    "domain": ".facebook.com", "path": "/"},
    ]