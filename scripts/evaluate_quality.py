"""Evaluate the saved, synthetic human-labeled cases without touching the DB."""

import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.providers import MockProvider, OpenAIProvider  # noqa: E402
from app.question_quality import obvious_trivia_reason, quality_review  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["mock", "openai"], default="mock")
    parser.add_argument(
        "--cases",
        type=Path,
        default=ROOT / "backend/tests/fixtures/question_quality.json",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    provider = OpenAIProvider() if args.provider == "openai" else MockProvider()
    records = []
    started = time.monotonic()
    calls = 0
    for case in json.loads(args.cases.read_text()):
        reason = obvious_trivia_reason(case["question"], case["evidence"])
        if reason:
            review = quality_review([reason])
        else:
            calls += 1
            review = provider.review_question(
                {"question": case["question"], "evidence": case["evidence"]}
            )
        records.append(
            {"id": case["id"], "expected": case["acceptable"], "review": review}
        )
    bad = [record for record in records if not record["expected"]]
    good = [record for record in records if record["expected"]]
    summary = {
        "provider": provider.name,
        "model": os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
        if args.provider == "openai"
        else None,
        "cases": len(records),
        "bad_rejected": sum(not item["review"]["acceptable"] for item in bad),
        "bad_total": len(bad),
        "good_accepted": sum(item["review"]["acceptable"] for item in good),
        "good_total": len(good),
        "review_calls": calls,
        "seconds": round(time.monotonic() - started, 1),
        "mismatches": [
            item["id"]
            for item in records
            if item["expected"] != item["review"]["acceptable"]
        ],
    }
    if args.output:
        args.output.write_text(
            json.dumps(
                {"summary": summary, "records": records}, ensure_ascii=False, indent=2
            )
            + "\n"
        )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
