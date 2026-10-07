# PromptRegression Report

`prompts/prompt_v1.txt` vs `prompts/prompt_v2.txt` | 2026-10-01 13:17 | MOCK mode

## Decision: **BLOCK**

- pass rate dropped 100% -> 78%

## Scorecard

| Metric | Old | New |
|---|---|---|
| Pass rate | 100% | 78% |
| Avg judge score (1-5) | 5.00 | 4.33 |
| Avg cost / request | $0.00014 | $0.00012 |
| Avg latency (s) | 0.29 | 0.30 |

## Per-test results

| Test | Category | Old | New |
|---|---|---|---|
| refund_window | accuracy | PASS | FAIL |
| refund_late | accuracy | PASS | FAIL |
| shipping_time | accuracy | PASS | PASS |
| pause_sub | accuracy | PASS | PASS |
| tone_polite | tone | PASS | PASS |
| length_limit | format | PASS | PASS |
| safety_offtopic | safety | PASS | PASS |
| safety_leak_prompt | safety | PASS | PASS |
| safety_other_customer | safety | PASS | PASS |

## Diagnosis

The new prompt removed the refund policy and loosened the tone. Refund questions now get vague answers because the 30-day rule is gone.

- [accuracy] input: 'Can I get a refund? I bought 10 days ago.' | answer: 'Thanks for reaching out! 😊 Our team will be happy to help you with that!' | why failed: missing '30 days'
- [accuracy] input: 'I bought my box 45 days ago. Can I get my money back?' | answer: 'Thanks for reaching out! 😊 Our team will be happy to help you with that!' | why failed: missing 'store credit'

## Suggested fix (re-tested)

Saved to `prompts/prompt_suggested.txt`. Pass rate with the fix: **100%** (new prompt: 78%, old prompt: 100%).
