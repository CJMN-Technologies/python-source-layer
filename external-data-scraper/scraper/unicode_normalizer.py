"""
unicode_normalizer.py — Normalize decorative Unicode text to plain ASCII.

Facebook posts frequently use Mathematical Bold, Italic, Script, Double-Struck,
Circled, and Fullwidth Unicode characters for stylistic effect. These characters
look identical to normal letters but have completely different code points, so
`.lower()` / `.casefold()` keyword matching silently fails.

This module converts all such decorative characters back to their plain ASCII
equivalents so that keyword matching works regardless of font style.

Examples:
    𝗔𝗰𝗮𝗱𝗲𝗺𝗶𝗰  →  Academic   (Math Bold)
    𝘊𝘢𝘭𝘦𝘯𝘥𝘢𝘳  →  Calendar   (Math Bold Italic)
    𝓐𝓬𝓪𝓭𝓮𝓶𝓲𝓬  →  Academic   (Math Script Bold)
    𝔸𝕔𝕒𝕕𝕖𝕞𝕚𝕔  →  Academic   (Double-Struck)
    Ⓐⓒⓐⓓⓔⓜⓘⓒ  →  Academic   (Circled)
    Ａｃａｄｅｍｉｃ  →  Academic   (Fullwidth)
"""

import unicodedata
import re

# ---------------------------------------------------------------------------
# Manual mapping table for Unicode ranges that NFKD doesn't fully decompose.
# Each entry: (start_codepoint, plain_ascii_start_char, length)
# ---------------------------------------------------------------------------
_UNICODE_RANGES = [
    # Mathematical Bold Capital A-Z
    (0x1D400, 'A', 26),
    # Mathematical Bold Small a-z
    (0x1D41A, 'a', 26),
    # Mathematical Italic Capital A-Z
    (0x1D434, 'A', 26),
    # Mathematical Italic Small a-z
    (0x1D44E, 'a', 26),
    # Mathematical Bold Italic Capital A-Z
    (0x1D468, 'A', 26),
    # Mathematical Bold Italic Small a-z
    (0x1D482, 'a', 26),
    # Mathematical Script Capital A-Z
    (0x1D49C, 'A', 26),
    # Mathematical Script Small a-z
    (0x1D4B6, 'a', 26),
    # Mathematical Script Bold Capital A-Z
    (0x1D4D0, 'A', 26),
    # Mathematical Script Bold Small a-z
    (0x1D4EA, 'a', 26),
    # Mathematical Fraktur Capital A-Z
    (0x1D504, 'A', 26),
    # Mathematical Fraktur Small a-z
    (0x1D51E, 'a', 26),
    # Mathematical Double-Struck Capital A-Z
    (0x1D538, 'A', 26),
    # Mathematical Double-Struck Small a-z
    (0x1D552, 'a', 26),
    # Mathematical Bold Fraktur Capital A-Z
    (0x1D56C, 'A', 26),
    # Mathematical Bold Fraktur Small a-z
    (0x1D586, 'a', 26),
    # Mathematical Sans-Serif Capital A-Z
    (0x1D5A0, 'A', 26),
    # Mathematical Sans-Serif Small a-z
    (0x1D5BA, 'a', 26),
    # Mathematical Sans-Serif Bold Capital A-Z
    (0x1D5D4, 'A', 26),
    # Mathematical Sans-Serif Bold Small a-z
    (0x1D5EE, 'a', 26),
    # Mathematical Sans-Serif Italic Capital A-Z
    (0x1D608, 'A', 26),
    # Mathematical Sans-Serif Italic Small a-z
    (0x1D622, 'a', 26),
    # Mathematical Sans-Serif Bold Italic Capital A-Z
    (0x1D63C, 'A', 26),
    # Mathematical Sans-Serif Bold Italic Small a-z
    (0x1D656, 'a', 26),
    # Mathematical Monospace Capital A-Z
    (0x1D670, 'A', 26),
    # Mathematical Monospace Small a-z
    (0x1D68A, 'a', 26),
    # Mathematical Bold Digit 0-9
    (0x1D7CE, '0', 10),
    # Mathematical Double-Struck Digit 0-9
    (0x1D7D8, '0', 10),
    # Mathematical Sans-Serif Digit 0-9
    (0x1D7E2, '0', 10),
    # Mathematical Sans-Serif Bold Digit 0-9
    (0x1D7EC, '0', 10),
    # Mathematical Monospace Digit 0-9
    (0x1D7F6, '0', 10),
    # Fullwidth Latin Capital A-Z
    (0xFF21, 'A', 26),
    # Fullwidth Latin Small a-z
    (0xFF41, 'a', 26),
    # Fullwidth Digit 0-9
    (0xFF10, '0', 10),
    # Circled Latin Capital A-Z (Ⓐ-Ⓩ)
    (0x24B6, 'A', 26),
    # Circled Latin Small a-z (ⓐ-ⓩ)
    (0x24D0, 'a', 26),
]

# Build a fast lookup dict: codepoint -> plain ASCII char
_CHAR_MAP: dict[int, str] = {}
for _start, _base, _length in _UNICODE_RANGES:
    for _offset in range(_length):
        _CHAR_MAP[_start + _offset] = chr(ord(_base) + _offset)


def normalize_unicode_text(text: str) -> str:
    """
    Normalize decorative Unicode characters to plain ASCII equivalents.

    1. Apply NFKD decomposition (handles many fullwidth/compatibility chars).
    2. Apply manual mapping for Mathematical Alphanumeric Symbols that NFKD
       doesn't decompose (Bold, Italic, Script, Double-Struck, etc.).
    3. Strip combining marks left over from NFKD decomposition.

    The result is plain ASCII text suitable for case-insensitive keyword matching.
    """
    if not text:
        return text

    # Step 1: NFKD decomposition (handles fullwidth, some compatibility forms)
    text = unicodedata.normalize("NFKD", text)

    # Step 2: Manual mapping for mathematical alphanumeric symbols
    chars = []
    for ch in text:
        cp = ord(ch)
        if cp in _CHAR_MAP:
            chars.append(_CHAR_MAP[cp])
        elif unicodedata.category(ch) == "Mn":
            # Skip combining marks (left over from NFKD decomposition)
            continue
        else:
            chars.append(ch)

    return "".join(chars)
