ACADEMIC_KEYWORDS = [
    "no classes",
    "class suspension",
    "classes suspended",
    "suspended classes",
    "school suspension",
    "resumption of classes",
    "cancelled classes",
    "class suspended",
    "schedule change",
    "class schedule",
    "online classes",
    "class resumption",
    "class postponement",
    "class cancellation",
    "school closed",
    "university closed",
    "school reopening",
    "resumption of classes",
    "class postponement",

    # Filipino / Taglish
    "walang pasok",
    "suspendido ang klase",
    "suspendido ang pasok",
    "bukas ang pasok",
    "petsa ng pasok",
    "huling araw ng klase",
    "pasukan",
    "class schedule",
    "class suspension",
    "school suspension",
]

LGU_KEYWORDS = [
    "class suspension",
    "classes suspended",
    "suspended classes",
    "school suspension",
    "resumption of classes",
    "school closed",
    "class postponement",
    "class cancellation",
    "road closure",
    "school closure",
    "weather advisory",
    "storm signal",
    "signal number",
    "monsoon",
    "habagat",
    "bagyo",
    "baha",
    "suspendido ang trabaho",
    "estado ng kalamidad",
    "pampublikong pahinga",
]


def classify_post(text: str) -> str | None:
    text_lower = text.lower()

    academic_match = any(kw in text_lower for kw in ACADEMIC_KEYWORDS)
    lgu_match = any(kw in text_lower for kw in LGU_KEYWORDS)
    academic_context = any(ctx in text_lower for ctx in ["class", "classes", "school", "pasok", "suspendido", "walang pasok"])

    if academic_match and academic_context:
        return "academic"
    if lgu_match and not academic_match:
        return "lgu"
    if academic_match and lgu_match and academic_context:
        return "academic"

    return None