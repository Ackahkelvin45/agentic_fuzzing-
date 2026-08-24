"""test_pipeline.py — exercise EVERY classifier branch with a known input, and
prove both sanitizers are active with symbolized stack traces.

Run:  .venv/bin/python fuzz/test_pipeline.py
Exit 0 = all assertions passed. This is the evidence that the outcome
contract holds on more than the happy path.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "fuzz"))
from runner import (  # noqa: E402
    run_once, Outcome, harness_mode, assert_real_target, SANITIZER_ENV,
)

BUILD = ROOT / "build"
HARNESS = BUILD / "harness"
CONTROL = BUILD / "harness_control"

# Same sanitizer flags as harness/build.sh — kept in sync intentionally.
SAN_FLAGS = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
             "-fno-omit-frame-pointer", "-g", "-O0"]

_passed = 0
_failed = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {extra}")


def compile_c(src: str, out: Path, sanitize: bool) -> Path:
    with tempfile.NamedTemporaryFile("w", suffix=".c", delete=False) as f:
        f.write(src)
        path = f.name
    flags = SAN_FLAGS if sanitize else ["-O0", "-g"]
    subprocess.run([os.environ.get("CC","clang"), *flags, path, "-o", str(out)], check=True)
    return out


def main() -> int:
    assert HARNESS.exists() and CONTROL.exists(), "run ./run.sh build first"

    # --- helper binaries -------------------------------------------------
    selftest = compile_c((ROOT / "harness" / "selftest.c").read_text(),
                         BUILD / "selftest", sanitize=True)
    segv = compile_c("int main(void){volatile int*p=0;return *p;}",
                     BUILD / "_segv", sanitize=False)          # raw SIGSEGV
    hang = compile_c("#include <unistd.h>\nint main(void){for(;;)pause();return 0;}",
                     BUILD / "_hang", sanitize=False)          # never returns

    print("== classifier branches (default harness) ==")
    # NB: use NON-object inputs here. On the default (full-sanitizer) build, any
    # object-with-member trips json-parser's confirmed first-pass UB (finding #1,
    # crashes/), so `{"a":1}` aborts rather than classifying valid/reject.
    r = run_once(HARNESS, b'[1,2,3,null]')
    check("VALID  on well-formed JSON", r.outcome is Outcome.VALID, r.signature_source())

    r = run_once(HARNESS, b'[1,2')
    check("REJECT on malformed JSON", r.outcome is Outcome.REJECT, r.signature_source())

    # json-parser's length-taking API tests embedded NUL faithfully (no SKIP,
    # unlike the parson harness): a NUL inside a string is a well-formed REJECT.
    r = run_once(HARNESS, b'"a\x00b"')
    check("embedded NUL is TESTED, not skipped",
          r.outcome is not Outcome.SKIP, r.signature_source())

    big = b"a" * (64 * 1024 * 1024 + 8)
    r = run_once(HARNESS, big)
    check("SKIP   on oversized input (distinct code 11)",
          r.outcome is Outcome.SKIP and r.detail == "oversized", r.signature_source())

    print("== crash branches ==")
    r = run_once(CONTROL, b'"__CRASH__"')
    check("CRASH  on ASan abort (control build)", r.outcome is Outcome.CRASH, r.signature_source())
    check("       ASan report captured with a stack frame",
          b"AddressSanitizer" in r.stderr and b"#0 " in r.stderr)

    r = run_once(segv, b"")
    check("CRASH  on plain SIGSEGV (no sanitizer)",
          r.outcome is Outcome.CRASH and r.term_signal is not None, r.signature_source())

    r = run_once(hang, b"", timeout_s=2)
    check("CRASH  on timeout (hang), same rule not special verdict",
          r.outcome is Outcome.CRASH and r.timed_out, r.signature_source())

    print("== both sanitizers active + symbolized (real build flags) ==")
    env = {**__import__("os").environ, **SANITIZER_ENV}
    a = subprocess.run([str(selftest), "asan"], stderr=subprocess.PIPE, env=env)
    check("ASan aborts on heap-overflow", a.returncode != 0)
    check("ASan report is symbolized (names asan_trigger/selftest.c)",
          b"asan_trigger" in a.stderr or b"selftest.c" in a.stderr, a.stderr[:120])

    u = subprocess.run([str(selftest), "ubsan"], stderr=subprocess.PIPE, env=env)
    check("UBSan ABORTS on signed overflow (not just warns)", u.returncode != 0)
    check("UBSan report says 'runtime error' (UB detected)", b"runtime error" in u.stderr)
    check("UBSan stack trace present (print_stacktrace=1 in effect)",
          b"#0 " in u.stderr, u.stderr[:200])

    print("== build-mode separation is ENFORCED ==")
    check("default binary reports mode 'default'", harness_mode(HARNESS) == "default")
    check("control binary reports mode 'control'", harness_mode(CONTROL) == "control")
    guard_ok = False
    try:
        assert_real_target(CONTROL)
    except RuntimeError:
        guard_ok = True
    check("assert_real_target REFUSES the control build", guard_ok)
    assert_real_target(HARNESS)  # must not raise
    check("assert_real_target ACCEPTS the default build", True)

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
