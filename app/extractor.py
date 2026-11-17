"""The extraction pipeline.

    PDF bytes
      -> text (pdf_text.py)
      -> LLM fills the schema and rates its confidence per field
      -> Pydantic validates types and cleans numbers
      -> grounding check: is each value really in the document?
      -> business rules: do the numbers add up?
      -> final confidence per field; anything shaky is flagged for review

The model's own confidence is only a starting point. Models are often
confidently wrong, so a value that isn't in the text, or that breaks a
business rule, has its confidence capped no matter what the model said.
"""

import json
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.config import Settings, settings as default_settings
from app.grounding import is_grounded
from app.llm import ChatModel, chat_json
from app.pdf_text import extract_text
from app.schemas import DOC_TYPES, scalar_fields
from app.validation import Issue, validate

UNGROUNDED_CAP = 0.4  # value not found in the document
RULE_BREAK_CAP = 0.3  # value involved in a failed business rule
DEFAULT_CONFIDENCE = 0.5  # model gave no confidence

SYSTEM = (
    "You extract data from business documents into JSON. Copy values exactly "
    "as they appear; never guess or calculate a value that is not printed. "
    "Use null for anything not in the document. Reply with JSON only."
)

USER = """Document type: {label}

Fill this JSON schema:
{schema}

Rules:
- Dates as YYYY-MM-DD.
- Numbers as plain numbers without currency symbols or thousands separators.
- currency as a 3-letter ISO code (USD, GBP, EUR, INR...), inferred from symbols if needed.

Also rate how sure you are about each top-level field, from 0 to 1.

Reply as JSON:
{{"data": {{...fields...}}, "confidence": {{"<field name>": 0.0-1.0, ...}}}}

Document text:
<<<
{text}
>>>"""


@dataclass
class FieldResult:
    value: object
    confidence: float
    grounded: bool
    flags: list[str] = field(default_factory=list)


@dataclass
class ExtractionResult:
    doc_type: str
    data: dict
    fields: dict[str, FieldResult]
    issues: list[Issue]
    needs_review: bool
    pages: int = 0
    model: str = ""

    def to_dict(self) -> dict:
        return {
            "doc_type": self.doc_type,
            "data": self.data,
            "fields": {k: vars(v) for k, v in self.fields.items()},
            "issues": [i.to_dict() for i in self.issues],
            "needs_review": self.needs_review,
            "pages": self.pages,
            "model": self.model,
        }


def score_fields(
    doc_type: str,
    data: dict,
    text: str,
    model_confidence: dict,
    issues: list[Issue],
    threshold: float,
) -> tuple[dict[str, FieldResult], bool]:
    """Combine model confidence, grounding and rule checks into a final score per field."""
    model_cls, _, required = DOC_TYPES[doc_type]
    rule_fields = {f for issue in issues if issue.severity == "error" for f in issue.fields}
    results: dict[str, FieldResult] = {}

    for name in scalar_fields(model_cls) + ["line_items"]:
        value = data.get(name)
        raw = model_confidence.get(name, DEFAULT_CONFIDENCE)
        conf = float(raw) if isinstance(raw, (int, float)) else DEFAULT_CONFIDENCE
        conf = min(max(conf, 0.0), 1.0)
        flags: list[str] = []

        if name == "line_items":
            grounded = all(
                is_grounded(item.get("amount"), text) for item in (value or [])
            )
        else:
            grounded = is_grounded(value, text, name)

        if not grounded:
            conf = min(conf, UNGROUNDED_CAP)
            flags.append("not found in document")
        if name in rule_fields:
            conf = min(conf, RULE_BREAK_CAP)
            flags.append("fails a validation check")
        if value in (None, "", []) and name in required:
            conf = 0.0
            flags.append("required but missing")
        if value not in (None, "", []) and conf < threshold and not flags:
            flags.append("low model confidence")

        results[name] = FieldResult(value=value, confidence=round(conf, 2),
                                    grounded=grounded, flags=flags)

    needs_review = any(r.flags for r in results.values()) or any(
        i.severity == "error" for i in issues
    )
    return results, needs_review


def extract_from_text(
    text: str,
    doc_type: str,
    llm: ChatModel,
    settings: Settings = default_settings,
) -> ExtractionResult:
    if doc_type not in DOC_TYPES:
        raise ValueError(f"Unknown document type '{doc_type}'. Choose from {list(DOC_TYPES)}.")
    model_cls, label, _ = DOC_TYPES[doc_type]
    text = text[: settings.max_text_chars]

    reply = chat_json(
        llm, SYSTEM,
        USER.format(label=label, schema=json.dumps(model_cls.model_json_schema()), text=text),
    )
    raw_data = reply.get("data", reply)  # tolerate models that skip the wrapper
    confidence = reply.get("confidence", {}) if isinstance(reply.get("confidence"), dict) else {}

    try:
        data = model_cls.model_validate(raw_data).model_dump()
    except ValidationError:
        # Keep what's valid: validate field by field, drop the broken ones.
        data = {}
        for name in model_cls.model_fields:
            try:
                data |= model_cls.model_validate({name: raw_data.get(name)}).model_dump(include={name})
            except ValidationError:
                data[name] = [] if name == "line_items" else None
                confidence[name] = 0.0

    issues = validate(doc_type, data)
    fields, needs_review = score_fields(doc_type, data, text, confidence, issues,
                                        settings.review_threshold)
    return ExtractionResult(doc_type=doc_type, data=data, fields=fields, issues=issues,
                            needs_review=needs_review, model=getattr(llm, "model", ""))


def extract_from_pdf(
    pdf_bytes: bytes,
    doc_type: str,
    llm: ChatModel,
    settings: Settings = default_settings,
) -> ExtractionResult:
    text, pages = extract_text(pdf_bytes, settings.max_pages)
    result = extract_from_text(text, doc_type, llm, settings)
    result.pages = pages
    return result


def revalidate(doc_type: str, data: dict, previous_fields: dict) -> tuple[dict, list[Issue], dict]:
    """Re-check data after a person corrected it.

    Fields the person changed get confidence 1.0; rules are re-run on the new data.
    """
    model_cls, _, _ = DOC_TYPES[doc_type]
    clean = model_cls.model_validate(data).model_dump()
    issues = validate(doc_type, clean)
    rule_fields = {f for i in issues if i.severity == "error" for f in i.fields}
    fields = {}
    for name in scalar_fields(model_cls) + ["line_items"]:
        old = previous_fields.get(name, {})
        changed = old.get("value") != clean.get(name)
        flags = ["fails a validation check"] if name in rule_fields else []
        fields[name] = {
            "value": clean.get(name),
            "confidence": 1.0 if changed or not old else old.get("confidence", 1.0),
            "grounded": old.get("grounded", True),
            "flags": flags + (["corrected by reviewer"] if changed and old else []),
        }
    return clean, issues, fields
