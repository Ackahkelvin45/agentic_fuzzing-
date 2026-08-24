"""coverage.py — EVALUATION ONLY (not part of the blackbox loop).

The assignment forbids coverage instrumentation *for steering* the loop. This
script uses coverage purely to *measure*, after the fact, one claim the design
rests on: that an LLM grammar-seeded generator reaches far more of the parser
than random text. It builds a SEPARATE, non-sanitizer llvm-cov binary (so
finding #1's UB is harmless and objects parse normally), draws a fixed, seeded
sample of inputs from each strategy, and reports line coverage of json.c.

    .venv/bin/python eval/coverage.py            # writes eval/coverage.md

Deliberately measures only two points — random baseline vs. the evolved
generator. It does NOT plot a per-iteration trajectory: iteration 1 already
covers every JSON production (see runs/iter-*/stats.json), so line coverage
saturates immediately and a trajectory would misrepresent the loop's later,
depth/diversity-oriented work.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def _llvm(tool: str) -> list[str]:
    """Return an argv prefix for an LLVM tool, portable across macOS and Linux:
    `xcrun llvm-cov` on macOS (Apple clang), plain `llvm-cov` (or a versioned
    `llvm-cov-N`) on Linux."""
    if shutil.which(tool):
        return [tool]
    if shutil.which("xcrun"):
        return ["xcrun", tool]
    for cand in (f"{tool}-18", f"{tool}-17", f"{tool}-16", f"{tool}-15", f"{tool}-14"):
        if shutil.which(cand):
            return [cand]
    return ["xcrun", tool]  # last resort; will error clearly if truly absent

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "fuzz"))
sys.path.insert(0, str(ROOT / "fuzz" / "loop"))

N = 400          # examples drawn per strategy (fixed, seeded)
SEED = 0
BUILD = ROOT / "build"
COV_BIN = BUILD / "harness_cov"
JSON_C = ROOT / "vendor" / "json-parser" / "json.c"


def build_coverage_binary() -> None:
    """Compile a coverage-instrumented, NON-sanitizer harness (objects parse)."""
    BUILD.mkdir(exist_ok=True)
    cmd = [
        "clang", "-fprofile-instr-generate", "-fcoverage-mapping", "-g", "-O0",
        "-I", str(ROOT / "vendor" / "json-parser"),
        str(JSON_C), str(ROOT / "harness" / "harness.c"), "-o", str(COV_BIN),
    ]
    subprocess.run(cmd, check=True)


def draw_examples(strategy, n: int, seed: int) -> list[str]:
    """Draw n examples from a Hypothesis strategy, deterministically."""
    from hypothesis import given, settings, seed as hyp_seed, HealthCheck
    out: list[str] = []

    @settings(max_examples=n, database=None, deadline=None,
              suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(s=strategy)
    def collect(s):
        out.append(s if isinstance(s, str) else str(s))

    hyp_seed(seed)(collect)()
    return out


def measure(strategy, label: str) -> float:
    """Run n seeded examples through the coverage binary; return json.c line%."""
    examples = draw_examples(strategy, N, SEED)
    profdir = BUILD / f"_cov_{label}"
    profdir.mkdir(exist_ok=True)
    for old in profdir.glob("*.profraw"):
        old.unlink()
    for i, ex in enumerate(examples):
        data = ex.encode("utf-8", "surrogatepass")
        env = {"LLVM_PROFILE_FILE": str(profdir / f"{i}.profraw")}
        subprocess.run([str(COV_BIN)], input=data, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    raws = [str(p) for p in profdir.glob("*.profraw")]
    merged = profdir / "merged.profdata"
    subprocess.run([*_llvm("llvm-profdata"), "merge", "-sparse", *raws,
                    "-o", str(merged)], check=True)
    # `llvm-cov export` with a tiny summary parse keeps us off text-format drift.
    rep = subprocess.run(
        [*_llvm("llvm-cov"), "report", str(COV_BIN),
         f"-instr-profile={merged}", str(JSON_C)],
        capture_output=True, text=True, check=True).stdout
    # TOTAL line: the last numeric %.  Columns: regions... lines %, branches...
    total = [ln for ln in rep.splitlines() if ln.strip().startswith("TOTAL")][0]
    # line coverage is the 4th percentage on the row for a single-file report;
    # parse all percentages and take the line-cover one (index 2: region,func,line).
    pcts = [tok for tok in total.split() if tok.endswith("%")]
    line_pct = float(pcts[2].rstrip("%"))  # region%, function%, line%, branch%
    return line_pct


def main() -> int:
    import baseline_strategy
    import importlib.util

    print(f"[eval] building coverage binary -> {COV_BIN}")
    build_coverage_binary()

    baseline = baseline_strategy.naive_strategy
    spec = importlib.util.spec_from_file_location(
        "evolved", ROOT / "runs" / "iter-5" / "strategy.py")
    evolved_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evolved_mod)
    evolved = evolved_mod.strategy

    print(f"[eval] measuring line coverage of json.c over {N} seeded examples each")
    base_cov = measure(baseline, "baseline")
    eng_cov = measure(evolved, "evolved")

    out = (
        "# Coverage evaluation (measurement only — not used to steer the loop)\n\n"
        f"Line coverage of `vendor/json-parser/json.c` over {N} seeded examples "
        "each, on a separate non-sanitizer `-fcoverage-mapping` build:\n\n"
        "| Generator | json.c line coverage |\n|---|---|\n"
        f"| naive baseline (`st.text()`, Step 3) | {base_cov:.1f}% |\n"
        f"| LLM grammar-seeded (evolved, iter-5) | {eng_cov:.1f}% |\n\n"
        f"The grammar-seeded generator reaches **{eng_cov/max(base_cov,1e-9):.1f}x** "
        "the line coverage of random text. Random text still exercises the lexer and\n"
        "rejection/error paths (hence the non-trivial baseline), but it rarely forms a\n"
        "parseable value, so it misses the value/number/string/object *construction*\n"
        "paths that only (near-)valid JSON reaches — exactly what the grammar-seeded\n"
        "generator adds. This *measures* an assertion the design rests on; it is not a\n"
        "steering signal.\n"
    )
    (ROOT / "eval" / "coverage.md").write_text(out)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
