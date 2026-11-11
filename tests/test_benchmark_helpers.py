"""The benchmark's two objective checks must count what they claim to count."""

from backend.benchmarks.single_vs_pipeline import PICK, covered, summarise


def test_covered_requires_every_option_case_insensitively():
    assert covered("graphql and REST both work", ["REST", "GraphQL"])
    assert not covered("only REST is discussed", ["REST", "GraphQL"])


def test_explicit_pick_is_detected():
    assert PICK.search("I recommend DynamoDB for this.")
    assert not PICK.search("Both are fine; it depends.")


def test_summary_counts_only_completed_runs_for_cost_and_all_runs_for_checks():
    rows = [
        {"ok": True, "seconds": 2.0, "tokens": 100, "cost_usd": 0.001,
         "answer": "REST wins, pick it"},
        {"ok": False, "seconds": 9.0, "tokens": 0, "cost_usd": 0.0, "answer": ""},
    ]
    s = summarise(rows, [["REST"], ["REST"]])
    assert s["runs"] == 2 and s["completed"] == 1
    assert s["mean_tokens"] == 100 and s["median_seconds"] == 2.0
    assert s["mentions_every_option"] == 1 and s["makes_explicit_pick"] == 1
