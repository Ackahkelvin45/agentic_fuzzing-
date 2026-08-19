"""test_loop.py — regression tests for defects found in adversarial review.

Every test here corresponds to a bug that was live in the code and invisible to
the other suites. Named so a future reader knows what each one is protecting.

Run:  .venv/bin/python fuzz/test_loop.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "fuzz"))
sys.path.insert(0, str(ROOT / "fuzz" / "loop"))
from agent import (  # noqa: E402
    decide_refinement, extract_strategy, screen_code, summarize, _score,
    _productions_structural, _nesting_depth, StopController, refine_prompt,
    seed_prompt, EXAMPLES_PER_RUN, VALIDATE_EXAMPLES, PROBE_EXAMPLES_EACH,
)
from oracle import oracle_accepts, classify_divergence  # noqa: E402
from probes import PROBES, PROBE_EXPECT_VALID  # noqa: E402
from runner import TIMEOUT_S  # noqa: E402

_p = _f = 0


def check(name, cond, extra=""):
    global _p, _f
    _p += bool(cond); _f += not cond
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"   {extra}"))


def main() -> int:
    # --- deep-input signal integrity (the self-defeating steering bug) -------
    deep = "[" * 1600 + "1" + "]" * 1600
    prods = _productions_structural(deep)
    check("deep input yields productions (not silently empty)",
          prods == {"array", "number"}, str(prods))
    check("deep input depth measured", _nesting_depth(deep) == 1600)
    shallow_s = {"productions_accepted": ["array", "number"], "novelty": 10,
                 "cap_distance_mass": 0.0}
    deep_s = dict(shallow_s, cap_distance_mass=0.5)
    check("near-cap depth scores HIGHER, not lower", _score(deep_s) > _score(shallow_s))

    # --- decide_refinement robustness (KeyError killed a paid run) ----------
    s = {"acceptance_rate": 0.5, "novelty": 10, "productions_gap": [],
         "cap_distance_mass": 0.9}
    for prev, label in ((None, "prev=None"), ({}, "prev={} (gated iteration)"),
                        ({"acceptance_rate": 0.1}, "prev partial")):
        try:
            decide_refinement(s, prev, {}, False)
            check(f"decide_refinement survives {label}", True)
        except KeyError as e:
            check(f"decide_refinement survives {label}", False, f"KeyError {e}")

    # --- crash must not freeze the loop via a giant score -------------------
    crash_s = dict(s, productions_accepted=["array"], outcomes={"crash": 1})
    check("_score stays bounded on crash (no 1e6 freeze)", _score(crash_s) < 1000)

    # --- prompts never receive a None strategy ------------------------------
    check("seed_prompt builds without prior state", len(seed_prompt()) == 2)
    body = refine_prompt("strategy=1", {}, ("a", "b"))[-1]["content"]
    check("refine_prompt embeds real code", "strategy=1" in body)

    # --- extract_strategy fence handling ------------------------------------
    check("prefers python block over trailing bash",
          extract_strategy("```python\nstrategy=1\n```\n```bash\nrm -rf /\n```") == "strategy=1")
    check("strips ```py language tag", extract_strategy("```py\nA=1\n```") == "A=1")
    check("no fence -> raw text", extract_strategy("strategy=2") == "strategy=2")

    # --- screen_code: the demonstrated bypass stays blocked -----------------
    blocked = [
        ("hypothesis.internal chain", "import hypothesis\nhypothesis.internal.escalation.os.system('x')"),
        ("import hypothesis.internal", "import hypothesis.internal.escalation as E"),
        ("json.encoder.re", "import json\njson.encoder.re.purge()"),
        ("from os import system", "from os import system"),
        ("dunder access", "x=(1).__class__"),
        ("eval", "x=eval('1')"),
        # Found by audit AFTER the first hardening pass: ImportFrom validated
        # only the module, not the imported NAMES.
        ("from hypothesis import internal", "from hypothesis import internal"),
        ("ExampleDatabase file write",
         "from hypothesis.database import DirectoryBasedExampleDatabase\n"
         "DirectoryBasedExampleDatabase('/tmp/x').save(b'k', b'v')"),
        ("GitHubArtifactDatabase (network)",
         "from hypothesis.database import GitHubArtifactDatabase"),
        ("from json import decoder", "from json import decoder"),
    ]
    for label, code in blocked:
        try:
            screen_code(code); ok = False
        except ValueError:
            ok = True
        check(f"screen_code blocks: {label}", ok)
    for label, code in [
        ("plain strategy", "from hypothesis import strategies as st\nimport json\nstrategy=st.text()"),
        ("@composite", "from hypothesis import strategies as st\n"
                       "@st.composite\ndef f(draw): return draw(st.text())\nstrategy=f()"),
        ("st.recursive", "from hypothesis import strategies as st\nimport json\n"
                         "strategy=st.recursive(st.integers(), st.lists).map(json.dumps)"),
    ]:
        try:
            screen_code(code); ok = True
        except ValueError as e:
            ok = False
        check(f"screen_code ALLOWS legitimate: {label}", ok)

    # --- oracle robustness ---------------------------------------------------
    check("oracle survives deep nesting (no RecursionError escape)",
          oracle_accepts(b"[" * 20000 + b"]" * 20000) in (True, False))
    check("oracle: parson-lenient divergence detected",
          getattr(classify_divergence(True, b"1abc"), "kind", None) == "parson-lenient")
    check("oracle: agreement yields None", classify_divergence(True, b'{"a":1}') is None)

    # --- assignment constraints are actually the configured numbers ---------
    check("per-input timeout is 5s (assignment)", TIMEOUT_S == 5)
    check("examples per iteration is 500 (assignment)", EXAMPLES_PER_RUN == 500)
    budget = VALIDATE_EXAMPLES + len(PROBES) * PROBE_EXAMPLES_EACH
    check("gate+probes fit inside the 500 budget", budget < EXAMPLES_PER_RUN, str(budget))

    # --- StopController caps -------------------------------------------------
    sc = StopController(max_iters=2)
    check("iteration cap enforced",
          [sc.start_iteration() for _ in range(4)] == [True, True, False, False])
    sc2 = StopController(max_iters=5, max_usd=0.001)
    sc2.record_tokens(10_000_000, 10_000_000)
    check("budget cap enforced", sc2.start_iteration() is False)
    sc3 = StopController(max_iters=5, max_seconds=0)
    check("wall-clock cap enforced", sc3.start_iteration() is False)

    # --- probes generate what they claim ------------------------------------
    for name, strat in PROBES.items():
        ex = str(strat.example())
        if PROBE_EXPECT_VALID.get(name):
            ok = True
            try:
                json.loads(ex)
            except ValueError:
                ok = name in ("big_number",)  # may exceed double range by design
            check(f"probe '{name}' emits parseable JSON", ok, ex[:40])
    deep_ex = str(PROBES["deep_nesting"].example())
    check("deep_nesting probe targets the 2048 cap band",
          1500 <= _nesting_depth(deep_ex) <= 2100, str(_nesting_depth(deep_ex)))

    # --- summarize sanity ----------------------------------------------------
    summ = summarize([('{"a":[1]}', "valid"), ('{', "reject"), ('x', "skip")])
    check("summarize excludes skip from acceptance",
          summ["acceptance_rate"] == 0.5, str(summ["acceptance_rate"]))
    check("summarize reports productions", "object" in summ["productions_accepted"])

    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    raise SystemExit(main())
