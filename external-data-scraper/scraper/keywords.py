# ============================================================
# keywords.py — LRT-2 Scraper Event Keywords
#
# ALL matching is done via .casefold() on both the keyword
# and the input text, which handles:
#   - lowercase:   "walang pasok"
#   - Title Case:  "Walang Pasok"
#   - ALL CAPS:    "WALANG PASOK", "NO CLASSES"
#   - Mixed:       "WALANG Pasok", "Class SUSPENDED"
#
# Keyword groups are aligned to external.friction_weight table.
# ============================================================

from unicode_normalizer import normalize_unicode_text


# ---------------------------------------------------------------------------
# GROUP 1 — CLASS SUSPENSION / SCHOOL HOLIDAY (friction: high)
# Covers: walang pasok, class suspension, holiday, etc.
# ---------------------------------------------------------------------------
CLASS_SUSPENSION_KEYWORDS = [
    # English — standard phrases
    "no classes",
    "class suspension",
    "classes suspended",
    "suspended classes",
    "class suspended",
    "school suspension",
    "school closed",
    "university closed",
    "campus closed",
    "school closure",
    "class cancellation",
    "cancelled classes",
    "class postponement",
    "postponed classes",
    "resumption of classes",
    "classes will resume",
    "classes resume",
    "class resumption",
    "no classes and work",
    "no classes and office work",
    "asynchronous classes",
    "shift to online classes",
    "online classes",
    "flexible learning",
    "distance learning",
    "modular classes",
    "blended learning",
    # English — holidays
    "regular holiday",
    "special non-working holiday",
    "non-working holiday",
    "special working holiday",
    "school holiday",
    "academic holiday",
    "holiday break",
    "long weekend",
    "holiday tomorrow",
    "public holiday",
    # Tagalog / Taglish
    "walang pasok",
    "walang klase",
    "walang pasok sa klase",
    "suspendido ang klase",
    "suspendido ang pasok",
    "kanselado ang klase",
    "suspensyon ng klase",
    "pagpapatuloy ng klase",
    "pampublikong pahinga",
    "espesyal na walang pasok",
    "regular na pahinga",
    "bukas ang pasok",
    "petsa ng pasok",
    "huling araw ng klase",
]

# ---------------------------------------------------------------------------
# GROUP 2 — LGU / GOVERNMENT ADVISORIES (friction: high)
# Covers: city-wide suspensions, weather, calamity state, road closures
# ---------------------------------------------------------------------------
LGU_ADVISORY_KEYWORDS = [
    # Work suspension
    "no office transactions",
    "offices closed",
    "government offices closed",
    "suspension of work",
    "no work",
    "skeletal workforce",
    "work from home",
    # Weather
    "weather advisory",
    "storm signal",
    "signal number",
    "signal no.",
    "typhoon advisory",
    "tropical storm",
    "super typhoon",
    "monsoon",
    "habagat",
    "amihan",
    "bagyo",
    "baha",
    "flash flood",
    "flood advisory",
    "orange warning",
    "red warning",
    "yellow warning",
    "rainfall advisory",
    "heavy rain",
    "torrential rain",
    # State of calamity
    "estado ng kalamidad",
    "state of calamity",
    "calamity",
    "disaster",
    "force majeure",
    # Road / City events
    "road closure",
    "road closed",
    "partial road closure",
    # Tagalog
    "suspendido ang trabaho",
    "walang opisina",
    "walang trabaho",
    "sarado ang opisina",
    "advisory ng lgu",
]

