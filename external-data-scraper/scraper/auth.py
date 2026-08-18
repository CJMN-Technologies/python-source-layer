from urllib.parse import unquote
from dotenv import load_dotenv
import os

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

def get_all_cookie_profiles() -> list[list[dict]]:
    def _g(k: str, suffix: str = "") -> str:
        v = os.getenv(f"{k}{suffix}")
        return v if v else ""

    profiles = []


def get_cookies() -> list[dict]:
    """Backward-compatible helper returning primary account cookies."""
    profiles = get_all_cookie_profiles()
    return profiles[0] if profiles else []
    
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


def get_all_cookie_profiles_labeled(batch: str = "all") -> list[dict]:
    """Return cookie profiles with human-readable labels and env suffixes.

    When running parallel matrix batches (e.g. Eastbound vs Westbound),
    this function partitions the available accounts so concurrent runners
    never share or collide on the same Facebook account simultaneously.
    """
    def _g(k: str, suffix: str = "") -> str:
        v = os.getenv(f"{k}{suffix}")
        return v if v else ""

    all_profiles = []

    # Check default account (no suffix)
    if os.getenv("FB_C_USER") and os.getenv("FB_XS"):
        from urllib.parse import unquote
        xs_raw = os.getenv("FB_XS") or ""
        try:
            xs = unquote(xs_raw) if xs_raw else ""
        except Exception:
            xs = xs_raw

        all_profiles.append({
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

            all_profiles.append({
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

    if not all_profiles:
        return []

    batch_key = (batch or "all").strip().lower()

    # Dedicated Pool Allocation:
    # If 2 or more accounts exist, split across Eastbound and Westbound to prevent dual-runner collisions
    if len(all_profiles) >= 2:
        if batch_key in ("eastbound", "east", "a"):
            # Eastbound gets even-indexed accounts: Account 1 (Primary), Account 3 (Backup 2), etc.
            east_pool = [p for i, p in enumerate(all_profiles) if i % 2 == 0]
            return east_pool if east_pool else all_profiles
        elif batch_key in ("westbound", "west", "b"):
            # Westbound gets odd-indexed accounts: Account 2 (Backup 1), Account 4 (Backup 3), etc.
            west_pool = [p for i, p in enumerate(all_profiles) if i % 2 != 0]
            return west_pool if west_pool else all_profiles

    return all_profiles