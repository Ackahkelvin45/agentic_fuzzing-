"""test_signal.py — prove the proxy signal is ACTIONABLE before spending tokens.

Each assertion feeds decide_refinement a synthetic summary crafted so exactly
one signal fires, and checks it maps to the distinct, expected strategy edit.
The guardrail (acceptance up + novelty down -> revert) is tested too. This is
the critic's demand: show the signal produces a nameable change per case, not
vague churn — verifiable with NO LLM and NO API spend.

Run:  .venv/bin/python fuzz/test_signal.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "fuzz"))
sys.path.insert(0, str(ROOT / "fuzz" / "loop"))
from agent import decide_refinement, _PRODS  # noqa: E402

_p = _f = 0


def check(name: str, got, want) -> None:
    global _p, _f
    ok = got == want
    _p += ok
    _f += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: {got}" + ("" if ok else f"  != {want}"))


# A baseline 'healthy' summary where every steer is satisfied, so only the
# signal we intentionally break should fire.
def healthy() -> dict:
    return {
        "acceptance_rate": 0.6,
        "novelty": 40,
        "max_nesting_depth": 2000,
        "productions_accepted": list(_PRODS),
        "productions_gap": [],
        "cap_distance_mass": 0.2,
        "divergences": 0,
    }


def main() -> int:
    # 1. Coverage gap -> add/upweight that production.
    s = healthy(); s["productions_gap"] = ["object"]
    check("coverage gap fires add_or_upweight", decide_refinement(s)[0], "add_or_upweight_production")

    # 2. Shallow near-cap mass -> deepen toward cap.
    s = healthy(); s["cap_distance_mass"] = 0.0
    check("low cap-distance fires deepen", decide_refinement(s)[0], "generate_nesting_toward_cap")

    # 3. Fresh crash dominates everything.
    s = healthy(); s["productions_gap"] = ["null"]  # even with a gap present
    check("crash dominates", decide_refinement(s, new_crash=True)[0], "intensify_near_crash")

    # 4. Probe repair: a should-be-valid feature accepted too rarely.
    s = healthy()
    probes = {"unicode_escape": 0.05, "big_number": 0.9}
    check("broken probe fires repair", decide_refinement(s, probe_acceptance=probes)[0], "fix_feature_encoding")

    # 5. Guardrail: acceptance up but novelty down -> revert (quiet failure).
    prev = healthy(); prev["acceptance_rate"] = 0.4; prev["novelty"] = 50
    cur = healthy(); cur["acceptance_rate"] = 0.8; cur["novelty"] = 20
    check("quiet-failure guardrail fires revert", decide_refinement(cur, prev=prev)[0], "revert_last_edit")

    # 6. Guardrail does NOT fire when novelty holds even as acceptance rises.
    prev = healthy(); prev["acceptance_rate"] = 0.4; prev["novelty"] = 20
    cur = healthy(); cur["acceptance_rate"] = 0.8; cur["novelty"] = 45
    check("healthy improvement is not reverted", decide_refinement(cur, prev=prev)[0], "broaden_exploration")

    # 7. Each distinct signal produced a DISTINCT action (no churn/collapse).
    actions = {
        decide_refinement(dict(healthy(), productions_gap=["array"]))[0],
        decide_refinement(dict(healthy(), cap_distance_mass=0.0))[0],
        decide_refinement(healthy(), new_crash=True)[0],
        decide_refinement(healthy(), probe_acceptance={"unicode_escape": 0.0})[0],
    }
    check("four signals -> four distinct actions", len(actions), 4)

    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    raise SystemExit(main())
