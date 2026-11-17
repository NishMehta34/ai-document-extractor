"""Get text out of a PDF.

Only text-based PDFs are supported. A scanned PDF is an image with no text
layer; we detect that and say so rather than sending empty text to the model.
"""

import io
import re

from pypdf import PdfReader
from pypdf.errors import PdfReadError


class DocumentError(ValueError):
    """A problem with the uploaded file that the user can fix."""


def extract_text(pdf_bytes: bytes, max_pages: int = 5) -> tuple[str, int]:
    """Return (text, page_count). Raises DocumentError for unusable files."""
    if not pdf_bytes.startswith(b"%PDF"):
        raise DocumentError("This file is not a PDF.")
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        if reader.is_encrypted:
            raise DocumentError("This PDF is password-protected.")
        pages = len(reader.pages)
        if pages > max_pages:
            raise DocumentError(f"This PDF has {pages} pages; the limit is {max_pages}.")
        parts = [
            f"--- Page {i + 1} ---\n{page.extract_text() or ''}"
            for i, page in enumerate(reader.pages)
        ]
    except PdfReadError as exc:
        raise DocumentError(f"Could not read this PDF ({exc}).") from exc

    text = "\n".join(parts).strip()
    letters = len(re.sub(r"[^A-Za-z0-9]", "", re.sub(r"--- Page \d+ ---", "", text)))
    if letters < 30:
        raise DocumentError(
            "No text found. This looks like a scanned PDF; scanned documents "
            "are not supported yet."
        )
    return text, pages
