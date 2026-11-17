"""Export extractions to CSV or Excel.

CSV: one row per line item, with the document fields repeated on each row.
     This is the shape most accounting tools import.
Excel: a "Documents" sheet (one row per document) and a "Line items" sheet.
"""

import csv
import io

from app.schemas import DOC_TYPES, scalar_fields
from app.storage import ExtractionRecord

_META = ["id", "filename", "doc_type", "status", "created_at"]
_ITEM_COLUMNS = ["description", "quantity", "unit_price", "amount"]


def _header_fields(records: list[ExtractionRecord]) -> list[str]:
    """Union of document fields across the records' types, in schema order."""
    names: list[str] = []
    for record in records:
        for name in scalar_fields(DOC_TYPES[record.doc_type][0]):
            if name not in names:
                names.append(name)
    return names


def _meta(record: ExtractionRecord) -> list:
    return [getattr(record, m) for m in _META]


def to_csv(records: list[ExtractionRecord]) -> str:
    fields = _header_fields(records)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(_META + fields + ["line_no"] + [f"item_{c}" for c in _ITEM_COLUMNS])
    for record in records:
        doc = _meta(record) + [record.data.get(f) for f in fields]
        items = record.data.get("line_items") or [{}]
        for i, item in enumerate(items, start=1):
            writer.writerow(doc + [i if item else ""] + [item.get(c) for c in _ITEM_COLUMNS])
    return out.getvalue()


def to_xlsx(records: list[ExtractionRecord]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    fields = _header_fields(records)
    wb = Workbook()
    docs = wb.active
    docs.title = "Documents"
    docs.append(_META + fields + ["needs_review_fields"])
    review_fill = PatternFill("solid", fgColor="FFF4CC")

    for record in records:
        flagged = [n for n, f in record.fields.items() if f.get("flags")]
        docs.append(_meta(record) + [record.data.get(f) for f in fields] + [", ".join(flagged)])
        if record.status == "needs_review":
            for cell in docs[docs.max_row]:
                cell.fill = review_fill

    items = wb.create_sheet("Line items")
    items.append(["id", "filename", "line_no"] + _ITEM_COLUMNS)
    for record in records:
        for i, item in enumerate(record.data.get("line_items") or [], start=1):
            items.append([record.id, record.filename, i] + [item.get(c) for c in _ITEM_COLUMNS])

    for sheet in (docs, items):
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = "A2"
        for column in sheet.columns:
            width = max(len(str(c.value or "")) for c in column)
            sheet.column_dimensions[column[0].column_letter].width = min(max(width + 2, 10), 45)

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
