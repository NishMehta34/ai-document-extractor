import pytest

from app.extractor import extract_from_pdf, revalidate
from app.pdf_text import DocumentError, extract_text
from tests.conftest import FakeLLM, sample_pdf


def test_pdf_text_extraction():
    text, pages = extract_text(sample_pdf(1))
    assert pages == 1
    assert "NW-20260001" in text


@pytest.mark.parametrize("data, message", [
    (b"hello", "not a PDF"),
])
def test_pdf_errors(data, message):
    with pytest.raises(DocumentError, match=message):
        extract_text(data)


def test_page_limit():
    with pytest.raises(DocumentError, match="limit is 0"):
        extract_text(sample_pdf(1), max_pages=0)


def test_correct_extraction_is_auto_approved(truth):
    result = extract_from_pdf(sample_pdf(1), "invoice", FakeLLM(truth))
    assert result.data["total"] == truth["total"]
    assert result.issues == []
    assert not result.needs_review
    assert all(f.grounded for f in result.fields.values())
    assert result.fields["total"].confidence == 0.95


def test_hallucinated_value_is_flagged_even_if_model_is_confident(truth):
    def invent_customer(d):
        d["customer_name"] = "Totally Made Up Corp"

    result = extract_from_pdf(sample_pdf(1), "invoice", FakeLLM(truth, 0.99, invent_customer))
    field = result.fields["customer_name"]
    assert field.confidence <= 0.4
    assert "not found in document" in field.flags
    assert result.needs_review


def test_wrong_total_is_flagged_by_rules(truth):
    def wrong_total(d):
        d["total"] = round(d["total"] + 50, 2)

    result = extract_from_pdf(sample_pdf(1), "invoice", FakeLLM(truth, 0.99, wrong_total))
    assert "fails a validation check" in result.fields["total"].flags
    assert result.fields["total"].confidence <= 0.3
    assert result.needs_review


def test_low_model_confidence_is_flagged(truth):
    result = extract_from_pdf(sample_pdf(1), "invoice", FakeLLM(truth, confidence=0.5))
    assert "low model confidence" in result.fields["vendor_name"].flags
    assert result.needs_review


def test_formatted_numbers_and_missing_wrapper_are_tolerated(truth):
    def format_like_document(d):
        d["total"] = "$2,954.59"

    llm = FakeLLM(truth, mutate=format_like_document, wrap=False)
    result = extract_from_pdf(sample_pdf(1), "invoice", llm)
    assert result.data["total"] == 2954.59


def test_one_broken_field_does_not_lose_the_rest(truth):
    def break_items(d):
        d["line_items"] = "see attached"

    result = extract_from_pdf(sample_pdf(1), "invoice", FakeLLM(truth, mutate=break_items))
    assert result.data["vendor_name"] == truth["vendor_name"]
    assert result.data["line_items"] == []


def test_unknown_doc_type(truth):
    with pytest.raises(ValueError, match="Unknown document type"):
        extract_from_pdf(sample_pdf(1), "passport", FakeLLM(truth))


def test_revalidate_after_human_correction(truth):
    def wrong_total(d):
        d["total"] = round(d["total"] + 50, 2)

    result = extract_from_pdf(sample_pdf(1), "invoice", FakeLLM(truth, mutate=wrong_total))
    previous = result.to_dict()["fields"]
    corrected = dict(result.data, total=truth["total"])
    data, issues, fields = revalidate("invoice", corrected, previous)
    assert issues == []
    assert fields["total"]["confidence"] == 1.0
    assert "corrected by reviewer" in fields["total"]["flags"]
