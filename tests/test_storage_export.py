import csv
import io
import os

import pytest

from app.export import to_csv, to_xlsx
from app.extractor import extract_from_pdf
from app.ratelimit import RateLimiter
from app.storage import ExtractionRecord, Store
from tests.conftest import FakeLLM, sample_pdf, sample_truth


def make_record(n: int = 1, status: str = "auto_approved") -> ExtractionRecord:
    result = extract_from_pdf(sample_pdf(n), "invoice", FakeLLM(sample_truth(n))).to_dict()
    return ExtractionRecord(filename=f"invoice_{n:02d}.pdf", doc_type="invoice", status=status,
                            data=result["data"], fields=result["fields"], issues=result["issues"])


@pytest.fixture(params=["sqlite", "postgres"])
def store(request, tmp_path):
    if request.param == "sqlite":
        return Store(f"sqlite:///{tmp_path}/test.db")
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL not set")
    s = Store(url)
    s._execute("DELETE FROM extractions")
    return s


def test_save_get_list_update_delete(store):
    a = store.save(make_record(1))
    b = store.save(make_record(2, status="needs_review"))

    assert store.get(a.id).data["total"] == sample_truth(1)["total"]
    assert {r.id for r in store.list()} == {a.id, b.id}
    assert [r.id for r in store.list(status="needs_review")] == [b.id]

    updated = store.update(b.id, data={"total": 1.0}, fields={}, issues=[], status="reviewed")
    assert updated.status == "reviewed" and updated.data == {"total": 1.0}

    assert store.delete(a.id) is True
    assert store.get(a.id) is None
    assert store.delete(a.id) is False


def test_bad_database_url():
    with pytest.raises(ValueError):
        Store("mysql://nope")


def test_csv_has_one_row_per_line_item():
    records = [make_record(1), make_record(2)]
    rows = list(csv.DictReader(io.StringIO(to_csv(records))))
    expected = len(sample_truth(1)["line_items"]) + len(sample_truth(2)["line_items"])
    assert len(rows) == expected
    assert rows[0]["invoice_number"] == sample_truth(1)["invoice_number"]
    assert rows[0]["item_amount"] != ""


def test_xlsx_has_two_sheets():
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(to_xlsx([make_record(1)])))
    assert wb.sheetnames == ["Documents", "Line items"]
    assert wb["Documents"]["A2"].value  # id
    assert wb["Line items"].max_row == len(sample_truth(1)["line_items"]) + 1


def test_rate_limiter():
    now = [0.0]
    limiter = RateLimiter(2, window_seconds=60, clock=lambda: now[0])
    assert limiter.check("a")[0] and limiter.check("a")[0]
    allowed, retry = limiter.check("a")
    assert not allowed and retry > 0
    assert limiter.check("b")[0]  # other clients unaffected
    now[0] = 61
    assert limiter.check("a")[0]  # window passed
