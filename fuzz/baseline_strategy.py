"""baseline_strategy.py — Step 3: the intentionally naive generator.

Purpose is PLUMBING, not bug-finding. This proves the whole pipeline works:
generate input -> run through harness -> classify crash/reject/valid -> log.

The strategy is just `st.text()` (arbitrary unicode). Against a JSON parser
almost everything will be REJECTED at the front door — that is the expected,
correct result, and seeing a healthy reject count (with no crashes and the
occasional accidental "valid", e.g. a bare number or string) is exactly how we
confirm the classifier distinguishes the three outcomes.

Run:  python fuzz/baseline_strategy.py [path-to-harness] [max_examples]
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from hypothesis import given, settings, HealthCheck, strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runner import run_once, Outcome  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HARNESS = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "build" / "harness")
MAX_EXAMPLES = int(sys.argv[2]) if len(sys.argv) > 2 else 300

# The naive baseline: arbitrary text. No grammar knowledge at all.
naive_strategy = st.text()

_tally: Counter[str] = Counter()
_crash_inputs: list[bytes] = []
_valid_examples: list[str] = []


@settings(max_examples=MAX_EXAMPLES, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(s=naive_strategy)
def test_pipeline(s: str) -> None:
    data = s.encode("utf-8", "surrogatepass")
    result = run_once(HARNESS, data)
    _tally[result.outcome.value] += 1
    if result.outcome is Outcome.VALID and len(_valid_examples) < 10:
        _valid_examples.append(s)
    if result.outcome is Outcome.CRASH:
        _crash_inputs.append(data)
        # Fail so Hypothesis engages its shrinker on the crashing input.
        raise AssertionError(f"harness crashed: {result.signature_source()}")


def main() -> int:
    print(f"[baseline] harness={HARNESS}  max_examples={MAX_EXAMPLES}")
    crashed = False
    try:
        test_pipeline()
    except AssertionError as e:
        crashed = True
        print(f"[baseline] CRASH surfaced (shrunk): {e}")
    total = sum(_tally.values())
    # SKIP is reported separately so 'reject' always means "parser rejected".
    print(f"[baseline] outcomes over {total} runs: {dict(_tally)}")

    # Prove the VALID branch is real: show accepted examples and assert none is
    # an empty/whitespace-only "exit-0-for-the-wrong-reason" false positive.
    print(f"[baseline] sample accepted inputs: {[repr(v) for v in _valid_examples[:6]]}")
    empties = [v for v in _valid_examples if v.strip() == ""]
    if empties:
        print(f"[baseline] FAIL: {len(empties)} accepted inputs were empty/whitespace only.")
        return 1

    got_valid = _tally.get(Outcome.VALID.value, 0) > 0
    got_reject = _tally.get(Outcome.REJECT.value, 0) > 0
    if not crashed and got_valid and got_reject:
        print("[baseline] OK: valid + reject branches both exercised; no crashes on naive text.")
        return 0
    if crashed:
        print("[baseline] NOTE: naive text crashed the target — investigate.")
        return 1
    print("[baseline] WARNING: did not see both valid and reject — check the harness.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
