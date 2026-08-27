"""proxy_validation.py — EVALUATION ONLY. Does the blind proxy signal correlate
with actual coverage?

The assignment forbids coverage instrumentation *for steering*. This script uses
coverage purely to AUDIT, after the fact, the central design bet: that the proxy
signal the loop steered by (production coverage, deep-nesting mass, accepted-
structure novelty) actually tracks the code coverage it could not observe.

Method (proxy and coverage computed from the SAME inputs, so they are comparable):
  for each committed iteration's EFFECTIVE strategy,
    1. draw N seeded example strings (deterministic),
    2. run them through the coverage binary  -> region/line/branch coverage of json.c,
    3. run the same strings through the hunt harness + summarize() -> proxy signal,
  then correlate the two series across iterations (Spearman rho, computed without
  scipy).

CAVEAT stated up front: n = 5 iterations is a SMALL sample and line coverage
SATURATES at iteration 1 (every JSON production appears immediately), so the
informative signal is region/branch coverage vs. the depth/diversity proxy
components — not line coverage, which cannot move once saturated. This is an
exploratory correlation, reported with its n, not a significance claim.

    .venv/bin/python eval/proxy_validation.py     # writes eval/proxy_validation.md
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "fuzz"))
sys.path.insert(0, str(ROOT / "fuzz" / "loop"))

from coverage import _llvm, build_coverage_binary, draw_examples, COV_BIN  # noqa: E402

N = 300          # examples drawn per iteration (fixed, seeded)
SEED = 0
BUILD = ROOT / "build"
JSON_C = ROOT / "vendor" / "json-parser" / "json.c"
HUNT = BUILD / "harness_hunt"
ITERS = [1, 2, 3, 4, 5]


def effective_strategy_path(iter_dir: Path) -> Path:
    """The strategy that actually ran that iteration: strategy.py unless it is a
    broken pre-repair artifact, in which case strategy_fix.py (mirrors agent.py's
    replay fallback)."""
    canonical = iter_dir / "strategy.py"
    fix = iter_dir / "strategy_fix.py"
    if not fix.exists():
        return canonical
    try:
        _load(canonical)
        from hypothesis import given, settings, HealthCheck
        strat = _load(canonical).strategy

        @settings(max_examples=1, deadline=None, database=None,
                  suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(s=strat)
        def _probe(s):
            pass

        _probe()
        return canonical
    except Exception:  # noqa: BLE001
        return fix


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"strat_{path.parent.name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def coverage_of(examples: list[str], label: str) -> dict:
    """Region/line/branch coverage of json.c over exactly these input strings."""
    profdir = BUILD / f"_cov_pv_{label}"
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
    rep = subprocess.run(
        [*_llvm("llvm-cov"), "report", str(COV_BIN),
         f"-instr-profile={merged}", str(JSON_C)],
        capture_output=True, text=True, check=True).stdout
    total = [ln for ln in rep.splitlines() if ln.strip().startswith("TOTAL")][0]
    pcts = [float(t.rstrip("%")) for t in total.split() if t.endswith("%")]
    # columns: region%, function%, line%, branch%
    return {"region": pcts[0], "line": pcts[2], "branch": pcts[3]}


def proxy_of(examples: list[str], label: str) -> dict:
    """Run the same inputs through the hunt harness and summarize() to get the
    proxy-signal values on this exact sample (so proxy and coverage line up)."""
    from runner import run_once  # noqa: E402
    from agent import summarize  # noqa: E402
    samples = []
    for ex in examples:
        data = ex.encode("utf-8", "surrogatepass")
        r = run_once(HUNT, data)
        samples.append((ex, r.outcome.value))
    s = summarize(samples)
    return {
        "acceptance": s["acceptance_rate"],
        "productions": len(s["productions_accepted"]),
        "cap_mass": s["cap_distance_mass"],
        "novelty": s["novelty"],
        "score": (len(s["productions_accepted"]) * 5 + s["novelty"]
                  + s["cap_distance_mass"] * 20),
    }


def spearman(xs: list[float], ys: list[float]) -> float:
    """Spearman rank correlation, no scipy. Returns 0.0 if a series is constant."""
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = ranks(xs), ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return round(num / (dx * dy), 3) if dx and dy else 0.0


def main() -> int:
    if not HUNT.exists():
        print(f"[eval] need the hunt build: MODE=hunt bash harness/build.sh")
        return 1
    print(f"[eval] building coverage binary -> {COV_BIN}")
    build_coverage_binary()

    rows = []
    for it in ITERS:
        idir = ROOT / "runs" / f"iter-{it}"
        spath = effective_strategy_path(idir)
        strat = _load(spath).strategy
        examples = draw_examples(strat, N, SEED)
        cov = coverage_of(examples, f"iter{it}")
        prox = proxy_of(examples, f"iter{it}")
        rows.append({"iter": it, "strategy": spath.name, **cov, **prox})
        print(f"[eval] iter-{it} ({spath.name}): region={cov['region']:.1f}% "
              f"branch={cov['branch']:.1f}% | novelty={prox['novelty']} "
              f"cap_mass={prox['cap_mass']:.2f} score={prox['score']:.1f}")

    # Correlate each proxy component against region and branch coverage (the
    # non-saturating metrics). Line coverage saturates, so it is reported but not
    # the basis for the correlation claim.
    proxy_keys = ["score", "novelty", "cap_mass", "productions", "acceptance"]
    corr = {}
    for pk in proxy_keys:
        pv = [r[pk] for r in rows]
        corr[pk] = {
            "region": spearman(pv, [r["region"] for r in rows]),
            "branch": spearman(pv, [r["branch"] for r in rows]),
        }

    lines = [
        "# Proxy-signal validation against coverage (measurement only)",
        "",
        "**Question.** Coverage instrumentation is forbidden for *steering* the",
        "loop. Post-hoc, does the blind proxy signal the loop actually steered by",
        "correlate with the code coverage it could not see? This audits the central",
        "design bet.",
        "",
        f"**Method.** For each committed iteration's *effective* strategy, {N} seeded",
        "examples are drawn once and used for BOTH measurements, so proxy and",
        "coverage describe the same inputs: coverage of `json.c` on the "
        "`-fcoverage-mapping` build, and the proxy signal via the hunt harness + "
        "`summarize()`. Spearman rho is computed across the five iterations.",
        "",
        "**Caveat (read first).** n = 5 is small and **line coverage saturates at",
        "iteration 1** (every JSON production appears immediately), so line coverage",
        "cannot move and is not the basis for any correlation. The informative axes",
        "are **region** and **branch** coverage vs. the depth/diversity proxy",
        "components. Treat rho values as exploratory, reported with their n.",
        "",
        "## Per-iteration measurements",
        "",
        "| iter | strategy | region% | line% | branch% | acc | prods | cap_mass | novelty | score |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['iter']} | {r['strategy']} | {r['region']:.1f} | {r['line']:.1f} "
            f"| {r['branch']:.1f} | {r['acceptance']:.2f} | {r['productions']} "
            f"| {r['cap_mass']:.2f} | {r['novelty']} | {r['score']:.1f} |")
    lines += [
        "",
        "## Correlation (Spearman rho, n=5)",
        "",
        "| proxy component | vs region cov | vs branch cov |",
        "|---|---|---|",
    ]
    for pk in proxy_keys:
        lines.append(f"| {pk} | {corr[pk]['region']:+.3f} | {corr[pk]['branch']:+.3f} |")
    lines += [
        "",
        "## Reading it",
        "",
        "A positive rho means the proxy component moved together with real coverage",
        "across iterations — evidence the blind signal was tracking something the",
        "loop could not observe. A near-zero or negative rho for a component means it",
        "was *not* a good coverage proxy at this budget, which is itself an honest,",
        "useful result: it says which parts of the hand-designed signal earned their",
        "place and which did not. With n=5 and saturating line coverage this is an",
        "exploratory audit, not a significance test; the value is in *doing* the",
        "post-hoc validation the assignment's 'why did you expect it to work?' asks",
        "for, rather than asserting the signal was good.",
        "",
    ]
    (ROOT / "eval" / "proxy_validation.md").write_text("\n".join(lines))
    print("\n".join(lines[:4]))
    print(f"[eval] wrote eval/proxy_validation.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
