"""leakcheck.py — hunt for memory LEAKS in json-parser with LeakSanitizer.

Meaningful on Linux only: LeakSanitizer is unsupported on macOS/arm64, so this
surface was never checked during development. It runs a seeded batch of inputs
from the evolved generator through the `hunt` build (objects parse fully, so the
parse -> free path completes) with `detect_leaks=1`, and reports any leak.

    python3 eval/leakcheck.py                    # after ./run.sh build

Uses the hunt build because on the default build finding #1's UB aborts on any
object BEFORE json_value_free runs, which would mask object-path leaks.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "fuzz"))
sys.path.insert(0, str(ROOT / "fuzz" / "loop"))

N = 300
SEED = 0
HARNESS = ROOT / "build" / "harness_hunt"
# LeakSanitizer ON (the whole point); still abort on other sanitizer errors.
ASAN = ("detect_leaks=1:abort_on_error=0:detect_stack_use_after_return=1:"
        "allocator_may_return_null=0")


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


def main() -> int:
    import importlib.util
    if not HARNESS.exists():
        print(f"[leak] {HARNESS} missing — run ./run.sh build first", file=sys.stderr)
        return 2

    spec = importlib.util.spec_from_file_location(
        "evolved", ROOT / "runs" / "iter-5" / "strategy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    inputs = draw(mod.strategy, N, SEED)

    if sys.platform == "darwin":
        print("[leak] NOTE: LeakSanitizer is unsupported on macOS/arm64 — this "
              "check only finds leaks when run on Linux (see the Dockerfile).")

    leaks = []
    for data in inputs:
        r = subprocess.run([str(HARNESS)], input=data.encode("utf-8", "surrogatepass"),
                           env={"ASAN_OPTIONS": ASAN},
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        err = r.stderr.decode("utf-8", "replace")
        if "LeakSanitizer" in err or "detected memory leaks" in err:
            leaks.append((data[:60], err[:600]))

    print(f"[leak] ran {len(inputs)} inputs through {HARNESS.name} with detect_leaks=1")
    if not leaks:
        print("[leak] RESULT: no leaks detected.")
        return 0
    print(f"[leak] RESULT: {len(leaks)} input(s) leaked. First report:\n")
    print(f"  input: {leaks[0][0]!r}\n")
    print(leaks[0][1])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
