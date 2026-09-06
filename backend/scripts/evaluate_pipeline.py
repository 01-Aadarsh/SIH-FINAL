#!/usr/bin/env python3
"""
IP-SAKTI Sahayak (SIH26045) - Offline Pipeline Benchmark & Evaluation Suite
Evaluates citation precision, safe abstention faithfulness, and latency.

Wired to api.main.run_query() -- the same function /query and
/api/v1/voice/query call, not a reimplementation -- because this
project's actual pipeline entrypoint is a plain async function, not a bare
LangGraph `app` with a `{"question": ..., "language": "en", ...}` input
shape. A few concrete adaptations from an earlier draft of this script,
kept here rather than silently applied elsewhere:

  - Citation matching checks source_file/section_heading/chunk_id (the
    real fields on this project's Citation objects -- see
    docs/API_CONTRACT.md) plus the answer text itself, not `act`/
    `section`/`text_snippet`, which aren't fields this system's citations
    ever carry. Matching only against those non-existent fields would
    make every single query register "Citation Match: NO" regardless of
    whether the answer was actually correct -- a broken report, not a
    strict one.
  - Abstention is read from response.flags.abstained -- the one
    documented, authoritative signal (see docs/API_CONTRACT.md) -- rather
    than string-sniffing the answer for a phrase ("insufficient statutory
    authority") this system's abstention prompt doesn't use (the real
    marker is generation/prompts.py::ABSTENTION_MARKER, "I could not find
    this in my sources.").
  - `language` is fixed to "en-IN", the actual code this project's
    Literal-typed language field accepts; a bare "en" isn't one of the 23
    valid Sarvam codes.
  - No `category` input field -- formulation_category is triaged
    internally (graph/formulation.py), not something a caller supplies.

Usage (from backend/, venv active):
    python scripts/evaluate_pipeline.py
    python -m scripts.evaluate_pipeline
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# backend/ on sys.path, so this also runs as a plain script (not just
# `-m scripts.evaluate_pipeline`) -- this file lives at backend/scripts/,
# so its own parent's parent is backend/, not the repo root.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from tabulate import tabulate
from dotenv import load_dotenv

load_dotenv(override=True)

from api.main import run_query  # noqa: E402 -- must follow the sys.path insert above

# data/ is a sibling of backend/, at the repo root -- BACKEND_ROOT.parent,
# not BACKEND_ROOT itself.
REPO_ROOT = BACKEND_ROOT.parent
BENCHMARK_PATH = REPO_ROOT / "data" / "eval_benchmark.json"
REPORT_PATH = BACKEND_ROOT / "eval_report.json"


def check_citations_matched(
    response_citations: List[Dict[str, Any]], answer_text: str, expected_statutes: List[str]
) -> bool:
    """True if ANY expected statutory marker (case-insensitive) appears in
    the citations' real fields (source_file, section_heading, chunk_id) or
    in the answer text itself -- a citation's own heading doesn't always
    literally spell out a statute number even when the underlying passage
    is the right one. Vacuously True when expected_statutes is empty (the
    hallucination-trap queries), since there's nothing to check there.

    source_file is an underscored filename (e.g.
    "Biological_Diversity_Act_2002.pdf" -- see ingestion/loader.py), while
    a benchmark's expected_statutes are natural spaced phrases ("Biological
    Diversity Act"). A plain substring check between the two never matches
    even when the citation is exactly right -- caught by manually
    inspecting a "FAIL" result whose actual citations and answer were both
    correct. Underscores are normalized to spaces before matching so a
    real, correct citation isn't scored as a miss over punctuation."""
    if not expected_statutes:
        return True

    citation_corpus = " ".join(
        f"{c.get('source_file', '')} {c.get('section_heading', '')} {c.get('chunk_id', '')}"
        for c in response_citations
    ).lower().replace("_", " ")
    citation_corpus += " " + (answer_text or "").lower()

    return any(statute.lower() in citation_corpus for statute in expected_statutes)