# ---------------------------------------------------------------------------
# GROUP 3 — TRANSPORT STRIKES & DISRUPTIONS (friction: 0.85–1.0)
# Directly aligned to: "Full Suspension, Power Failure" (1.0)
#                      "Transport Strike, Tigil Pasada" (0.9)
#                      "Partial Line Suspension" (0.85)
# ---------------------------------------------------------------------------
TRANSPORT_DISRUPTION_KEYWORDS = [
    # Strikes
    "tigil pasada",
    "transport strike",
    "jeepney strike",
    "drivers strike",
    "welga ng drivers",
    "welga ng jeep",
    "welga ng piston",
    "piston de marikina",
    "welga",
    "strike",
    # LRT-2 specific disruptions
    "lrt-2 suspended",
    "lrt suspended",
    "lrt-2 suspension",
    "train suspended",
    "train suspension",
    "full suspension",
    "power failure",
    "service disruption",
    "train disruption",
    "lrt-2 disruption",
    "no lrt service",
    "lrt not operating",
    "lrt operations suspended",
    "provisionary service",
    "limited service",
    "partial suspension",
    "cubao-antipolo only",
    "antipolo-cubao only",
    "recto-santolan only",
    # Tagalog
    "suspendido ang operasyon",
    "tigil operasyon",
    "walang serbisyo ng lrt",
    "abisong lrt",
    "tigil ang lrt",
    "walang tren",
    "di tumatakbo ang lrt",
]

# ---------------------------------------------------------------------------
# GROUP 4 — TRAIN SERVICE DEGRADATION (friction: 0.5)
# Aligned to: "Code Yellow, Delayed Train" (0.5)
# ---------------------------------------------------------------------------
TRAIN_DEGRADATION_KEYWORDS = [
    "delayed train",
    "train delay",
    "lrt delay",
    "lrt-2 delay",
    "lrt delayed",
    "code yellow",
    "code yellow advisory",
    "degraded headway",
    "lrt-2 advisory",
    "lrt advisory",
    "train advisory",
    "service interruption",
    "gap in service",
    "extended headway",
    "delayed ang tren",
    "delayed ang lrt",
    "na-delay ang lrt",
]

# ---------------------------------------------------------------------------
# GROUP 5 — ARENA / MAJOR EVENTS (friction: 0.65)
# Aligned to: "Concert, Sports Event" (0.65)
# ---------------------------------------------------------------------------
ARENA_EVENT_KEYWORDS = [
    "concert",
    "sports event",
    "arena event",
    "major event",
    "smart araneta",
    "araneta coliseum",
    "big dome",
    "araneta",
    "philsports",
    "phil sports arena",
    "mall of asia arena",
    "moa arena",
    "festival mall arena",
    "filoil flying v",
    "filoil ecoil",
    "ust tiger dome",
    "jru",
    "game day",
    "uaap",
    "ncaa",
    "pba game",
    "fight night",
    "boxing event",
    "boxing match",
    "k-pop concert",
    "fans event",
]

# ---------------------------------------------------------------------------
# GROUP 6 — ACADEMIC CALENDAR EVENTS (friction: low, but high volume)
# Includes: exams, enrollment, graduation — affects ridership patterns
# ---------------------------------------------------------------------------
ACADEMIC_CALENDAR_KEYWORDS = [
    "enrollment",
    "enrolment",
    "enrollment period",
    "registration period",
    "pre-enrollment",
    "online enrollment",
    "early registration",
    "orientation",
    "freshmen orientation",
    "new student orientation",
    "freshmen",
    "midterms",
    "midterm examination",
    "midterm exams",
    "finals",
    "final examination",
    "final exams",
    "board exam",
    "entrance exam",
    "examination",
    "exam",
    "exam week",
    "university exam week",
    "quiz bee",
    "graduation",
    "commencement",
    "moving up",
    "recognition day",
    "convocation",
    "academic calendar",
    "school calendar",
    "collegiate calendar",
    "university calendar",
    "semester starts",
    "semester begins",
    "start of classes",
    "first day of classes",
    "pasukan",
    "back to school",
    "intramurals",
    "sportsfest",
    "cheerdance",
    "university week",
    "foundation day",
    "college day",
    "js prom",
    "prom night",
    "schoolyear",
    "school year",
    "academic year",
    "sem break",
    "semester break",
    "christmas break",
    "summer break",
    "holiday break",
    "walang klase bukas",
    "huling araw ng klase",
]

