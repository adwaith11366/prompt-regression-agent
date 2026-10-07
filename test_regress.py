import json
from pathlib import Path

from regress import LLM, code_checks, compare, decide, run_version, summarize

ROOT = Path(__file__).resolve().parent.parent
CASES = json.loads((ROOT / "tests" / "cases.json").read_text())


def metrics(pass_rate=1.0, score=5.0, cost=1.0, latency=1.0):
    return dict(pass_rate=pass_rate, avg_score=score, avg_cost=cost, avg_latency=latency)


def test_code_checks_pass_and_fail():
    case = {"must_contain": ["30 days"], "must_not_contain": ["calm down"], "max_words": 5}
    assert code_checks(case, "Refund within 30 days")[0]
    ok, why = code_checks(case, "Please calm down and wait a very long time")
    assert not ok and "30 days" in why and "calm down" in why


def test_safety_regression_blocks():
    reg = [{"id": "x", "category": "safety"}]
    decision, _ = decide(metrics(), metrics(0.95), reg)
    assert decision == "BLOCK"


def test_big_pass_rate_drop_blocks():
    assert decide(metrics(1.0), metrics(0.8), [])[0] == "BLOCK"


def test_cost_rise_warns():
    assert decide(metrics(), metrics(cost=1.5), [])[0] == "WARN"


def test_identical_prompts_approve():
    assert decide(metrics(), metrics(), [])[0] == "APPROVE"


def test_compare_finds_regressions_and_fixes():
    old = [{"id": "a", "passed": True, "category": "accuracy"}, {"id": "b", "passed": False, "category": "accuracy"}]
    new = [{"id": "a", "passed": False, "category": "accuracy"}, {"id": "b", "passed": True, "category": "accuracy"}]
    reg, fixes = compare(old, new)
    assert [r["id"] for r in reg] == ["a"] and [r["id"] for r in fixes] == ["b"]


def test_end_to_end_mock_blocks_bad_prompt_and_approves_same_prompt():
    llm = LLM(mock=True)
    v1 = (ROOT / "prompts" / "prompt_v1.txt").read_text()
    v2 = (ROOT / "prompts" / "prompt_v2.txt").read_text()
    r1, r2 = run_version(llm, v1, CASES, 1), run_version(llm, v2, CASES, 1)
    reg, _ = compare(r1, r2)
    assert {r["id"] for r in reg} == {"refund_window", "refund_late"}
    assert decide(summarize(r1), summarize(r2), reg)[0] == "BLOCK"
    same, _ = compare(r1, run_version(llm, v1, CASES, 1))
    assert not same
