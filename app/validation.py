"""Business-rule checks on extracted data.

These catch mistakes the model can't see itself: line items that don't add
up, a due date before the invoice date, a missing total. Each issue names
the fields involved so they can be flagged for review.
"""

from dataclasses import asdict, dataclass, field
from datetime import date

from app.schemas import DOC_TYPES

TOLERANCE = 0.02  # allow 2 cents of rounding difference


@dataclass
class Issue:
    message: str
    fields: list[str] = field(default_factory=list)
    severity: str = "error"  # "error" or "warning"

    def to_dict(self) -> dict:
        return asdict(self)


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(TOLERANCE, abs(b) * 0.001)


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def validate(doc_type: str, data: dict) -> list[Issue]:
    _, _, required = DOC_TYPES[doc_type]
    issues: list[Issue] = []

    for name in required:
        if data.get(name) in (None, ""):
            issues.append(Issue(f"Required field '{name}' is missing.", [name]))

    # Dates must be real YYYY-MM-DD dates.
    for name, value in data.items():
        if "date" in name and value and _parse_date(value) is None:
            issues.append(Issue(f"'{name}' is not a valid YYYY-MM-DD date: {value}", [name]))

    # Date order.
    for start, end in [("invoice_date", "due_date"), ("order_date", "delivery_date")]:
        a, b = _parse_date(data.get(start)), _parse_date(data.get(end))
        if a and b and b < a:
            issues.append(Issue(f"'{end}' is before '{start}'.", [start, end]))

    currency = data.get("currency")
    if currency and not (len(currency) == 3 and currency.isalpha() and currency.isupper()):
        issues.append(Issue(f"Currency should be a 3-letter code like USD, not '{currency}'.",
                            ["currency"], "warning"))

    # Line items: quantity x unit price = amount.
    items = data.get("line_items") or []
    for i, item in enumerate(items, start=1):
        q, p, amt = item.get("quantity"), item.get("unit_price"), item.get("amount")
        if None not in (q, p, amt) and not _close(q * p, amt):
            issues.append(Issue(
                f"Line {i}: {q} × {p} = {q * p:.2f}, but amount is {amt:.2f}.", ["line_items"]))

    # Line items add up to the subtotal (or total if there's no subtotal).
    amounts = [it.get("amount") for it in items]
    subtotal, tax, total = data.get("subtotal"), data.get("tax"), data.get("total")
    if items and all(a is not None for a in amounts):
        target_name = "subtotal" if subtotal is not None else "total"
        target = subtotal if subtotal is not None else total
        if target is not None and not _close(sum(amounts), target):
            issues.append(Issue(
                f"Line items add up to {sum(amounts):.2f}, but {target_name} is {target:.2f}.",
                ["line_items", target_name]))

    # Subtotal + tax = total.
    if None not in (subtotal, tax, total) and not _close(subtotal + tax, total):
        issues.append(Issue(
            f"Subtotal {subtotal:.2f} + tax {tax:.2f} = {subtotal + tax:.2f}, "
            f"but total is {total:.2f}.", ["subtotal", "tax", "total"]))

    return issues