# ---------------------------------------------------------------------------
# Merged keyword sets for fast lookup
# ---------------------------------------------------------------------------
# "Strong" keywords — posting any of these is very likely a relevant event
STRONG_KEYWORDS = (
    CLASS_SUSPENSION_KEYWORDS
    + LGU_ADVISORY_KEYWORDS
    + TRANSPORT_DISRUPTION_KEYWORDS
    + TRAIN_DEGRADATION_KEYWORDS
    + ARENA_EVENT_KEYWORDS
)

# "Soft" keywords — need additional context to confirm relevance
SOFT_KEYWORDS = ACADEMIC_CALENDAR_KEYWORDS

# All keywords combined
ALL_KEYWORDS = STRONG_KEYWORDS + SOFT_KEYWORDS

# ---------------------------------------------------------------------------
# ACADEMIC-SPECIFIC (used by classify_post for academic pages)
# ---------------------------------------------------------------------------
ACADEMIC_KEYWORDS = (
    CLASS_SUSPENSION_KEYWORDS
    + ACADEMIC_CALENDAR_KEYWORDS
    + [
        "schedule change",
        "class schedule",
        "school reopening",
        "holiday",
        "holidays",
    ]
)

# ---------------------------------------------------------------------------
# LGU-SPECIFIC (used by classify_post for LGU/government pages)
# ---------------------------------------------------------------------------
LGU_KEYWORDS = (
    LGU_ADVISORY_KEYWORDS
    + CLASS_SUSPENSION_KEYWORDS
    + TRANSPORT_DISRUPTION_KEYWORDS
    + TRAIN_DEGRADATION_KEYWORDS
    + ARENA_EVENT_KEYWORDS
    + [
        "holiday",
        "holidays",
        "long weekend",
    ]
)

# ---------------------------------------------------------------------------
# Context keywords — used for disambiguation
# ---------------------------------------------------------------------------
ACADEMIC_CONTEXT_KEYWORDS = [
    "class",
    "classes",
    "school",
    "university",
    "college",
    "campus",
    "student",
    "students",
    "academic",
    "faculty",
    "teacher",
    "professor",
    "pasok",
    "klase",
    "suspendido",
    "walang pasok",
    "enrollment",
    "exam",
    "graduation",
]


def _casefold_match(keyword_list: list[str], text: str) -> bool:
    """Return True if any keyword matches the casefolded text."""
    casefolded = normalize_unicode_text(text).casefold()
    return any(kw.casefold() in casefolded for kw in keyword_list)


def classify_post(text: str) -> str | None:
    """
    Pre-classify a post using keyword matching (before LLM call).
    Uses .casefold() for case-insensitive matching across:
      - ALL CAPS:   NO CLASSES, WALANG PASOK, SUSPENDED
      - Title Case: No Classes, Walang Pasok
      - lowercase:  no classes, walang pasok
      - Mixed:      NO Classes, WALANG Pasok

    Returns: 'academic', 'lgu', 'transport', 'arena', or None.
    """
    if not text or not text.strip():
        return None

    # Normalize decorative Unicode fonts (bold, italic, script, etc.) to plain ASCII
    lowered = normalize_unicode_text(text).casefold()

    academic_match = any(kw.casefold() in lowered for kw in ACADEMIC_KEYWORDS)
    lgu_match = any(kw.casefold() in lowered for kw in LGU_KEYWORDS)
    transport_match = any(kw.casefold() in lowered for kw in TRANSPORT_DISRUPTION_KEYWORDS)
    arena_match = any(kw.casefold() in lowered for kw in ARENA_EVENT_KEYWORDS)
    train_match = any(kw.casefold() in lowered for kw in TRAIN_DEGRADATION_KEYWORDS)
    academic_context = any(kw.casefold() in lowered for kw in ACADEMIC_CONTEXT_KEYWORDS)

    # Transport/train disruptions and arena events are always relevant
    if transport_match or train_match:
        return "lgu"

    if arena_match:
        return "lgu"

    if academic_match and academic_context:
        return "academic"

    if lgu_match and not academic_match:
        return "lgu"

    if academic_match and lgu_match and academic_context:
        return "academic"

    return None
