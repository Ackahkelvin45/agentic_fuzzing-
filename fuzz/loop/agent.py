"""agent.py — the agentic loop (Step 4): seed -> validate -> run -> summarize
-> refine -> stop, bounded by the assignment's iteration/example/cost budget.

Runs with no API key via llm.py's MOCK mode (canned strategy, zero spend), or
against DeepSeek / any OpenAI-compatible provider once a key is set.

PROXY SIGNAL (blackbox — no coverage instrumentation is available):
  primary   production coverage, nesting depth (json-parser has no cap)
  repair    single-feature probes (probes.py) — unconfounded attribution
  objective unique crash signatures
  guardrail accepted-structure novelty (catches "acceptance up, diversity down")
Each maps to exactly ONE nameable edit; see decide_refinement and DECISIONS.md D5.

NOTE: json-parser's json_parse_ex returns an error string on rejection, which the
harness surfaces on stderr. A sample of those reasons is fed back to the model
(summarize's reject_reasons_sample, Step 4.4); the single-feature probes
(probes.py) still give the UNCONFOUNDED per-feature attribution the aggregate
error sample cannot.

DIFFERENTIAL ORACLE (oracle.py) runs every iteration as a FINDINGS channel (not
a steering one): inputs json-parser accepts but a strict parser rejects are candidate
leniency deviations.

SECURITY: run_live executes model-generated strategy code (unavoidable here).
screen_code() is an AST allowlist — a footgun-guard, NOT a sandbox (a determined
generator can still escape it). Review runs/iter-N/strategy.py and prefer a
throwaway environment.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import re
import sys
import time
import traceback
from collections import Counter
from pathlib import Path


class GeneratorError(Exception):
    """The model-produced strategy failed to import or errored while drawing.

    `localized` (when set) is a `strategy.py:LINE: <source>` pointer at the
    model-authored frame that raised — captured always, surfaced to the model
    only when LOCALIZE_ERRORS is on (so the plumbing fix and the localization
    experiment stay separable)."""

    def __init__(self, message: str, localized: str | None = None):
        super().__init__(message)
        self.localized = localized


# Experiment toggle (Path-B isolation). OFF by default: the A/C plumbing fixes
# surface a BARE exception (type + message), matching the "still bare errors"
# baseline. Set LOCALIZE_ERRORS=1 to also hand the model the file:line of the
# frame that raised, to test whether localization alone closes the gap.
LOCALIZE_ERRORS = os.environ.get("LOCALIZE_ERRORS") == "1"


def _localize_tb(exc: BaseException) -> str | None:
    """Deepest traceback frame that lives in a model-authored strategy file,
    as 'basename:LINE: <source>', else None. Used to point the model at the
    exact line it got wrong instead of a bare exception string."""
    hit = None
    for fs in traceback.extract_tb(exc.__traceback__):
        base = os.path.basename(fs.filename)
        if str(RUNS) in fs.filename or base.startswith("strategy"):
            hit = fs
    if hit is None:
        return None
    tail = f": {hit.line}" if hit.line else ""
    return f"{os.path.basename(hit.filename)}:{hit.lineno}{tail}"


def _fmt_gen_error(e: "GeneratorError | Exception") -> str:
    """The message to put in a repair prompt: bare by default; with the
    file:line appended when LOCALIZE_ERRORS is on and we have a location."""
    msg = str(e)
    loc = getattr(e, "localized", None)
    if LOCALIZE_ERRORS and loc:
        msg = f"{msg}  [raised at {loc}]"
    return msg


ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "runs"
GRAMMAR_FILE = ROOT / "grammar" / "JSON.g4"
ADAPT_FILE = ROOT / "grammar" / "adaptation-notes.md"
DEFAULT_HARNESS = ROOT / "build" / "harness"
MAX_ITERS = 5
MAX_USD = 5.0
# Assignment constraint: at most 500 Hypothesis examples per ITERATION. That
# budget must cover everything we push through the harness in one iteration —
# the acceptance gate, the optional repair check, the single-feature probes, and
# the main run — not just the main run. These are subtracted from it below.
EXAMPLES_PER_RUN = 500
VALIDATE_EXAMPLES = 30          # acceptance gate sample
PROBE_EXAMPLES_EACH = 10        # per single-feature probe

# Default token prices ($ per 1M tokens). These match DeepSeek's small/mid tier;
# override via llm.py env (LLM_PRICE_IN/OUT). Recorded in runs/cost.md.
PRICE_PER_MTOK = {"input": 0.27, "output": 1.10}


def _ensure_fuzz_on_path() -> None:
    """Put fuzz/, fuzz/loop and fuzz/triage on sys.path exactly once (idempotent).

    Previously each run_strategy call re-inserted, growing sys.path monotonically.
    """
    for p in (ROOT / "fuzz", ROOT / "fuzz" / "loop", ROOT / "fuzz" / "triage"):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


# --- stop conditions + cost accounting (REAL) -----------------------------

class StopController:
    def __init__(self, max_iters: int = MAX_ITERS, max_usd: float = MAX_USD,
                 price_in: float = PRICE_PER_MTOK["input"],
                 price_out: float = PRICE_PER_MTOK["output"],
                 max_seconds: float = 40 * 60):
        self.max_iters = max_iters
        self.max_usd = max_usd
        self.price_in = price_in
        self.price_out = price_out
        self.max_seconds = max_seconds
        self.deadline = time.monotonic() + max_seconds
        self.iters = 0
        self.tokens_in = 0
        self.tokens_out = 0

    @property
    def usd(self) -> float:
        return (self.tokens_in / 1e6 * self.price_in
                + self.tokens_out / 1e6 * self.price_out)

    def record_tokens(self, tin: int, tout: int) -> None:
        self.tokens_in += tin
        self.tokens_out += tout

    def start_iteration(self) -> bool:
        """Return True if another iteration is allowed; count it if so."""
        if (self.iters >= self.max_iters or self.usd >= self.max_usd
                or time.monotonic() >= self.deadline):
            return False
        self.iters += 1
        return True

    def reason(self) -> str:
        if self.iters >= self.max_iters:
            return f"iteration cap reached ({self.max_iters})"
        if self.usd >= self.max_usd:
            return f"budget cap reached (${self.usd:.2f} >= ${self.max_usd})"
        if time.monotonic() >= self.deadline:
            return f"wall-clock cap reached ({self.max_seconds/60:.0f} min)"
        return "converged / no more work"

    def cost_report(self) -> str:
        return (f"iterations={self.iters}  tokens_in={self.tokens_in}  "
                f"tokens_out={self.tokens_out}  est_cost=${self.usd:.4f}")


# --- proxy-signal summary (REAL) ------------------------------------------

def _nesting_depth(s: str) -> int:
    depth = mx = 0
    for c in s:
        if c in "[{":
            depth += 1
            mx = max(mx, depth)
        elif c in "]}":
            depth = max(0, depth - 1)
    return mx


_PRODS = ("object", "array", "string", "number", "true", "false", "null")

# json-parser has NO nesting cap (iterative parser), but deep OBJECT nesting
# stresses the first-pass memory measurement, so we still steer generation mass
# toward a deep band. 2048 is a convenient depth target, not a library limit.
CAP_DEPTH = 2048
CAP_BAND = (1500, CAP_DEPTH)


def _walk_types(o, acc: set[str]) -> set[str]:
    """Collect the JSON productions present in a decoded value.

    ITERATIVE on purpose: a recursive walk blew Python's stack on exactly the
    deeply-nested inputs the cap-distance signal steers toward, so those inputs
    silently contributed ZERO productions and collapsed into one novelty bucket
    — the steering signal was fighting itself.
    """
    stack = [o]
    while stack:
        cur = stack.pop()
        # bool is a subclass of int, so it must be checked first.
        if isinstance(cur, bool):
            acc.add("true" if cur else "false")
        elif cur is None:
            acc.add("null")
        elif isinstance(cur, str):
            acc.add("string")
        elif isinstance(cur, (int, float)):
            acc.add("number")
        elif isinstance(cur, dict):
            acc.add("object")
            stack.extend(cur.values())
        elif isinstance(cur, list):
            acc.add("array")
            stack.extend(cur)
    return acc


def _loads_deep(s: str):
    """json.loads that survives deeply-nested input.

    CPython's decoder recurses, so a 2000-deep array raises RecursionError. We
    raise the limit for the duration of the parse (and restore it), because the
    deep inputs are precisely what we must be able to measure.
    """
    old = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(max(old, 3 * CAP_DEPTH + 1000))
        return json.loads(s)
    finally:
        sys.setrecursionlimit(old)


def _productions_structural(s: str) -> set[str]:
    """Which grammar productions actually occur in a VALID JSON string, decided
    by parsing it — NOT substring matching (which counts 'null' inside any text
    and confounds the coverage signal)."""
    try:
        return _walk_types(_loads_deep(s), set())
    except (ValueError, RecursionError):
        return set()


def _structure_fingerprint(s: str) -> tuple:
    """A coarse structural identity of an ACCEPTED input, for novelty counting:
    (which productions present, depth band). Distinct fingerprints = diversity."""
    d = _nesting_depth(s)
    band = min(d // 4, 8)  # coarse depth bands so trivial jitter isn't 'novel'
    return (tuple(sorted(_productions_structural(s))), band)


def summarize(samples: list[tuple[str, str]], divergences: list | None = None,
              crash_signatures: list[str] | None = None,
              reject_reasons: "Counter | None" = None) -> dict:
    """samples: list of (generated_input, outcome_value). Returns the proxy signal.

    Primary steering terms (unconfounded, need no parser feedback):
      - productions_accepted : productions that appeared AND parsed valid
      - cap_distance_mass    : fraction of accepted inputs in the deep band [1500,2048]
      - novelty              : count of distinct accepted structure fingerprints
                               (the quiet-failure guardrail co-metric)
    `reject_reasons` (Step 4.4) is a Counter of json-parser's error strings; the
    top few are surfaced so the LLM sees WHY inputs are being rejected.
    """
    outcomes = Counter(o for _, o in samples)
    non_skip = sum(v for k, v in outcomes.items() if k != "skip")
    accept = outcomes.get("valid", 0)

    accepted = [s for s, o in samples if o == "valid"]
    depths_all = [_nesting_depth(s) for s, _ in samples]
    accepted_depths = [_nesting_depth(s) for s in accepted]

    prod_accepted = set().union(*[_productions_structural(s) for s in accepted]) if accepted else set()
    in_band = sum(1 for d in accepted_depths if CAP_BAND[0] <= d <= CAP_BAND[1])
    novelty = len({_structure_fingerprint(s) for s in accepted})

    div = divergences or []
    return {
        "outcomes": dict(outcomes),
        "acceptance_rate": round(accept / non_skip, 3) if non_skip else 0.0,
        "max_nesting_depth": max(depths_all) if depths_all else 0,
        "productions_accepted": sorted(prod_accepted),
        # steer on productions that were GENERATED but never ACCEPTED, plus any
        # never generated at all — both are coverage gaps to close.
        "productions_gap": [p for p in _PRODS if p not in prod_accepted],
        "cap_distance_mass": round(in_band / len(accepted), 3) if accepted else 0.0,
        "novelty": novelty,
        "divergences": len(div),
        "divergence_examples": [d.input_repr for d in div[:3]],
        # Step 4.4: the LLM must see which unique crashes exist so far, so it can
        # steer toward that region instead of rediscovering the same bug.
        "unique_crash_signatures": sorted(crash_signatures or []),
        # Step 4.4: a sample of WHY inputs were rejected (top reasons + counts),
        # so the model can steer away from productions it keeps getting wrong.
        "reject_reasons_sample": (reject_reasons.most_common(5)
                                  if reject_reasons else []),
    }


# --- actionability: one summary -> exactly ONE nameable strategy edit ---------

# Thresholds are deliberately explicit so the mapping is auditable, not vibes.
CAP_MASS_TARGET = 0.05     # want >=5% of accepted inputs near the nesting cap
PROBE_FAIL_RATE = 0.20     # a should-be-valid probe accepted <20% => broken feature
# Quiet-failure revert margins. The guardrail used to fire on ANY novelty drop;
# characterization showed novelty has a ~1.5 per-run std (so a two-measurement
# difference has ~2.1 std), which made a 1-2 point jitter trip a spurious
# revert — it fired on noise in the recorded run. Require BOTH legs to be real
# moves: acceptance up by > ACC margin AND novelty down by > ~2 sigma. (A fully
# stable signal needs averaging over seeds — deferred as future work.)
NOVELTY_REVERT_MARGIN = 4    # novelty must fall by more than this (~2 sigma)
ACC_REVERT_MARGIN = 0.05     # and acceptance must rise by more than this


def decide_refinement(summary: dict,
                      prev: dict | None = None,
                      probe_acceptance: dict | None = None,
                      new_crash: bool = False) -> tuple[str, str]:
    """Return (action, detail): the single, mechanical change for the next
    iteration. Priority is fixed so exactly one edit happens per iteration (the
    critic's 'sequence, don't parallelize' requirement), and every branch names
    a distinct action — no branch emits a vague 'make it better'.
    """
    # 0. Guardrail (highest priority): quiet failure = acceptance up but the
    #    diversity of accepted structures collapsed -> we're generating blander
    #    JSON and retreating from edge cases. Revert the last edit.
    #    `if prev` (not `is not None`): a gated iteration can hand us {}, which
    #    would pass an is-not-None check and then KeyError on the lookups.
    if prev and "acceptance_rate" in prev and "novelty" in prev:
        acc_rose = summary["acceptance_rate"] - prev["acceptance_rate"] > ACC_REVERT_MARGIN
        novelty_fell = prev["novelty"] - summary["novelty"] > NOVELTY_REVERT_MARGIN
        if acc_rose and novelty_fell:
            return ("revert_last_edit",
                    f"acceptance {prev['acceptance_rate']}->{summary['acceptance_rate']} "
                    f"but novelty {prev['novelty']}->{summary['novelty']} (quiet failure)")

    # 1. Objective: a fresh crash dominates everything else.
    if new_crash:
        return ("intensify_near_crash", "mutate structurally around the crashing input")

    # 2. Primary steer: close a production coverage gap (unconfounded).
    gap = summary.get("productions_gap", [])
    if gap:
        return ("add_or_upweight_production", gap[0])

    # 3. Primary steer: push nesting depth into the deep band (stresses first pass).
    if summary.get("cap_distance_mass", 0.0) < CAP_MASS_TARGET:
        return ("generate_nesting_toward_cap", f"target depth {CAP_BAND[0]}-{CAP_BAND[1]}")

    # 4. Repair (only via single-feature probes -> unambiguous attribution).
    if probe_acceptance:
        from probes import PROBE_EXPECT_VALID  # noqa: E402
        for name, rate in sorted(probe_acceptance.items()):
            if PROBE_EXPECT_VALID.get(name) and rate < PROBE_FAIL_RATE:
                return ("fix_feature_encoding", f"{name} probe accepted only {rate:.0%}")

    # 5. Nothing indicated: broaden exploration.
    return ("broaden_exploration", "coverage saturated; diversify accepted structures")


# --- strategy loading + a run (REAL) --------------------------------------

def load_strategy(strategy_path: Path):
    spec = importlib.util.spec_from_file_location("evolved_strategy", strategy_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load strategy from {strategy_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "strategy"):
        raise AttributeError(f"{strategy_path} must define a top-level `strategy`")
    return mod.strategy


def _reject_reason(stderr: bytes) -> str:
    """Pull json-parser's error string out of the harness stderr ('reject: <msg>').
    Empty when the parser gave no message (or the outcome was not a rejection)."""
    if not stderr:
        return ""
    lines = stderr.decode("utf-8", "replace").strip().splitlines()
    if not lines:
        return ""
    last = lines[-1]
    reason = last[len("reject: "):] if last.startswith("reject: ") else last
    return reason.strip()[:120]


def run_strategy(harness: Path, strategy, max_examples: int,
                 run_deadline_s: float = 600.0, log_path: Path | None = None,
                 run_seed: int | None = None):
    """Execute `strategy` through the harness.

    Returns (samples, divergences): samples is [(input, outcome)]; divergences
    is the list of differential-oracle disagreements (json-parser vs. strict json)
    collected on the same pass, so the oracle stays wired into every run.

    Raises GeneratorError if the strategy errors while drawing (e.g. produces an
    invalid strategy, raises on first example) — so one bad generator fails just
    that iteration instead of aborting the whole loop. `run_deadline_s` is a
    per-run wall-clock backstop (default 10 min, per the assignment) after which
    remaining draws are skipped so a pathological generator can't run forever.

    `run_seed` fixes Hypothesis's PRNG so a run is reproducible (same seed ->
    same draws). None keeps it random — the right default for a FUZZER, which
    wants input diversity across runs. Measurement/CI paths pass an explicit
    seed and vary it to report mean+/-std instead of trusting one noisy draw.
    The example DATABASE is always disabled here: a persisted example store made
    runs stateful, so a strategy's score depended on what earlier runs had saved
    — a hidden confound behind the un-reproducible trajectory the critic found.
    """
    _ensure_fuzz_on_path()
    from runner import run_once, Outcome  # noqa: E402
    from oracle import classify_divergence  # noqa: E402
    from hypothesis import given, settings, seed as hyp_seed, HealthCheck
    from hypothesis.errors import FlakyFailure

    samples: list[tuple[str, str]] = []
    divergences: list = []
    crash_inputs: list[bytes] = []  # includes Hypothesis's shrink attempts
    reject_reasons: Counter = Counter()  # json-parser's error strings (Step 4.4)
    log_records: list[dict] | None = [] if log_path is not None else None
    start = time.monotonic()
    truncated = [False]

    @settings(max_examples=max_examples, deadline=None, database=None,
              suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(s=strategy)
    def _t(s) -> None:
        if time.monotonic() - start > run_deadline_s:
            truncated[0] = True
            return  # backstop: stop expensive work; let Hypothesis wind down
        text = s if isinstance(s, str) else repr(s)
        data = s if isinstance(s, bytes) else str(s).encode("utf-8", "surrogatepass")
        r = run_once(harness, data)
        samples.append((text, r.outcome.value))
        reason = _reject_reason(r.stderr) if r.outcome is Outcome.REJECT else ""
        if reason:
            reject_reasons[reason] += 1
        if log_records is not None:
            # Assignment Step 4.3: log PER INPUT — crash/no-crash, the sanitizer
            # output when it crashed, and the exit code / parser error otherwise.
            rec = {
                "input": text[:2000],
                "outcome": r.outcome.value,
                "exit_code": r.exit_code,
                "signal": r.term_signal,
                "timed_out": r.timed_out,
                "detail": r.detail,
            }
            if r.outcome is Outcome.CRASH and r.stderr:
                rec["sanitizer_output"] = r.stderr.decode("utf-8", "replace")[:4000]
            elif reason:
                rec["reject_reason"] = reason  # json-parser's "why rejected" string
            log_records.append(rec)
        if r.outcome in (Outcome.VALID, Outcome.REJECT):
            d = classify_divergence(r.outcome is Outcome.VALID, data)
            if d is not None:
                divergences.append(d)
        if r.outcome is Outcome.CRASH:
            if len(crash_inputs) < 100:
                crash_inputs.append(data)
            # Raising here makes Hypothesis shrink the crashing input for us.
            raise AssertionError(f"crash: {r.signature_source()}")

    runnable = hyp_seed(run_seed)(_t) if run_seed is not None else _t
    try:
        runnable()
    except AssertionError as e:
        print(f"[loop] crash surfaced: {e}")
    except FlakyFailure as e:
        # A crash that doesn't reproduce identically during shrinking raises
        # FlakyFailure, not AssertionError. Timeouts are the flakiest crash
        # class and the assignment counts them as crashes — so this must NOT be
        # mistaken for a broken generator, or we'd silently discard real bugs.
        print(f"[loop] flaky crash surfaced (kept): {str(e)[:120]}")
    except Exception as e:  # noqa: BLE001 - a bad generator must not kill the loop
        raise GeneratorError(f"{type(e).__name__}: {e}"[:200],
                             localized=_localize_tb(e)) from e
    if truncated[0]:
        print(f"[loop] run hit {run_deadline_s/60:.0f}-min backstop; truncated")
    if log_path is not None and log_records is not None:
        with log_path.open("w") as fh:
            for rec in log_records:
                fh.write(json.dumps(rec) + "\n")
    return samples, divergences, crash_inputs, reject_reasons


def run_probes(harness: Path, per_probe: int = 20) -> dict[str, dict]:
    """Measure each SINGLE-FEATURE probe against BOTH json-parser and the strict
    oracle -> unconfounded repair signal. Returns
    {probe_name: {"target": rate, "oracle": rate}}. Real; needs a built harness.

    Two rates are needed to disambiguate a low target-acceptance:
      target low + oracle low  -> the GENERATOR emits malformed feature (repair)
      target low + oracle high -> json-parser DEVIATION (finding), not a generator bug
    """
    _ensure_fuzz_on_path()
    from runner import run_once  # noqa: E402
    from oracle import oracle_accepts  # noqa: E402
    from probes import PROBES  # noqa: E402
    from hypothesis import given, settings, HealthCheck

    rates: dict[str, dict] = {}
    for name, strat in PROBES.items():
        counts = Counter()
        oracle_ok = [0, 0]  # [accepted, total]

        @settings(max_examples=per_probe, deadline=None,
                  suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(s=strat)
        def _t(s) -> None:
            data = str(s).encode("utf-8", "surrogatepass")
            r = run_once(harness, data)
            counts[r.outcome.value] += 1
            oracle_ok[1] += 1
            oracle_ok[0] += oracle_accepts(data)

        _t()
        non_skip = sum(v for k, v in counts.items() if k != "skip")
        rates[name] = {
            "target": round(counts.get("valid", 0) / non_skip, 3) if non_skip else 0.0,
            "oracle": round(oracle_ok[0] / oracle_ok[1], 3) if oracle_ok[1] else 0.0,
        }
    return rates


def classify_probes(rates: dict[str, dict]) -> tuple[dict[str, float], dict[str, dict]]:
    """Split probe results into (generator_broken, target_deviations).

    generator_broken: {name: target_rate}   -> feed to decide_refinement (repair)
    target_deviations: {name: {...}}         -> findings (json-parser vs RFC)
    """
    from probes import PROBE_EXPECT_VALID  # noqa: E402
    broken: dict[str, float] = {}
    deviations: dict[str, dict] = {}
    for name, r in rates.items():
        if not PROBE_EXPECT_VALID.get(name):
            continue  # near_valid_malformed is meant to be rejected
        if r["target"] < PROBE_FAIL_RATE:
            if r["oracle"] >= 0.5:
                deviations[name] = r          # json-parser vs strict oracle = deviation
            else:
                broken[name] = r["target"]    # generator emits malformed = repair
    return broken, deviations


# --- LLM stages (REAL) ----------------------------------------------------

SYSTEM_MSG = (
    "You are an expert in property-based testing with Python's Hypothesis "
    "library. You write strategies that generate STRINGS in a target text "
    "format for fuzzing a C parser. Output ONLY one Python code block and no "
    "prose. The module MUST define a top-level name `strategy` that is a "
    "Hypothesis SearchStrategy producing `str` values. You may import "
    "`from hypothesis import strategies as st` and `import json` only — no "
    "other imports, no file/network/OS access."
)

_REQUIREMENTS = """\
Requirements for the `strategy`:
- Generate strings in the JSON grammar below. Use st.recursive (or @st.composite)
  for the recursive obj/arr productions — do NOT flatten to a big random string.
