def classify_rainfall(mm: float) -> str:
    if mm > 30:
        return "Red"
    elif mm >= 15:
        return "Orange"
    elif mm >= 7.5:
        return "Yellow"
    else:
        return "None"