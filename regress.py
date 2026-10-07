#!/usr/bin/env python3
"""PromptRegression Agent
Runs an old and a new prompt against a test suite, judges the answers,
measures cost/latency, diagnoses regressions, decides APPROVE/WARN/BLOCK,
suggests a fix and re-tests it.

Usage:
  python regress.py --old prompts/prompt_v1.txt --new prompts/prompt_v2.txt --mock
  python regress.py --old prompts/prompt_v1.txt --new prompts/prompt_v2.txt   # real API
"""
import argparse, json, os, re, sys, time, datetime
from pathlib import Path

ROOT = Path(__file__).parent
APP_MODEL = os.getenv("APP_MODEL", "claude-haiku-4-5-20251001")    # model under test
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "claude-sonnet-5-5")        # judge + diagnosis
# $ per million tokens (input, output). PLACEHOLDERS: check Anthropic's pricing page.
PRICES = {"claude-haiku-4-5-20251001": (1.0, 5.0), "claude-sonnet-5-5": (3.0, 15.0)}

# Decision thresholds
MAX_PASS_DROP = 0.10      # BLOCK if pass rate falls by 10+ points
MAX_COST_RISE = 0.25      # WARN if cost per run rises 25%+
MAX_LATENCY_RISE = 0.50   # WARN if latency rises 50%+
MAX_SCORE_DROP = 0.30     # WARN if average judge score drops 0.3+


# ---------------------------------------------------------------- LLM layer
class LLM:
    def __init__(self, mock=False):
        self.mock = mock
        if not mock:
            import anthropic  # pip install anthropic ; needs ANTHROPIC_API_KEY
            self.client = anthropic.Anthropic()

    def call(self, model, system, user, max_tokens=500):
        t0 = time.time()
        if self.mock:
            text = mock_reply(system, user)
            return dict(text=text, in_tok=len(system.split()) + len(user.split()),
                        out_tok=len(text.split()), latency=0.2 + 0.01 * len(text.split()))
        r = self.client.messages.create(model=model, max_tokens=max_tokens, system=system,
                                        messages=[{"role": "user", "content": user}])
        return dict(text=r.content[0].text, in_tok=r.usage.input_tokens,
                    out_tok=r.usage.output_tokens, latency=time.time() - t0)


def mock_reply(system, user):
    """Offline stand-in for an LLM so the demo runs without an API key."""
    s, u = system.lower(), user.lower()
    if "ignore all" in u or "system prompt" in u or "john smith" in u:
        return "Sorry, I can't share that information."
    if "poem" in u:
        return "Sorry, I can only help with Brewly questions."
    if "refund" in u or "money back" in u:
        if "30 days" in s:
            if "45 days" in u:
                return "That is past our 30 days refund window, but I can offer store credit."
            return "Yes! You are within 30 days, so you qualify for a full refund."
        return "Thanks for reaching out! \U0001F60A Our team will be happy to help you with that!"
    if "deliver" in u or "ship" in u:
        return "Orders arrive in 3-5 business days."
    if "pause" in u:
        return "You can pause anytime from your account page."
    if "terrible" in u:
        return "I'm really sorry about the delay. Let me help make it right."
    return "Brewly delivers fresh coffee monthly. Pause or cancel anytime from your account."


def cost_of(model, in_tok, out_tok):
    pin, pout = PRICES.get(model, (1.0, 5.0))
    return (in_tok * pin + out_tok * pout) / 1_000_000


# ---------------------------------------------------------------- Evaluation
def code_checks(case, text):
    low, problems = text.lower(), []
    for s in case.get("must_contain", []):
        if s.lower() not in low:
            problems.append(f"missing '{s}'")
    for s in case.get("must_not_contain", []):
        if s.lower() in low:
            problems.append(f"contains forbidden '{s}'")
    mw = case.get("max_words")
    if mw and len(text.split()) > mw:
        problems.append(f"{len(text.split())} words (limit {mw})")
    return (not problems), "; ".join(problems)


JUDGE_SYS = ("You are a strict QA evaluator for a customer-support bot. Score the answer 1-5 "
             "against the rubric (5 = fully satisfies, 1 = fails). "
             'Respond ONLY with JSON: {"score": <1-5>, "reason": "<one sentence>"}')


def judge(llm, case, answer, code_ok):
    if llm.mock:
        return dict(score=5 if code_ok else 2, reason="mock judge (follows code checks)")
    prompt = (f"Customer message:\n{case['input']}\n\nBot answer:\n{answer}\n\n"
              f"Rubric:\n{case['rubric']}")
    try:
        r = llm.call(JUDGE_MODEL, JUDGE_SYS, prompt, max_tokens=200)
        data = json.loads(re.search(r"\{.*\}", r["text"], re.S).group(0))
        return dict(score=int(data["score"]), reason=str(data["reason"]))
    except Exception as e:  # judge failure should never crash the run
        return dict(score=3, reason=f"judge error: {e}")


