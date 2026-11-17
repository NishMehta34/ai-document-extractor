import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.storage import Store  # noqa: E402
from tests.conftest import FakeLLM, sample_pdf, sample_truth  # noqa: E402


def make_client(tmp_path, llm=None, **settings_overrides):
    settings = Settings(**settings_overrides)
    store = Store(f"sqlite:///{tmp_path}/api.db")
    return TestClient(create_app(settings, store, llm or FakeLLM(sample_truth(1))))


def upload(client, pdf=None, headers=None):
    return client.post("/api/extract", files={"file": ("inv.pdf", pdf or sample_pdf(1), "application/pdf")},
                       data={"doc_type": "invoice"}, headers=headers or {})


def test_page_and_config(tmp_path):
    client = make_client(tmp_path)
    assert "AI Document Extractor" in client.get("/").text
    config = client.get("/api/config").json()
    assert {"value": "invoice", "label": "Invoice"} in config["doc_types"]


def test_extract_list_get_export(tmp_path):
    client = make_client(tmp_path)
    record = upload(client).json()
    assert record["status"] == "auto_approved"
    assert record["data"]["total"] == sample_truth(1)["total"]

    assert client.get("/api/extractions").json()[0]["id"] == record["id"]
    assert client.get(f"/api/extractions/{record['id']}").status_code == 200

    csv_response = client.get(f"/api/extractions/{record['id']}/export?format=csv")
    assert csv_response.headers["content-type"].startswith("text/csv")
    xlsx = client.get("/api/export?format=xlsx")
    assert xlsx.content[:2] == b"PK"  # xlsx files are zip archives


def test_review_flow(tmp_path):
    def wrong_total(d):
        d["total"] = round(d["total"] + 50, 2)

    client = make_client(tmp_path, llm=FakeLLM(sample_truth(1), mutate=wrong_total))
    record = upload(client).json()
    assert record["status"] == "needs_review"

    corrected = dict(record["data"], total=sample_truth(1)["total"])
    reviewed = client.patch(f"/api/extractions/{record['id']}", json={"data": corrected}).json()
    assert reviewed["status"] == "reviewed"
    assert reviewed["issues"] == []


def test_bad_uploads(tmp_path):
    client = make_client(tmp_path, max_upload_mb=0.001)
    assert upload(client, pdf=b"hello").status_code == 422
    assert upload(client).status_code == 413


def test_access_code_and_rate_limit(tmp_path):
    client = make_client(tmp_path, demo_access_code="secret", rate_limit_per_hour=1)
    assert upload(client).status_code == 401
    assert upload(client, headers={"X-Access-Code": "secret"}).status_code == 200
    assert upload(client, headers={"X-Access-Code": "secret"}).status_code == 429


def test_model_failure_returns_502(tmp_path):
    class Broken:
        model = "broken"

        def chat(self, system, user):
            raise ConnectionError("model offline")

    client = make_client(tmp_path, llm=Broken())
    response = upload(client)
    assert response.status_code == 502
    assert "model offline" in response.json()["detail"]


def test_missing_record_404(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/api/extractions/nope").status_code == 404
    assert client.delete("/api/extractions/nope").status_code == 404
