import pytest

from app.grounding import date_variants, is_grounded, numbers_in
from app.schemas import Invoice, clean_number


@pytest.mark.parametrize("raw, expected", [
    ("$1,234.50", 1234.5), ("€1.234,50", 1234.5), ("£0.00", 0.0),
    ("(12.00)", -12.0), ("", None), (None, None), (99, 99), ("1 500", 1500.0),
])
def test_clean_number(raw, expected):
    assert clean_number(raw) == expected


def test_invoice_model_cleans_values():
    inv = Invoice.model_validate({"invoice_number": 1042, "total": "$2,954.59",
                                  "line_items": [{"amount": "€10,50", "quantity": "2"}]})
    assert inv.invoice_number == "1042"
    assert inv.total == 2954.59
    assert inv.line_items[0].amount == 10.5


def test_numbers_in_reads_both_formats():
    nums = numbers_in("Total €1.792,57 and $2,954.59")
    assert 1792.57 in nums and 2954.59 in nums


def test_date_grounding_matches_document_formats():
    assert "February 27, 2026" in date_variants("2026-02-27")
    assert is_grounded("2026-05-23", "Date: 23/05/2026", "invoice_date")
    assert not is_grounded("2026-05-24", "Date: 23/05/2026", "invoice_date")


def test_hallucinated_values_are_not_grounded():
    text = "Invoice No: INV-1 Vendor: Northwind Analytics Ltd Total £100.00"
    assert is_grounded("Northwind Analytics Ltd", text, "vendor_name")
    assert not is_grounded("Southwind Analytics", text, "vendor_name")
    assert is_grounded(100.0, text, "total")
    assert not is_grounded(110.0, text, "total")
    assert is_grounded("GBP", text, "currency")
    assert is_grounded(None, text, "due_date")  # nothing claimed
