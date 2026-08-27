"""experiment.py — EVALUATION ONLY. A controlled, multi-seed experiment:
does grammar-seeding, and then feedback refinement, MEASURABLY beat the baseline?

Motivation. The committed single run reads as a clean 51->72 "evolution", but a
single unseeded run is max-of-noise (DECISIONS.md D9): its trajectory does not
reproduce. Rather than claim per-iteration improvement from one lucky draw, this
runs a proper experiment — three fixed generators, each measured over K
independent PRNG seeds — and reports mean +/- 95% CI with a nonparametric
significance test. The claim then rests on a distribution, not an anecdote.

Conditions (all fixed strategies; nothing is re-run through the LLM here):
  A. baseline   random `st.text()`                 (no grammar knowledge)
  B. seed       iter-1's effective grammar-seeded strategy (NO refinement)
  C. evolved    iter-5's strategy (AFTER 5 refinement iterations)

Per (seed, condition) ONE pass over N seeded examples through the coverage build
yields, together:
  - region / line / branch coverage of json.c  (from the merged profile)
  - acceptance rate, accepted-structure novelty, deep-nesting cap-mass
    (from the harness exit codes captured on the same pass)
so coverage and the proxy metrics describe exactly the same inputs.

Then across the K seeds: mean +/- std, a 95% CI (t-interval), and a two-sided
PERMUTATION test (assumption-free, no scipy) for the two comparisons that matter:
  grammar-seed vs random baseline   (does grammar-seeding help?)
  evolved vs grammar-seed           (does REFINEMENT help, beyond seeding?)

    .venv/bin/python eval/experiment.py [K] [N]     # writes eval/experiment.md
    # defaults K=12 seeds, N=150 examples/seed
"""
from __future__ import annotations

import importlib.util
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "fuzz"))
sys.path.insert(0, str(ROOT / "fuzz" / "loop"))

from coverage import _llvm, build_coverage_binary, COV_BIN  # noqa: E402

