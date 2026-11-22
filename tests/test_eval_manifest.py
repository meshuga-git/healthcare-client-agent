import json
from pathlib import Path
from collections import Counter
from harbor.evaluate import run_case


def test_frozen_stratified_manifest():
    cases = json.loads((Path(__file__).resolve().parents[1] / "evals/scenarios.json").read_text())
    assert len(cases) == 200
    assert len({c["id"] for c in cases}) == 200
    assert Counter(c["split"] for c in cases) == {"dev": 150, "heldout": 50}
    assert all(25 <= n <= 35 for n in Counter(c["vertical"] for c in cases).values())


def test_one_dev_case_executes_real_api():
    assert run_case({"id": "unit-dev-book", "vertical": "dental", "kind": "book", "script": "Book", "offset_days": 3})[
        "passed"
    ]
