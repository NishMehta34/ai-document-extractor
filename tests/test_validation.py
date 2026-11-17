from app.validation import validate


def test_correct_invoice_has_no_issues(truth):
    assert validate("invoice", truth) == []


def test_missing_required_field(truth):
    truth["total"] = None
    messages = [i.message for i in validate("invoice", truth)]
    assert any("'total' is missing" in m for m in messages)


def test_line_items_not_adding_up(truth):
    truth["line_items"][0]["amount"] += 100
    issues = validate("invoice", truth)
    fields = {f for i in issues for f in i.fields}
    assert "line_items" in fields and "subtotal" in fields


def test_totals_not_adding_up(truth):
    truth["total"] += 5
    issues = validate("invoice", truth)
    assert any(set(i.fields) == {"subtotal", "tax", "total"} for i in issues)


def test_due_date_before_invoice_date(truth):
    truth["due_date"] = "2020-01-01"
    assert any("before" in i.message for i in validate("invoice", truth))


def test_bad_date_and_currency(truth):
    truth["invoice_date"] = "27/02/2026"
    truth["currency"] = "$"
    issues = validate("invoice", truth)
    assert any("not a valid" in i.message for i in issues)
    assert any(i.severity == "warning" and i.fields == ["currency"] for i in issues)


def test_rounding_is_tolerated(truth):
    truth["total"] = round(truth["total"] + 0.01, 2)
    assert validate("invoice", truth) == []