BUILD = ROOT / "build"
JSON_C = ROOT / "vendor" / "json-parser" / "json.c"
PERM_ITERS = 20000
PERM_SEED = 12345


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"strat_{path.parent.name}_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def effective_strategy(iter_dir: Path):
    """iter-N's strategy that actually ran (strategy.py, else strategy_fix.py)."""
    canonical = iter_dir / "strategy.py"
    fix = iter_dir / "strategy_fix.py"
    if not fix.exists():
        return _load(canonical).strategy
    try:
        strat = _load(canonical).strategy
        from hypothesis import given, settings, HealthCheck

        @settings(max_examples=1, deadline=None, database=None,
                  suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(s=strat)
        def _probe(s):
            pass

        _probe()
        return strat
    except Exception:  # noqa: BLE001
        return _load(fix).strategy


def draw(strategy, n: int, seed: int) -> list[str]:
    from hypothesis import given, settings, seed as hyp_seed, HealthCheck
    out: list[str] = []

    @settings(max_examples=n, database=None, deadline=None,
              suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(s=strategy)
    def collect(s):
        out.append(s if isinstance(s, str) else str(s))

    hyp_seed(seed)(collect)()
    return out


def measure(examples: list[str], tag: str) -> dict:
    """One pass: coverage (region/line/branch) + acceptance/novelty/cap_mass from
    the SAME inputs. The coverage build is non-sanitizer, so objects parse and its
    exit code still means 0=valid, 2=reject, 11=skip."""
    from agent import summarize  # noqa: E402
    profdir = BUILD / f"_cov_exp_{tag}"
    profdir.mkdir(exist_ok=True)
    for old in profdir.glob("*.profraw"):
        old.unlink()
    samples = []
    for i, ex in enumerate(examples):
        data = ex.encode("utf-8", "surrogatepass")
        env = {"LLVM_PROFILE_FILE": str(profdir / f"{i}.profraw")}
        p = subprocess.run([str(COV_BIN)], input=data, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        rc = p.returncode
        outcome = ("valid" if rc == 0 else "reject" if rc == 2
                   else "skip" if rc == 11 else "crash")
        samples.append((ex, outcome))
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
    s = summarize(samples)
    return {"region": pcts[0], "line": pcts[2], "branch": pcts[3],
            "acceptance": s["acceptance_rate"], "novelty": s["novelty"],
            "cap_mass": s["cap_distance_mass"],
            "productions": len(s["productions_accepted"])}


# --- statistics (no scipy) -------------------------------------------------

def mean(v): return sum(v) / len(v)


def std(v):
    m = mean(v)
    return (sum((x - m) ** 2 for x in v) / (len(v) - 1)) ** 0.5 if len(v) > 1 else 0.0


# two-sided 95% t-multipliers by dof (n-1), small-sample table; ~1.96 asymptote.
_T95 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45, 7: 2.36,
        8: 2.31, 9: 2.26, 10: 2.23, 11: 2.20, 12: 2.18, 14: 2.14, 16: 2.12,
        19: 2.09, 24: 2.06, 29: 2.05}


def ci95(v):
    n = len(v)
    if n < 2:
        return (mean(v), 0.0)
    dof = n - 1
    t = _T95.get(dof) or next((_T95[k] for k in sorted(_T95) if k >= dof), 1.96)
    return (mean(v), t * std(v) / (n ** 0.5))


def paired_perm_test(lo: list[float], hi: list[float],
                     iters: int = PERM_ITERS) -> tuple[float, float, float]:
    """Paired sign-flip permutation test for a randomized BLOCK design: the same
    K seeds are run under every condition, so measurements are paired by seed and
    the correct analysis conditions on that block. Tests whether the per-seed
    difference (hi - lo) has zero mean; each seed's difference-sign is independent
    under H0. Exact enumeration (2^K) when K is small. Returns
    (mean_diff, ci95_halfwidth_of_diff, p_value)."""
    diffs = [h - l for h, l in zip(hi, lo)]
    obs = abs(mean(diffs))
    n = len(diffs)
    _, half = ci95(diffs)                      # CI of the paired differences
    if n <= 20:                                # exact: enumerate all sign flips
        hits = total = 0
        for mask in range(1 << n):
            signed = [diffs[i] if (mask >> i) & 1 else -diffs[i] for i in range(n)]
            total += 1
            if abs(mean(signed)) >= obs - 1e-12:
                hits += 1
        return mean(diffs), half, hits / total
    rng = random.Random(PERM_SEED)             # sampled for large K
    hits = 0
    for _ in range(iters):
        signed = [d if rng.random() < 0.5 else -d for d in diffs]
        if abs(mean(signed)) >= obs - 1e-12:
            hits += 1
    return mean(diffs), half, (hits + 1) / (iters + 1)


def main() -> int:
    K = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    N = int(sys.argv[2]) if len(sys.argv) > 2 else 150
    print(f"[exp] building coverage binary; K={K} seeds x N={N} examples x 3 conditions")
    build_coverage_binary()

    import baseline_strategy
    conditions = {
        "A. baseline (random)": baseline_strategy.naive_strategy,
        "B. seed (iter-1, no refine)": effective_strategy(ROOT / "runs" / "iter-1"),
        "C. evolved (iter-5)": _load(ROOT / "runs" / "iter-5" / "strategy.py").strategy,
    }
    metrics = ["region", "line", "branch", "acceptance", "novelty", "cap_mass"]
    data = {c: {m: [] for m in metrics} for c in conditions}

    for si, seed in enumerate(range(K)):
        for ci, (cname, strat) in enumerate(conditions.items()):
            ex = draw(strat, N, seed)
            r = measure(ex, f"{ci}_{si}")
            for m in metrics:
                data[cname][m].append(r[m])
        print(f"[exp] seed {seed}: "
              + " | ".join(f"{c.split('.')[0]} reg={mean(data[c]['region']):.1f}"
                           for c in conditions))

    # aggregate
    agg = {c: {m: ci95(data[c][m]) for m in metrics} for c in conditions}
    names = list(conditions)

    def fmt(c, m):
        mu, h = agg[c][m]
        return f"{mu:.1f}±{h:.1f}" if m in ("region", "line", "branch", "novelty") \
            else f"{mu:.2f}±{h:.2f}"

    lines = [
        "# Controlled experiment: does grammar-seeding, then refinement, help?",
        "",
        f"**Design.** Three fixed generators, each measured over **K={K} independent",
        f"PRNG seeds** at **N={N} examples/seed**. Per (seed, condition) one pass over",
        "the coverage build yields both `json.c` coverage AND the acceptance / novelty /",
        "cap-mass proxy metrics on the *same* inputs. Values are **mean ± 95% CI**",
        "(t-interval) over the K seeds; comparisons use a two-sided **permutation test**",
        f"({PERM_ITERS} relabelings, no scipy). This replaces the single committed run's",
        "non-reproducible 51→72 trajectory (max-of-noise; DECISIONS.md D9) with a claim",
        "that rests on a distribution.",
        "",
        "## Results (mean ± 95% CI over seeds)",
        "",
        "| Condition | region% | line% | branch% | acceptance | novelty | cap_mass |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in names:
        lines.append("| " + c + " | " + " | ".join(
            fmt(c, m) for m in ["region", "line", "branch", "acceptance", "novelty", "cap_mass"]) + " |")

    # the two comparisons that matter, on region + branch coverage and novelty.
    # Paired sign-flip test on per-seed differences (randomized block design).
    def comp(lo, hi, label):
        out = [f"### {label}"]
        for m in ("region", "branch", "novelty"):
            d, half, p = paired_perm_test(data[lo][m], data[hi][m])
            sig = "**significant**" if p < 0.05 else "not significant"
            out.append(f"- {m}: Δ={d:+.1f} (95% CI [{d-half:+.1f}, {d+half:+.1f}])  "
                       f"paired p={p:.4f}, {sig}")
        return out

    lines += ["", "## Significance (paired sign-flip permutation test, exact)"]
    lines += comp(names[0], names[1], "Grammar-seed (B) vs random baseline (A) — does seeding help?")
    lines += comp(names[1], names[2], "Evolved (C) vs grammar-seed (B) — does REFINEMENT help beyond seeding?")
    lines += [
        "",
        "## Reading it",
        "",
        "A comparison is meaningful only if its CIs separate AND the permutation p is",
        "small. **B vs A** tests the project's core premise (grammar-seeding reaches the",
        "value-construction code random bytes miss). **C vs B** is the honest, harder",
        "test the single run could not make: does the feedback loop's refinement add",
        "anything *beyond* the initial grammar-seed, once seed-noise is averaged out? The",
        "numbers above answer both from a distribution rather than one lucky draw —",
        "including the case where refinement's effect is within noise, which is stated",
        "plainly rather than hidden.",
        "",
    ]
    (ROOT / "eval" / "experiment.md").write_text("\n".join(lines))
    print("\n".join(lines[lines.index("## Results (mean ± 95% CI over seeds)"):]))
    print("[exp] wrote eval/experiment.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
