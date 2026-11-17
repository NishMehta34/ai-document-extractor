"""Shared fixtures. Tests use the committed sample invoices and a fake LLM: no network."""

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SAMPLES = ROOT / "eval" / "samples"


def sample_pdf(n: int = 1) -> bytes:
    return (SAMPLES / f"invoice_{n:02d}.pdf").read_bytes()


def sample_truth(n: int = 1) -> dict:
    return json.loads((SAMPLES / f"invoice_{n:02d}.json").read_text())


class FakeLLM:
    """Replies with a fixed payload; optionally applies a change to simulate model mistakes."""

    model = "fake-model"

    def __init__(self, data: dict, confidence: float = 0.95, mutate=None, wrap: bool = True):
        self.data = copy.deepcopy(data)
        if mutate:
            mutate(self.data)
        self.confidence = confidence
        self.wrap = wrap
        self.calls = 0

    def chat(self, system: str, user: str) -> str:
        self.calls += 1
        conf = {k: self.confidence for k in self.data}
        body = {"data": self.data, "confidence": conf} if self.wrap else self.data
        return "```json\n" + json.dumps(body) + "\n```"


@pytest.fixture
def truth():
    return sample_truth(1)
