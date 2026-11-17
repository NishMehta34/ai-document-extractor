"""Measure extraction accuracy on the sample invoices.

    python eval/run_eval.py                 # uses the model in your .env
    python eval/run_eval.py --limit 5       # quick check on 5 documents

Reports:
- Field accuracy: share of fields extracted exactly right (numbers to the cent,
  text ignoring case and spacing, dates as YYYY-MM-DD).
- Line items: share of documents where every line amount is right.
- Error catch rate: of the fields the model got WRONG, how many were flagged
  for review. This matters most in practice: a wrong value that is flagged
  gets fixed by a person; a wrong value that isn't flagged slips through.
- False alarm rate: of the fields the model got RIGHT, how many were flagged
  anyway (unnecessary review work).

Writes eval/results.md.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.extractor import extract_from_pdf  # noqa: E402
from app.schemas import Invoice, scalar_fields  # noqa: E402

SAMPLES = Path(__file__).parent / "samples"
FIELDS = scalar_fields(Invoice)


def same(expected, actual) -> bool:
    if expected is None or actual is None:
        return expected is None and actual in (None, "")
    if isinstance(expected, (int, float)):
        try:
            return abs(float(actual) - expected) < 0.005
        except (TypeError, ValueError):
            return False
    norm = lambda s: re.sub(r"[\s,.]+", " ", str(s)).strip().lower()  # noqa: E731
    return norm(expected) == norm(actual)


def lines_match(expected: list, actual: list) -> bool:
    exp = sorted(round(i["amount"], 2) for i in expected)
    try:
        act = sorted(round(float(i.get("amount")), 2) for i in actual)
    except (TypeError, ValueError):
        return False
    return exp == act


def evaluate(llm, limit: int | None = None) -> dict:
    paths = sorted(SAMPLES.glob("invoice_*.pdf"))[:limit]
    per_field = {f: {"right": 0, "total": 0} for f in FIELDS}
    wrong = flagged_wrong = right = flagged_right = docs_lines_ok = auto_ok = auto_bad = 0
    failures, mistakes = [], []
    started = time.time()

    for pdf in paths:
        truth = json.loads(pdf.with_suffix(".json").read_text())
        try:
            result = extract_from_pdf(pdf.read_bytes(), "invoice", llm)
        except Exception as exc:
            failures.append(f"{pdf.name}: {exc}")
            continue
        doc_has_error = False
        for name in FIELDS:
            ok = same(truth[name], result.data.get(name))
            flagged = bool(result.fields[name].flags)
            per_field[name]["total"] += 1
            if ok:
                per_field[name]["right"] += 1
                right += 1
                flagged_right += flagged
            else:
                doc_has_error = True
                wrong += 1
                flagged_wrong += flagged
                mistakes.append(f"{pdf.name} · {name}: expected `{truth[name]}`, got "
                                f"`{result.data.get(name)}`{' (flagged)' if flagged else ' (**not flagged**)'}")
        if lines_match(truth["line_items"], result.data.get("line_items") or []):
            docs_lines_ok += 1
        else:
            doc_has_error = True
        if not result.needs_review:
            auto_bad += doc_has_error
            auto_ok += not doc_has_error

    done = len(paths) - len(failures)
    pct = lambda a, b: round(100 * a / b, 1) if b else None  # noqa: E731
    return {
        "model": getattr(llm, "model", "?"),
        "documents": len(paths),
        "failed": failures,
        "seconds": round(time.time() - started, 1),
        "field_accuracy": pct(right, right + wrong),
        "line_items_accuracy": pct(docs_lines_ok, done),
        "error_catch_rate": pct(flagged_wrong, wrong),
        "false_alarm_rate": pct(flagged_right, right),
        "auto_approved_correct": auto_ok,
        "auto_approved_with_errors": auto_bad,
        "per_field": {f: pct(v["right"], v["total"]) for f, v in per_field.items()},
        "mistakes": mistakes,
    }


def to_markdown(r: dict) -> str:
    rows = "\n".join(f"| {f} | {v}% |" for f, v in r["per_field"].items())
    catch = "n/a (no errors)" if r["error_catch_rate"] is None else f"{r['error_catch_rate']}%"
    mistakes = "\n".join(f"- {m}" for m in r["mistakes"][:40]) or "- None"
    failed = "\n".join(f"- {f}" for f in r["failed"]) or "- None"
    return f"""# Evaluation results

Model: **{r['model']}** · {r['documents']} synthetic invoices (UK, US and EU layouts) · {r['seconds']}s

| Metric | Result |
|---|---|
| Field accuracy | **{r['field_accuracy']}%** |
| Documents with all line items right | {r['line_items_accuracy']}% |
| Wrong fields flagged for review (error catch rate) | **{catch}** |
| Correct fields flagged anyway (false alarm rate) | {r['false_alarm_rate']}% |
| Auto-approved documents that were fully correct | {r['auto_approved_correct']} |
| Auto-approved documents that had an error | {r['auto_approved_with_errors']} |

## Accuracy by field
| Field | Accuracy |
|---|---|
{rows}

## Mistakes
{mistakes}

## Documents that failed to process
{failed}
"""


def main() -> None:
    from app.llm import OpenAICompatibleLLM

    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="Only evaluate the first N documents")
    args = parser.parse_args()

    results = evaluate(OpenAICompatibleLLM(), args.limit)
    markdown = to_markdown(results)
    (Path(__file__).parent / "results.md").write_text(markdown)
    print(markdown)


if __name__ == "__main__":
    main()