async def run_single_eval(test_case: Dict[str, Any]) -> Dict[str, Any]:
    """Runs a single test case through the real live pipeline (real DB,
    real LLM -- same run_query() /query itself calls) and measures
    metrics."""
    query = test_case["query"]
    jurisdiction = test_case.get("jurisdiction", "india")
    expected_statutes = test_case.get("expected_statutes", [])
    should_abstain = test_case.get("should_abstain", False)

    start_time = time.perf_counter()
    try:
        response = await run_query(
            question=query,
            history=[],
            jurisdiction=jurisdiction,
            language="en-IN",
            synthesize_audio=False,
        )
        latency_ms = (time.perf_counter() - start_time) * 1000

        answer = response.answer or ""
        citations = [c.model_dump() for c in response.citations]
        # The authoritative signal (see docs/API_CONTRACT.md) -- not
        # string-matched from answer text, which this system deliberately
        # never requires a caller to do.
        abstained = response.flags.abstained

        if should_abstain:
            abstention_pass = abstained is True
            citation_pass = True  # Not evaluated for out-of-scope queries
        else:
            abstention_pass = abstained is False
            citation_pass = check_citations_matched(citations, answer, expected_statutes)

        test_passed = abstention_pass and citation_pass

        return {
            "id": test_case["id"],
            "query": query[:45] + "..." if len(query) > 45 else query,
            "category": test_case["category"],
            "expected_statute": ", ".join(expected_statutes) if expected_statutes else "ABSTAIN",
            "actual_abstained": abstained,
            "answer_preview": answer[:200],
            "matched_citation": citation_pass,
            "abstained_correctly": abstention_pass,
            "overall_pass": test_passed,
            "latency_ms": round(latency_ms, 1),
            "citations_found": len(citations),
        }

    except Exception as e:
        latency_ms = (time.perf_counter() - start_time) * 1000
        return {
            "id": test_case["id"],
            "query": query[:45] + "..." if len(query) > 45 else query,
            "category": test_case["category"],
            "expected_statute": ", ".join(expected_statutes),
            "actual_abstained": None,
            "answer_preview": "",
            "matched_citation": False,
            "abstained_correctly": False,
            "overall_pass": False,
            "latency_ms": round(latency_ms, 1),
            "citations_found": 0,
            "error": str(e),
        }


async def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not BENCHMARK_PATH.exists():
        print(f"Error: Benchmark file not found at {BENCHMARK_PATH}")
        sys.exit(1)

    with open(BENCHMARK_PATH, "r", encoding="utf-8") as f:
        test_cases = json.load(f)

    print("\n=======================================================")
    print("   IP-SAKTI Sahayak: Live Pipeline Benchmark Runner   ")
    print(f"   Target Corpus: {len(test_cases)} Curated Statutory Cases           ")
    print("=======================================================\n")

    results = []
    for idx, test_case in enumerate(test_cases, 1):
        print(f"Evaluating [{idx:02d}/{len(test_cases)}]: {test_case['id']}...", end="", flush=True)
        res = await run_single_eval(test_case)
        results.append(res)
        status_flag = "PASS" if res["overall_pass"] else "FAIL"
        print(f" [{status_flag}] ({res['latency_ms']} ms)")

    total_tests = len(results)
    passed_tests = sum(1 for r in results if r["overall_pass"])

    legal_tests = [r for r in results if r["category"] != "hallucination_trap"]
    statutory_correct = sum(1 for r in legal_tests if r["matched_citation"])
    statutory_accuracy = (statutory_correct / len(legal_tests)) * 100 if legal_tests else 0.0

    trap_tests = [r for r in results if r["category"] == "hallucination_trap"]
    abstention_correct = sum(1 for r in trap_tests if r["abstained_correctly"])
    abstention_accuracy = (abstention_correct / len(trap_tests)) * 100 if trap_tests else 0.0

    mean_latency = sum(r["latency_ms"] for r in results) / total_tests if total_tests else 0.0

    table_rows = [
        [
            r["id"],
            r["query"],
            r["expected_statute"],
            "YES" if r["matched_citation"] else "NO",
            "YES" if r["abstained_correctly"] else "NO",
            f"{r['latency_ms']} ms",
            "PASS" if r["overall_pass"] else "FAIL",
        ]
        for r in results
    ]

    headers = [
        "Case ID",
        "Query Preview",
        "Expected Statute",
        "Citation Match",
        "Abstention Pass",
        "Latency",
        "Verdict",
    ]

    print("\n" + tabulate(table_rows, headers=headers, tablefmt="fancy_grid"))

    print("\n---------------- FINAL BENCHMARK METRICS ----------------")
    print(f"Total Test Cases Executed      : {total_tests}")
    print(f"Overall Test Pass Rate         : {(passed_tests / total_tests) * 100:.1f}%")
    print(f"Statutory Citation Accuracy    : {statutory_accuracy:.1f}%")
    print(f"Safe Abstention Faithfulness   : {abstention_accuracy:.1f}%")
    print(f"Average Pipeline Latency       : {mean_latency:.1f} ms")
    print("---------------------------------------------------------\n")

    errors = [r for r in results if r.get("error")]
    if errors:
        print(f"{len(errors)} case(s) raised an exception instead of completing:")
        for r in errors:
            print(f"  {r['id']}: {r['error']}")
        print()

    output_report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "metrics": {
            "total_cases": total_tests,
            "overall_pass_rate_pct": round((passed_tests / total_tests) * 100, 2),
            "statutory_accuracy_pct": round(statutory_accuracy, 2),
            "abstention_faithfulness_pct": round(abstention_accuracy, 2),
            "mean_latency_ms": round(mean_latency, 2),
        },
        "results": results,
    }

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(output_report, f, indent=2)

    print(f"Full benchmark details saved to {REPORT_PATH.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
