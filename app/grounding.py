"""Check that an extracted value actually appears in the document text.

This catches the most dangerous model error: a plausible value that isn't
in the document at all (a hallucination). It's rule-based, so it's free
and predictable.
"""

import re
from datetime import date

_NUMBER = re.compile(r"\d[\d.,]*\d|\d")
_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def numbers_in(text: str) -> set[float]:
    """Every number in the text, read both the UK/US way (1,234.50)
    and the European way (1.234,50), since we don't know which the document uses."""
    found = set()
    for token in _NUMBER.findall(text):
        for candidate in (token.replace(",", ""), token.replace(".", "").replace(",", ".")):
            try:
                found.add(round(float(candidate), 2))
            except ValueError:
                pass
    return found


def date_variants(iso: str) -> list[str]:
    """All the ways a date might be written in a document."""
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return [iso]
    month, mon = _MONTHS[d.month - 1], _MONTHS[d.month - 1][:3]
    return [
        d.isoformat(),
        f"{d.day:02d}/{d.month:02d}/{d.year}", f"{d.month:02d}/{d.day:02d}/{d.year}",
        f"{d.day}/{d.month}/{d.year}", f"{d.month}/{d.day}/{d.year}",
        f"{d.day:02d}-{d.month:02d}-{d.year}", f"{d.day:02d}.{d.month:02d}.{d.year}",
        f"{d.day} {month} {d.year}", f"{month} {d.day}, {d.year}",
        f"{d.day} {mon} {d.year}", f"{mon} {d.day}, {d.year}", f"{d.day}-{mon}-{d.year}",
    ]


def is_grounded(value, text: str, field_name: str = "") -> bool:
    """True if the value can be found in the text."""
    if value is None:
        return True  # nothing claimed, nothing to check
    if isinstance(value, (int, float)):
        return round(float(value), 2) in numbers_in(text)

    value = str(value)
    norm_text = _norm(text)
    if "date" in field_name:
        return any(_norm(v) in norm_text for v in date_variants(value))
    if field_name == "currency":
        symbols = {"USD": ["$", "usd"], "GBP": ["£", "gbp"], "EUR": ["€", "eur"],
                   "INR": ["₹", "inr", "rs"]}
        return any(s in norm_text for s in symbols.get(value.upper(), [value.lower()]))

    norm_value = _norm(value)
    if norm_value in norm_text:
        return True
    # Long values (addresses, names split across lines): most words present.
    words = [w for w in re.findall(r"\w+", norm_value) if len(w) > 1]
    if len(words) >= 3:
        present = sum(w in norm_text for w in words)
        return present / len(words) >= 0.7
    return False