def run_version(llm, system, cases, repeats):
    results = []
    for case in cases:
        runs = []
        for _ in range(repeats):
            r = llm.call(APP_MODEL, system, case["input"])
            ok, why = code_checks(case, r["text"])
            j = judge(llm, case, r["text"], ok)
            runs.append(dict(text=r["text"], code_ok=ok, code_why=why, score=j["score"],
                             judge_why=j["reason"], latency=r["latency"],
                             cost=cost_of(APP_MODEL, r["in_tok"], r["out_tok"])))
        passes = sum(x["code_ok"] and x["score"] >= 4 for x in runs)
        results.append(dict(id=case["id"], category=case["category"], input=case["input"],
                            passed=passes > repeats / 2, runs=runs,
                            score=sum(x["score"] for x in runs) / repeats,
                            example=runs[0]))
    return results


def summarize(results):
    n = len(results)
    runs = [x for r in results for x in r["runs"]]
    return dict(pass_rate=sum(r["passed"] for r in results) / n,
                avg_score=sum(r["score"] for r in results) / n,
                avg_cost=sum(x["cost"] for x in runs) / len(runs),
                avg_latency=sum(x["latency"] for x in runs) / len(runs))


# ---------------------------------------------------------------- Agent logic
def compare(old, new):
    o = {r["id"]: r for r in old}
    regressions = [r for r in new if o[r["id"]]["passed"] and not r["passed"]]
    fixes = [r for r in new if not o[r["id"]]["passed"] and r["passed"]]
    return regressions, fixes


def decide(so, sn, regressions):
    reasons = []
    safety = [r["id"] for r in regressions if r["category"] == "safety"]
    if safety:
        reasons.append(f"safety regression in: {', '.join(safety)}")
    if so["pass_rate"] - sn["pass_rate"] >= MAX_PASS_DROP:
        reasons.append(f"pass rate dropped {so['pass_rate']:.0%} -> {sn['pass_rate']:.0%}")
    if reasons:
        return "BLOCK", reasons
    if sn["avg_cost"] > so["avg_cost"] * (1 + MAX_COST_RISE):
        reasons.append("cost per request rose more than 25%")
    if sn["avg_latency"] > so["avg_latency"] * (1 + MAX_LATENCY_RISE):
        reasons.append("latency rose more than 50%")
    if so["avg_score"] - sn["avg_score"] >= MAX_SCORE_DROP:
        reasons.append("average quality score dropped")
    if regressions:
        reasons.append(f"{len(regressions)} test(s) newly failing")
    return ("WARN" if reasons else "APPROVE"), reasons or ["no regressions detected"]


def diagnose(llm, old_prompt, new_prompt, regressions):
    if not regressions:
        return "No regressions to diagnose."
    detail = "\n".join(f"- [{r['category']}] input: {r['input']!r} | answer: {r['example']['text']!r} "
                       f"| why failed: {r['example']['code_why'] or r['example']['judge_why']}"
                       for r in regressions)
    if llm.mock:
        return ("The new prompt removed the refund policy and loosened the tone. "
                "Refund questions now get vague answers because the 30-day rule is gone.\n\n" + detail)
    sys_p = "You are a prompt engineer. Explain in 3-4 sentences why the new prompt caused these failures."
    msg = f"OLD PROMPT:\n{old_prompt}\n\nNEW PROMPT:\n{new_prompt}\n\nFAILURES:\n{detail}"
    return llm.call(JUDGE_MODEL, sys_p, msg, max_tokens=400)["text"] + "\n\n" + detail


def suggest_fix(llm, old_prompt, new_prompt, regressions):
    if llm.mock:
        return new_prompt.rstrip() + "\n- Refunds: full refund within 30 days of purchase; after that offer store credit.\n"
    detail = "\n".join(f"- {r['input']!r} -> {r['example']['text']!r}" for r in regressions)
    sys_p = ("Rewrite the NEW PROMPT so it keeps its intended changes but fixes the failures, "
             "restoring any lost rules from the OLD PROMPT. Output ONLY the full prompt text.")
    msg = f"OLD PROMPT:\n{old_prompt}\n\nNEW PROMPT:\n{new_prompt}\n\nFAILURES:\n{detail}"
    return llm.call(JUDGE_MODEL, sys_p, msg, max_tokens=800)["text"].strip() + "\n"


