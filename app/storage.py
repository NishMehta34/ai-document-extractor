"""Save extraction results to SQLite (default) or PostgreSQL.

The same small SQL works on both; only the driver and the placeholder
style differ. Structured results are stored as JSON text, which keeps
the schema simple while document types evolve.

Uploaded PDFs are never stored, only the extracted data.
"""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

SCHEMA = """
CREATE TABLE IF NOT EXISTS extractions (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    filename    TEXT NOT NULL,
    doc_type    TEXT NOT NULL,
    status      TEXT NOT NULL,
    model       TEXT,
    pages       INTEGER,
    data        TEXT NOT NULL,
    fields      TEXT NOT NULL,
    issues      TEXT NOT NULL
)
"""

STATUSES = ("needs_review", "auto_approved", "reviewed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ExtractionRecord(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    filename: str
    doc_type: str
    status: str
    model: str = ""
    pages: int = 0
    data: dict
    fields: dict
    issues: list


_JSON_COLUMNS = ("data", "fields", "issues")
_COLUMNS = list(ExtractionRecord.model_fields)


class Store:
    def __init__(self, url: str):
        self.url = url
        if url.startswith("sqlite:///"):
            self.kind, self.mark = "sqlite", "?"
            self.path = url.removeprefix("sqlite:///")
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        elif url.startswith(("postgresql://", "postgres://")):
            self.kind, self.mark = "postgres", "%s"
        else:
            raise ValueError("DATABASE_URL must start with sqlite:/// or postgresql://")
        self.init()

    @contextmanager
    def _conn(self):
        """Open a connection per operation: simple, and safe across threads."""
        if self.kind == "sqlite":
            conn = sqlite3.connect(self.path)
        else:
            import psycopg  # only needed when DATABASE_URL is PostgreSQL

            conn = psycopg.connect(self.url)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _execute(self, sql: str, params: tuple = (), fetch: bool = False):
        sql = sql.replace("?", self.mark)
        with self._conn() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return cur.fetchall() if fetch else None

    def init(self) -> None:
        self._execute(SCHEMA)

    def _row_to_record(self, row) -> ExtractionRecord:
        values = dict(zip(_COLUMNS, row))
        for col in _JSON_COLUMNS:
            values[col] = json.loads(values[col])
        return ExtractionRecord(**values)

    def save(self, record: ExtractionRecord) -> ExtractionRecord:
        values = record.model_dump()
        params = tuple(json.dumps(values[c]) if c in _JSON_COLUMNS else values[c] for c in _COLUMNS)
        placeholders = ", ".join("?" for _ in _COLUMNS)
        self._execute(f"INSERT INTO extractions ({', '.join(_COLUMNS)}) VALUES ({placeholders})", params)
        return record

    def get(self, record_id: str) -> ExtractionRecord | None:
        rows = self._execute(f"SELECT {', '.join(_COLUMNS)} FROM extractions WHERE id = ?",
                             (record_id,), fetch=True)
        return self._row_to_record(rows[0]) if rows else None

    def list(self, limit: int = 50, status: str | None = None) -> list[ExtractionRecord]:
        sql = f"SELECT {', '.join(_COLUMNS)} FROM extractions"
        params: tuple = ()
        if status:
            sql += " WHERE status = ?"
            params = (status,)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        rows = self._execute(sql, params + (limit,), fetch=True)
        return [self._row_to_record(r) for r in rows]

    def update(self, record_id: str, *, data: dict, fields: dict, issues: list,
               status: str) -> ExtractionRecord | None:
        self._execute(
            "UPDATE extractions SET data = ?, fields = ?, issues = ?, status = ?, updated_at = ? "
            "WHERE id = ?",
            (json.dumps(data), json.dumps(fields), json.dumps(issues), status, _now(), record_id),
        )
        return self.get(record_id)

    def delete(self, record_id: str) -> bool:
        exists = self.get(record_id) is not None
        self._execute("DELETE FROM extractions WHERE id = ?", (record_id,))
        return exists