- Deliberately cover edge cases: empty {} and [], deep nesting, duplicate keys,
  extreme numeric magnitudes/exponents (including overflow like 1e309), \\uXXXX
  and surrogate escapes, embedded NUL/control bytes, and near-valid-but-malformed
  inputs (double commas, missing colons, unterminated strings, leading +/.5/01).
- Respect the json-parser realities in the adaptation notes: there is NO nesting
  cap (vary depth freely), and bias toward OBJECTS WITH MEMBERS (the stressed
  first-pass path). Emitting a raw NUL byte is allowed and encouraged — the
  harness tests it faithfully.
- NOTE: json-parser ACCEPTS duplicate keys, a single trailing comma, non-finite
  numbers, and a lone `-`. These are still valuable edge inputs; expect them to
  be accepted rather than treating acceptance as a defect.
- The output must be `str` (the candidate JSON text; NUL/control chars are ok).
- API pitfall: `st.tuples(a, b)` DRAWS TUPLES, not strings. Always `.map` a tuple
  to a joined string before combining; never pass tuples into `",".join(...)`."""


def seed_prompt(prev_error: str | None = None) -> list[dict]:
    grammar = GRAMMAR_FILE.read_text()
    notes = ADAPT_FILE.read_text()
    # Path-A fix: a build-rejected attempt with no working baseline still
    # re-seeds from the grammar, but must NOT re-seed blind — feed the previous
    # failure back so the model stops repeating it (e.g. a hallucinated
    # `st.json_strings`). This is the model's own error, not a hint at the answer.
    retry = ""
    if prev_error:
        retry = ("\n\n=== your previous attempt was REJECTED before it ran ===\n"
                 f"{prev_error}\n"
                 "Do not repeat this failure. Use only real Hypothesis APIs and "
                 "keep the module internally consistent.")
    user = (f"{_REQUIREMENTS}\n\n=== JSON grammar (ANTLR) ===\n{grammar}\n\n"
            f"=== json-parser adaptation notes ===\n{notes}{retry}\n\n"
            f"Return the full module as one ```python code block.")
    return [{"role": "system", "content": SYSTEM_MSG},
            {"role": "user", "content": user}]


def refine_prompt(current_code: str, summary: dict, action: tuple[str, str]) -> list[dict]:
    act, detail = action
    user = (f"Here is the current Hypothesis strategy:\n\n```python\n{current_code}\n```\n\n"
            f"Here is the summary of its last run against the parser "
            f"(no coverage data is available):\n{json.dumps(summary, indent=2)}\n\n"
            f"Make EXACTLY ONE change to the strategy: **{act}** — {detail}.\n"
            f"Change nothing else. Keep it a valid Hypothesis strategy producing "
            f"`str`. Return the full revised module as one ```python code block.")
    return [{"role": "system", "content": SYSTEM_MSG},
            {"role": "user", "content": user}]


def call_llm(messages: list[dict], stop: StopController, config) -> str:
    from llm import chat  # noqa: E402
    text, tin, tout = chat(messages, config)
    stop.record_tokens(tin, tout)
    return text


_CODE_FENCE = re.compile(r"```([A-Za-z0-9_+-]*)\s*\n(.*?)```", re.DOTALL)
_PY_TAGS = ("python", "py", "python3", "")


def extract_strategy(text: str) -> str:
    """Pull the python code block from a model response, else the raw text.

    Prefers the LONGEST python-tagged block. Taking the *last* fence (the old
    behaviour) grabbed a trailing ```bash/```text block when the model appended
    usage notes, producing a SyntaxError and burning a paid iteration. The
    language tag is also stripped — it used to leak in as a first line.
    """
    blocks = [(tag.lower(), body) for tag, body in _CODE_FENCE.findall(text)]
    if not blocks:
        return text.strip()
    py = [b for tag, b in blocks if tag in _PY_TAGS]
    chosen = max(py, key=len) if py else max((b for _, b in blocks), key=len)
    return chosen.strip()


# Footgun-guard (NOT a sandbox): AST allowlist. A well-formed strategy needs
# only `hypothesis` and `json`, no dynamic-exec builtins, no dunder-attribute
# tricks. Stronger than a substring denylist (which `from os import system`
# slips past), though still defeatable by a determined adversary — run the live
# loop in a throwaway environment regardless.
_ALLOWED_IMPORT_ROOTS = {"hypothesis", "json"}
# Submodules of an ALLOWED root that re-export dangerous modules. Without this,
# `hypothesis.internal.escalation.os.system(...)` passes an import-root-only
# allowlist — demonstrated executing during review.
_BLOCKED_SUBMODULES = {"internal", "extra", "vendor", "encoder", "decoder",
                       "scanner", "database", "control", "configuration"}
# Names that are dangerous to import even from an allowed module (they touch the
# filesystem or network at construction/import time).
_BLOCKED_IMPORT_NAMES = {
    "DirectoryBasedExampleDatabase", "GitHubArtifactDatabase",
    "ReadOnlyDatabase", "MultiplexedDatabase", "BackgroundWriteDatabase",
    "settings", "Verbosity", "Phase", "target", "note", "event",
}
_BANNED_ATTRS = {"os", "sys", "subprocess", "socket", "shutil", "builtins",
                 "pathlib", "importlib", "popen", "system", "spawn", "fork",
                 "environ", "getenv", "putenv", "remove", "unlink", "rmtree"}
_BANNED_NAMES = {"eval", "exec", "__import__", "compile", "open", "globals",
                 "locals", "vars", "getattr", "setattr", "delattr", "input",
                 "breakpoint", "memoryview", "classmethod", "staticmethod"}


def screen_code(code: str) -> None:
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise ValueError(f"strategy does not parse: {e}") from e
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                parts = n.name.split(".")
                if parts[0] not in _ALLOWED_IMPORT_ROOTS:
                    raise ValueError(f"disallowed import: {n.name}")
                if any(p in _BLOCKED_SUBMODULES for p in parts[1:]):
                    raise ValueError(f"disallowed submodule import: {n.name}")
        elif isinstance(node, ast.ImportFrom):
            parts = (node.module or "").split(".")
            if parts[0] not in _ALLOWED_IMPORT_ROOTS:
                raise ValueError(f"disallowed import-from: {node.module}")
            if any(p in _BLOCKED_SUBMODULES for p in parts[1:]):
                raise ValueError(f"disallowed submodule import-from: {node.module}")
            # The IMPORTED NAMES must be screened too: validating only the module
            # let `from hypothesis import internal` (and `from hypothesis.database
            # import DirectoryBasedExampleDatabase`, which writes files at import
            # time) through — found by audit after the first hardening pass.
            for n in node.names:
                if n.name in _BLOCKED_SUBMODULES or n.name in _BANNED_ATTRS:
                    raise ValueError(f"disallowed imported name: {n.name}")
                if n.name in _BLOCKED_IMPORT_NAMES:
                    raise ValueError(f"disallowed imported name: {n.name}")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__"):
                raise ValueError(f"dunder attribute access: {node.attr}")
            if node.attr in _BANNED_ATTRS or node.attr in _BLOCKED_SUBMODULES:
                raise ValueError(f"disallowed attribute access: .{node.attr}")
        elif isinstance(node, ast.Name) and node.id in _BANNED_NAMES:
            raise ValueError(f"disallowed name: {node.id}")


def validate_generator(strategy, harness: Path, n: int = 40,
                       run_seed: int | None = None) -> float:
    """Acceptance sanity-check: run a small sample and return acceptance rate.
    A near-zero rate means the generator is being rejected at the front door."""
    samples, _, _, _ = run_strategy(harness, strategy, n, run_seed=run_seed)
    return summarize(samples)["acceptance_rate"]


ACCEPT_GATE = 0.05  # below this the generator is 'rejected at the front door'
# Validate across several PRNG seeds, not one. Characterization found a
# committed strategy that ran fine on its (lucky) original draw but RAISES on
# other seeds — a single-seed gate passed it. Each seed gets the FULL validate
# sample (measured: ~20+ draws/seed are needed to surface that particular
# fragility; a thinner split missed it). This reduces — not eliminates — the
# chance a seed-fragile strategy slips through, at a fixed, budgeted cost.
GATE_SEEDS = (0, 1, 2)
GATE_COST = len(GATE_SEEDS) * VALIDATE_EXAMPLES  # examples the gate spends/iter


def _score(summary: dict) -> float:
    """Higher is better: reward coverage breadth, accepted-structure novelty, and
    depth mass in the deep-nesting band.

    Deliberately does NOT encode "a crash happened". An earlier version returned
    1e6 on a crash, which made the tolerance band (10% of best) ~1e5 — so every
    later iteration counted as a regression and the loop froze on the crashing
    strategy. Crash dominance is handled as a separate flag by the caller.
    """
    return (len(summary.get("productions_accepted", [])) * 5
            + summary.get("novelty", 0)
            + summary.get("cap_distance_mass", 0.0) * 20)


def build_strategy(messages: list[dict], idir: Path, tag: str, stop, config):
    """LLM -> extract -> screen -> import. Commits response/strategy files.
    Raises ValueError (unparseable/screened code) or GeneratorError (import)."""
    text = call_llm(messages, stop, config)
    (idir / f"response{tag}.md").write_text(text)
    code = extract_strategy(text)
    screen_code(code)  # raises ValueError
    (idir / f"strategy{tag}.py").write_text(code)
    try:
        strategy = load_strategy(idir / f"strategy{tag}.py")
    except Exception as e:  # noqa: BLE001
        raise GeneratorError(f"import: {type(e).__name__}: {e}"[:200],
                             localized=_localize_tb(e)) from e
    return code, strategy


def safe_validate(strategy, harness: Path, n: int = 40):
    """Acceptance sanity-check that never raises, across several PRNG seeds so a
    SEED-FRAGILE strategy (runs on one lucky draw, raises/collapses on others)
    can't slip through the gate. Returns (mean_rate, error): error is set if the
    generator raised on ANY seed. The example budget is split across seeds, so
    the gate's cost is a fixed GATE_COST examples (accounted for in run_live).

    Aiming the repair at the REAL exception (rather than misreading a throw as a
    low acceptance rate) is what stops the model chasing 'emit more valid JSON'
    when the code was actually crashing during generation."""
    rates = []
    for sd in GATE_SEEDS:
        try:
            rates.append(validate_generator(strategy, harness, n=n, run_seed=sd))
        except GeneratorError as e:
            return 0.0, e  # fragile on even one seed => disqualify, route the fix
    return sum(rates) / len(rates), None


def run_live(harness: Path, max_examples: int) -> int:
    """The real agentic loop. Commits every iteration's prompt/response/strategy/
    stats to runs/iter-N/, and writes runs/evolution.md + runs/cost.md.
    Runs offline (MOCK) with no key, or against DeepSeek when a key is set."""
    _ensure_fuzz_on_path()
    from llm import LLMConfig  # noqa: E402
    from runner import assert_real_target  # noqa: E402

    # Refuse to gather "findings" from the positive-control (or roundtrip) build:
    # the guard is only meaningful if the real run path calls it.
    assert_real_target(harness)

    config = LLMConfig.from_env()
    stop = StopController(price_in=config.price_in, price_out=config.price_out)
    RUNS.mkdir(exist_ok=True)
    print(f"[live] model: {config.describe()}  (max_iters={stop.max_iters}, "
          f"budget=${stop.max_usd}, wall={stop.max_seconds/60:.0f}min)")

    evolution: list[str] = []
    known_signatures: set[str] = set()
    prev_summary: dict | None = None
    current_code: str | None = None
    best_code: str | None = None
    best_summary: dict | None = None
    best_score = float("-inf")
    action: tuple[str, str] = ("seed", "initial seed from grammar")
    last_build_error: str | None = None  # Path-A: carried into the next re-seed

    while stop.start_iteration():
        it = stop.iters
        idir = RUNS / f"iter-{it}"
        idir.mkdir(parents=True, exist_ok=True)

        # Re-seed whenever we have no usable strategy yet. Keying on `it == 1`
        # alone meant a failed first iteration sent the model the literal text
        # "None" as the current strategy, silently burning every later iteration.
        # Path-A fix: when re-seeding after a build rejection, pass the previous
        # error in — do NOT gate feedback on having a non-None baseline, which
        # used to discard the error and re-send a byte-identical blind prompt.
        if current_code is None:
            messages = seed_prompt(last_build_error)
        else:
            messages = refine_prompt(current_code, prev_summary or {}, action)
        (idir / "prompt.md").write_text(messages[-1]["content"])

        # --- obtain a usable strategy (F1/F2: never let a bad one kill/burn) ---
        try:
            code, strategy = build_strategy(messages, idir, "", stop, config)
        except (ValueError, GeneratorError) as e:
            detail = _fmt_gen_error(e)
            print(f"[live] iter {it}: strategy rejected ({detail}); rolling back")
            action = ("fix_generator_error", detail)
            last_build_error = detail            # so the next re-seed isn't blind
            current_code = best_code or current_code  # don't advance on garbage
            evolution.append(f"iter {it}: build rejected ({detail}) -> rollback")
            continue
        last_build_error = None  # a clean build clears the carried error

        # Budget the assignment's 500-examples-per-iteration across every
        # harness-facing sample this iteration makes.
        from probes import PROBES  # noqa: E402
        probe_budget = len(PROBES) * PROBE_EXAMPLES_EACH
        main_budget = max(1, max_examples - GATE_COST - probe_budget)

        acc0, gen_err = safe_validate(strategy, harness, n=VALIDATE_EXAMPLES)
        if acc0 < ACCEPT_GATE:
            main_budget = max(1, main_budget - GATE_COST)  # repair re-validate
            # F2: gate, don't just warn — one in-iteration repair re-prompt.
            # Path-C fix: if the generator RAISED, aim the repair at that real
            # exception; only truly-low acceptance gets the 'emit valid JSON'
            # nudge. Previously every sub-gate result was reported to the model
            # as "0% accepted", so an exception was mis-repaired as bad output.
            if gen_err is not None:
                detail = _fmt_gen_error(gen_err)
                print(f"[live] iter {it}: generator raised ({detail}); one repair re-prompt")
                fix_summary = {"generator_error": str(gen_err)}
                fix_action = ("fix_generator_error", detail)
            else:
                print(f"[live] iter {it}: acceptance {acc0:.0%} < gate; one repair re-prompt")
                fix_summary = {"validate_acceptance": acc0}
                fix_action = ("raise_acceptance",
                              f"only {acc0:.0%} accepted — emit mostly VALID JSON")
            fix_msgs = refine_prompt(code, fix_summary, fix_action)
            try:
                code2, strat2 = build_strategy(fix_msgs, idir, "_fix", stop, config)
                acc2, gen_err2 = safe_validate(strat2, harness, n=VALIDATE_EXAMPLES)
                if acc2 >= acc0:
                    code, strategy, acc0, gen_err = code2, strat2, acc2, gen_err2
            except (ValueError, GeneratorError) as e:
                print(f"[live] iter {it}: repair failed ({_fmt_gen_error(e)})")
            if acc0 < ACCEPT_GATE:  # still bad: skip the expensive full run
                (idir / "stats.json").write_text(json.dumps(
                    {"validate_acceptance": acc0, "gated": True,
                     "generator_error": str(gen_err) if gen_err else None}, indent=2))
                action = (("fix_generator_error", _fmt_gen_error(gen_err)) if gen_err
                          else ("raise_acceptance", f"still {acc0:.0%} after one repair"))
                # Keep prev_summary as-is (possibly None). Substituting {} here
                # used to pass decide_refinement's `is not None` guard and then
                # KeyError, killing the run before cost.md was ever written.
                current_code = code
                evolution.append(f"iter {it}: gated at acc={acc0:.2f}; full run skipped")
                continue

        # --- full run (F1: run_strategy may raise GeneratorError) ---
        try:
            samples, divergences, crash_inputs, reject_reasons = run_strategy(
                harness, strategy, main_budget,
                run_deadline_s=min(600.0, max(1.0, stop.deadline - time.monotonic())),
                log_path=idir / "per_input_log.jsonl")
        except GeneratorError as e:
            detail = _fmt_gen_error(e)
            print(f"[live] iter {it}: generator errored mid-run ({detail}); rolling back")
            action = ("fix_generator_error", detail)
            current_code = best_code or code
            evolution.append(f"iter {it}: mid-run error ({detail}) -> rollback")
            continue

        summary = summarize(samples, divergences, sorted(known_signatures),
                            reject_reasons=reject_reasons)
        probe_rates = run_probes(harness, per_probe=PROBE_EXAMPLES_EACH)
        broken, deviations = classify_probes(probe_rates)
        new_crash = "crash" in summary["outcomes"]
        sc = _score(summary)

        (idir / "stats.json").write_text(json.dumps({
            # Recorded FIRST and in every iteration file: a MOCK run must never
            # be mistakable for a real LLM run by anyone reading these artifacts.
            "mode": "MOCK" if config.mock else config.model,
            "validate_acceptance": acc0,
            "summary": summary,
            "probes": probe_rates,
            "target_deviations": deviations,
            "new_crash": new_crash,
            "score": sc,
        }, indent=2))
        if config.mock:
            (idir / "MOCK_RUN.md").write_text(
                "# MOCK run — not a real LLM run\n\n"
                "These artifacts were produced with `LLM_MOCK=1` (no API key, no\n"
                "spend). The strategy is a fixed canned generator, so it is\n"
                "IDENTICAL across iterations and shows the loop's *shape*, not\n"
                "real strategy evolution. Real runs record the model name in\n"
                "`stats.json:mode` and a non-zero token count in `runs/cost.md`.\n")

        # --- F3: real regression rollback (catches 'both down' too) ---
        # Tolerance band: only roll back on a MEANINGFUL drop, so run-to-run
        # novelty jitter doesn't cause spurious churn. Crash dominance is a
        # separate flag, NOT a huge score — folding it into the scalar made the
        # tolerance ~1e5 and froze the loop on the first crashing strategy.
        drop = best_score - sc
        regressed = (best_code is not None and not new_crash
                     and drop > max(2.0, 0.10 * abs(best_score)))

        # Decide the next edit BEFORE mutating `best_*`, so a guardrail-issued
        # revert still has the previous best to roll back to (it used to be
        # overwritten one line earlier, making the revert a silent no-op).
        if regressed:
            action = ("revert_last_edit", f"score {sc:.1f} < best {best_score:.1f}")
        else:
            action = decide_refinement(summary, prev_summary, broken, new_crash)

        if action[0] == "revert_last_edit" and best_code is not None:
            current_code, prev_summary = best_code, best_summary
        else:
            current_code, prev_summary = code, summary

        # `best` must only ever improve. Previously it was overwritten every
        # non-regressed iteration, so the score ratcheted DOWN (57->54->51...)
        # while each individual drop stayed inside the tolerance band.
        if new_crash or sc > best_score:
            best_code, best_summary, best_score = code, summary, sc

        line = (f"iter {it}: acc={summary['acceptance_rate']} "
                f"novelty={summary['novelty']} cap_mass={summary['cap_distance_mass']} "
                f"score={sc:.1f} crash={new_crash} deviations={list(deviations)} "
                f"{'REGRESSION->rollback' if regressed else '->'} "
                f"next: {action[0]} ({action[1]})")
        print(f"[live] {line}")
        evolution.append(line)
        if new_crash and crash_inputs:
            _ensure_fuzz_on_path()
            from triage import triage as run_triage  # noqa: E402
            grouped = run_triage(crash_inputs, harness)
            known_signatures.update(grouped.keys())
            summary["unique_crash_signatures"] = sorted(known_signatures)
            (idir / "stats.json").write_text(json.dumps({
                "mode": "MOCK" if config.mock else config.model,
                "validate_acceptance": acc0, "summary": summary,
                "probes": probe_rates, "target_deviations": deviations,
                "new_crash": new_crash, "score": sc,
            }, indent=2))
            (idir / "triage.json").write_text(json.dumps(grouped, indent=2))
            print(f"[live] CRASH triaged -> {len(grouped)} unique signature(s); "
                  f"see crashes/ and runs/iter-{it}/triage.json")

    banner = ("> **MOCK RUN — NOT A REAL LLM RUN.** Produced with `LLM_MOCK=1`: the "
              "strategy is a fixed canned generator, identical every iteration, so "
              "this shows the loop's *shape*, not real strategy evolution. "
              "No API calls, no spend.\n\n") if config.mock else ""
    (RUNS / "evolution.md").write_text(
        "# Strategy evolution\n\n" + banner
        + "\n".join(f"- {l}" for l in evolution) + "\n")
    (RUNS / "cost.md").write_text(
        f"# Cost accounting\n\nmodel: {config.describe()}\n\n{stop.cost_report()}\n\n"
        f"stop reason: {stop.reason()}\n")
    print(f"[live] stop: {stop.reason()}")
    print(f"[live] cost: {stop.cost_report()}")
    return 0


# --- offline replay (REAL) ------------------------------------------------

def run_replay(harness: Path, iter_dir: Path, max_examples: int) -> int:
    strategy = load_strategy(iter_dir / "strategy.py")
    samples, divergences, _, rr = run_strategy(harness, strategy, max_examples)
    print(f"[replay] {iter_dir.name}: {json.dumps(summarize(samples, divergences, reject_reasons=rr))}")
    return 0


# --- dry-run: full loop shape on canned data, no API (REAL) ----------------

def run_dry(harness: Path, max_examples: int) -> int:
    """Exercise seed -> validate -> run -> summarize -> refine -> stop end-to-end
    using a canned strategy and a no-op refine, so the shape is demonstrably
    runnable before the model is wired in."""
    from hypothesis import strategies as st

    # "Seed": a canned recursive JSON-ish strategy stands in for the LLM output.
    canned = st.recursive(
        st.one_of(st.integers(), st.booleans(), st.none(),
                  st.text(max_size=5)),
        lambda kids: st.one_of(st.lists(kids, max_size=3),
                               st.dictionaries(st.text(max_size=3), kids, max_size=3)),
        max_leaves=8,
    ).map(_to_jsonish)

    stop = StopController(max_iters=2, max_usd=MAX_USD)  # small cap for a demo
    current = "canned_seed_strategy"
    while stop.start_iteration():
        print(f"[dry-run] --- iteration {stop.iters} (strategy={current}) ---")
        samples, divergences, _, rr = run_strategy(harness, canned, max_examples)
        summary = summarize(samples, divergences, reject_reasons=rr)
        # "validate": acceptance sanity check
        if summary["acceptance_rate"] < 0.05:
            print(f"[dry-run] validate: acceptance {summary['acceptance_rate']} "
                  f"too low — a real loop would steer the generator here")
        print(f"[dry-run] summary: {json.dumps(summary)}")
        # "refine": no-op stand-in for call_llm(refine_prompt(...))
        stop.record_tokens(0, 0)  # a live refine would add real token counts
        current = f"canned_seed_strategy@iter{stop.iters}"
    print(f"[dry-run] stop: {stop.reason()}")
    print(f"[dry-run] cost: {stop.cost_report()}")
    return 0


def _to_jsonish(obj) -> str:
    try:
        return json.dumps(obj)
    except (TypeError, ValueError):
        return json.dumps(str(obj))


def main() -> int:
    ap = argparse.ArgumentParser(description="agentic fuzzing loop (json-parser/JSON)")
    ap.add_argument("--replay", metavar="ITER_DIR",
                    help="re-run a committed runs/iter-N strategy offline (no API)")
    ap.add_argument("--dry-run", action="store_true",
                    help="run the full loop shape on canned data (no API)")
    ap.add_argument("--harness", default=str(DEFAULT_HARNESS))
    ap.add_argument("--max-examples", type=int, default=EXAMPLES_PER_RUN)
    args = ap.parse_args()

    if args.replay:
        return run_replay(Path(args.harness), Path(args.replay), args.max_examples)
    if args.dry_run:
        return run_dry(Path(args.harness), args.max_examples)
    return run_live(Path(args.harness), args.max_examples)


if __name__ == "__main__":
    raise SystemExit(main())
