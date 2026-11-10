"""Matching the KYC name against the bank's account-holder name (plan M2, M3), and masking that name for display.

Both names are normalised first: case, punctuation, spacing and honorifics are ignored, and so is word order
("Kumar Arun" matches "Arun Kumar"). An initial matches a whole word that starts with it ("A Kumar" matches
"Arun Kumar"). Words that differ by a typo still count, scaled by how close they are. A score below the threshold
is a mismatch, and the link is refused.
"""

import re
import unicodedata
from difflib import SequenceMatcher

THRESHOLD = 0.8
_HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "shri", "sri", "smt", "kumari", "km", "prof"}
_WORD_SIMILARITY = 0.8


def words(name: str) -> list[str]:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    cleaned = re.sub(r"[^a-z ]", " ", ascii_name.lower().replace(".", " "))
    return [w for w in cleaned.split() if w not in _HONORIFICS]


def _word_score(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if len(a) == 1 or len(b) == 1:
        return 0.9 if a[0] == b[0] else 0.0  # an initial
    ratio = SequenceMatcher(None, a, b).ratio()
    return ratio if ratio >= _WORD_SIMILARITY else 0.0


def match_score(kyc_name: str, bank_name: str) -> float:
    """0 to 1. Pairs each word with its best unused match in the other name; unpaired words count against it."""
    left, right = words(kyc_name), words(bank_name)
    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    unused = list(right)
    total = 0.0
    for word in left:
        best = max(unused, key=lambda candidate: _word_score(word, candidate))
        score = _word_score(word, best)
        if score > 0:
            unused.remove(best)
            total += score
    return round(2 * total / (len(left) + len(right)), 3)


def names_match(kyc_name: str, bank_name: str) -> bool:
    return match_score(kyc_name, bank_name) >= THRESHOLD


def mask_name(name: str) -> str:
    """'Test User' -> 'T••• U•••'. Enough for the customer to recognise their account, not enough to harvest."""
    return " ".join(f"{w[0].upper()}•••" for w in name.split() if w) or "•••"


def mask_upi_id(upi_id: str) -> str:
    """'test1@okmockbank' -> 'te•••1@okmockbank'."""
    local, _, handle = upi_id.partition("@")
    shown = f"{local[:2]}•••{local[-1]}" if len(local) > 3 else f"{local[:1]}•••"
    return f"{shown}@{handle}"
