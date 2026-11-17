"""The evaluation script itself is tested with a fake model that reads the answer key."""

import json
import re

from eval.run_eval import evaluate, same, to_markdown
from tests.conftest import SAMPLES


class AnswerKeyLLM:
    """Returns the correct answer for whichever sample invoice it is shown,
    optionally with one field changed to simulate a model mistake."""

    model = "answer-key"

    def __init__(self, break_field=None, break_value=None):
        self.break_field, self.break_value = break_field, break_value
        self.truths = {json.loads(p.read_text())["invoice_number"]: json.loads(p.read_text())
                       for p in SAMPLES.glob("*.json")}

    def chat(self, system, user):
        number = next(n for n in self.truths if re.search(re.escape(n), user))
        data = dict(self.truths[number])
        if self.break_field:
            data[self.break_field] = self.break_value
        return json.dumps({"data": data, "confidence": {k: 0.95 for k in data}})


def test_same():
    assert same(10.0, "10.001")
    assert same("Acme Ltd.", "acme ltd")
    assert same(None, None) and not same(None, "x")


def test_perfect_model_scores_100():
    r = evaluate(AnswerKeyLLM(), limit=6)
    assert r["field_accuracy"] == 100.0
    assert r["line_items_accuracy"] == 100.0
    assert r["false_alarm_rate"] == 0.0  # correct values are never flagged
    assert r["auto_approved_with_errors"] == 0
    assert "100.0%" in to_markdown(r)


def test_wrong_values_are_caught():
    r = evaluate(AnswerKeyLLM("customer_name", "Invented Customer Ltd"), limit=6)
    assert r["per_field"]["customer_name"] == 0.0
    assert r["error_catch_rate"] == 100.0
    assert r["auto_approved_with_errors"] == 0