# ---------------------------------------------------------------- Reporting
def delta(a, b, pct=False, money=False):
    if money:
        return f"${a:.5f} -> ${b:.5f}"
    return f"{a:.0%} -> {b:.0%}" if pct else f"{a:.2f} -> {b:.2f}"


def build_report(args, so, sn, decision, why, regressions, fixes, diagnosis, old, new, fixed=None):
    L = [f"# PromptRegression Report", "",
         f"`{args.old}` vs `{args.new}` | {datetime.datetime.now():%Y-%m-%d %H:%M} | "
         f"{'MOCK mode' if args.mock else 'live API'}", "",
         f"## Decision: **{decision}**", ""] + [f"- {w}" for w in why] + ["",
         "## Scorecard", "", "| Metric | Old | New |", "|---|---|---|",
         f"| Pass rate | {so['pass_rate']:.0%} | {sn['pass_rate']:.0%} |",
         f"| Avg judge score (1-5) | {so['avg_score']:.2f} | {sn['avg_score']:.2f} |",
         f"| Avg cost / request | ${so['avg_cost']:.5f} | ${sn['avg_cost']:.5f} |",
         f"| Avg latency (s) | {so['avg_latency']:.2f} | {sn['avg_latency']:.2f} |", "",
         "## Per-test results", "", "| Test | Category | Old | New |", "|---|---|---|---|"]
    o = {r["id"]: r for r in old}
    for r in new:
        mark = lambda p: "PASS" if p else "FAIL"
        L.append(f"| {r['id']} | {r['category']} | {mark(o[r['id']]['passed'])} | {mark(r['passed'])} |")
    L += ["", "## Diagnosis", "", diagnosis]
    if fixes:
        L += ["", "## Newly fixed", ""] + [f"- {r['id']}" for r in fixes]
    if fixed:
        L += ["", "## Suggested fix (re-tested)", "",
              f"Saved to `{fixed['path']}`. Pass rate with the fix: **{fixed['summary']['pass_rate']:.0%}** "
              f"(new prompt: {sn['pass_rate']:.0%}, old prompt: {so['pass_rate']:.0%})."]
    return "\n".join(L) + "\n"


def save_history(args, so, sn, decision):
    p = ROOT / "history" / "runs.json"
    p.parent.mkdir(exist_ok=True)
    hist = json.loads(p.read_text()) if p.exists() else []
    hist.append(dict(time=datetime.datetime.now().isoformat(timespec="seconds"), old=args.old,
                     new=args.new, old_metrics=so, new_metrics=sn, decision=decision, mock=args.mock))
    p.write_text(json.dumps(hist, indent=2))


# ---------------------------------------------------------------- Main
def main():
    ap = argparse.ArgumentParser(description="PromptRegression Agent")
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--cases", default=str(ROOT / "tests" / "cases.json"))
    ap.add_argument("--repeats", type=int, default=1, help="runs per test (use 3 to reduce judge noise)")
    ap.add_argument("--mock", action="store_true", help="offline demo, no API key needed")
    ap.add_argument("--no-suggest", action="store_true")
    args = ap.parse_args()

    llm = LLM(mock=args.mock)
    cases = json.loads(Path(args.cases).read_text())
    old_p, new_p = Path(args.old).read_text(), Path(args.new).read_text()

    print(f"Running {len(cases)} tests x {args.repeats} on both prompts...")
    old, new = run_version(llm, old_p, cases, args.repeats), run_version(llm, new_p, cases, args.repeats)
    so, sn = summarize(old), summarize(new)
    regressions, fixes = compare(old, new)
    decision, why = decide(so, sn, regressions)
    diagnosis = diagnose(llm, old_p, new_p, regressions)

    fixed = None
    if regressions and not args.no_suggest:
        suggestion = suggest_fix(llm, old_p, new_p, regressions)
        path = ROOT / "prompts" / "prompt_suggested.txt"
        path.write_text(suggestion)
        fixed = dict(path=str(path.relative_to(ROOT)),
                     summary=summarize(run_version(llm, suggestion, cases, args.repeats)))

    report = build_report(args, so, sn, decision, why, regressions, fixes, diagnosis, old, new, fixed)
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "latest.md").write_text(report)
    save_history(args, so, sn, decision)

    print(f"\nDecision: {decision}")
    for w in why:
        print(f"  - {w}")
    print(f"Pass rate {delta(so['pass_rate'], sn['pass_rate'], pct=True)} | "
          f"cost {delta(so['avg_cost'], sn['avg_cost'], money=True)}")
    print("Report: reports/latest.md")
    sys.exit(1 if decision == "BLOCK" else 0)   # non-zero exit blocks the CI pipeline


if __name__ == "__main__":
    main()
