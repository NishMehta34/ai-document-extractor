"""FastAPI app: the web page plus a JSON API.

Run locally:  uvicorn app.main:app --reload
Then open:    http://localhost:8000      (web app)
              http://localhost:8000/docs (API docs)
"""

import logging
import secrets
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from app import __version__
from app.config import Settings, settings as default_settings
from app.export import to_csv, to_xlsx
from app.extractor import extract_from_pdf, revalidate
from app.llm import ChatModel, OpenAICompatibleLLM
from app.pdf_text import DocumentError
from app.ratelimit import RateLimiter
from app.schemas import DOC_TYPES
from app.storage import ExtractionRecord, Store

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


class Correction(BaseModel):
    data: dict


def create_app(
    settings: Settings = default_settings,
    store: Store | None = None,
    llm: ChatModel | None = None,
) -> FastAPI:
    app = FastAPI(title="AI Document Extractor", version=__version__,
                  description="Extract checked, structured data from invoices, "
                              "purchase orders and receipts.")
    store = store or Store(settings.database_url)
    llm = llm or OpenAICompatibleLLM()
    limiter = RateLimiter(settings.rate_limit_per_hour)

    def require_code(code: str | None) -> None:
        expected = settings.demo_access_code
        if expected and not secrets.compare_digest(code or "", expected):
            raise HTTPException(401, "A valid access code is required.")

    def client_id(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:  # behind a hosting proxy
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def get_or_404(record_id: str) -> ExtractionRecord:
        record = store.get(record_id)
        if not record:
            raise HTTPException(404, "Extraction not found.")
        return record

    def export_response(records: list[ExtractionRecord], fmt: str, name: str) -> Response:
        if fmt == "xlsx":
            return Response(
                to_xlsx(records),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'})
        return Response(to_csv(records), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/api/config")
    def config():
        return {
            "doc_types": [{"value": k, "label": v[1]} for k, v in DOC_TYPES.items()],
            "access_code_required": bool(settings.demo_access_code),
            "review_threshold": settings.review_threshold,
            "max_upload_mb": settings.max_upload_mb,
            "max_pages": settings.max_pages,
        }

    @app.post("/api/extract", response_model=ExtractionRecord)
    async def extract(
        request: Request,
        file: UploadFile = File(...),
        doc_type: str = Form("invoice"),
        x_access_code: str | None = Header(None),
    ):
        require_code(x_access_code)
        if doc_type not in DOC_TYPES:
            raise HTTPException(422, f"doc_type must be one of {list(DOC_TYPES)}.")
        allowed, retry_after = limiter.check(client_id(request))
        if not allowed:
            raise HTTPException(429, f"Rate limit reached. Try again in {retry_after // 60 + 1} minutes.",
                                headers={"Retry-After": str(retry_after)})

        content = await file.read(int(settings.max_upload_mb * 1024 * 1024) + 1)
        if len(content) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"File is larger than {settings.max_upload_mb:g} MB.")

        try:
            result = extract_from_pdf(content, doc_type, llm, settings)
        except DocumentError as exc:
            raise HTTPException(422, str(exc))
        except Exception as exc:
            log.exception("Extraction failed")
            raise HTTPException(502, f"The AI model could not process this document: {exc}")

        payload = result.to_dict()
        record = ExtractionRecord(
            filename=file.filename or "document.pdf", doc_type=doc_type,
            status="needs_review" if result.needs_review else "auto_approved",
            model=result.model, pages=result.pages,
            data=payload["data"], fields=payload["fields"], issues=payload["issues"],
        )
        return store.save(record)

    @app.get("/api/extractions", response_model=list[ExtractionRecord])
    def list_extractions(status: str | None = None, limit: int = Query(50, le=500)):
        return store.list(limit=limit, status=status)

    @app.get("/api/extractions/{record_id}", response_model=ExtractionRecord)
    def get_extraction(record_id: str):
        return get_or_404(record_id)

    @app.patch("/api/extractions/{record_id}", response_model=ExtractionRecord)
    def correct_extraction(record_id: str, correction: Correction,
                           x_access_code: str | None = Header(None)):
        """Save a reviewer's corrections; rules are re-run on the corrected data."""
        require_code(x_access_code)
        record = get_or_404(record_id)
        try:
            data, issues, fields = revalidate(record.doc_type, correction.data, record.fields)
        except ValidationError as exc:
            raise HTTPException(422, f"Invalid values: {exc.errors()[0]['msg']}")
        return store.update(record_id, data=data, fields=fields,
                            issues=[i.to_dict() for i in issues], status="reviewed")

    @app.delete("/api/extractions/{record_id}", status_code=204)
    def delete_extraction(record_id: str, x_access_code: str | None = Header(None)):
        require_code(x_access_code)
        if not store.delete(record_id):
            raise HTTPException(404, "Extraction not found.")

    @app.get("/api/extractions/{record_id}/export")
    def export_one(record_id: str, format: str = Query("csv", pattern="^(csv|xlsx)$")):
        record = get_or_404(record_id)
        return export_response([record], format, f"{record.doc_type}-{record.id}")

    @app.get("/api/export")
    def export_all(format: str = Query("csv", pattern="^(csv|xlsx)$"), status: str | None = None):
        return export_response(store.list(limit=500, status=status), format, "extractions")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


# Created lazily so importing this module in tests doesn't touch the real database.
def __getattr__(name: str):
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
