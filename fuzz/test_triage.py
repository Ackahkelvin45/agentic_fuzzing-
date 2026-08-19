"""test_triage.py — prove Step 5 triage on REAL sanitizer crashes.

Builds a synthetic multi-bug harness (harness/triage_test.c) where different
marker inputs crash in different functions, then checks that triage:
  - groups multiple variants of the SAME bug into one signature (dedup),
  - splits DIFFERENT bugs into different signatures,
  - excludes non-crashing inputs,
  - minimizes each reproducer down to its marker (ddmin, signature-preserving),
  - verifies each minimized reproducer reproduces standalone.

Run:  .venv/bin/python fuzz/test_triage.py
"""
from __future__ import annotations

import shutil
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "fuzz"))
sys.path.insert(0, str(ROOT / "fuzz" / "triage"))
from triage import triage, ddmin, verify, make_record  # noqa: E402
from signature import crash_signature  # noqa: E402

BUILD = ROOT / "build"
SAN = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
       "-fno-omit-frame-pointer", "-g", "-O0"]

_p = _f = 0


def check(name, cond, extra=""):
    global _p, _f
    _p += bool(cond); _f += not cond
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {extra}"))


def _sig_normalization_checks() -> None:
    # UBSan variable data must NOT split one bug into many (the critic's find).
    ub5 = b"parson.c:10:9: runtime error: index 5 out of bounds for type 'char [3]'\n    #0 0x1 in parse_x parson.c:10\n"
    ub7 = b"parson.c:10:9: runtime error: index 7 out of bounds for type 'char [9]'\n    #0 0x1 in parse_x parson.c:10\n"
    check("UBSan index 5 vs 7 -> SAME signature",
          crash_signature(ub5) == crash_signature(ub7), crash_signature(ub5))
    sh = b"x.c:1:1: runtime error: shift exponent 32 is too large for 32-bit type\n    #0 0x1 in shl x.c:1\n"
    check("UBSan kind differs from index -> DIFFERENT signature",
          crash_signature(sh) != crash_signature(ub5))
    # interceptor top frame is skipped so the real site is the signature.
    rep = b"ERROR: AddressSanitizer: heap-buffer-overflow\n    #0 0x1 in __asan_memcpy\n    #1 0x2 in memcpy\n    #2 0x3 in parse_string parson.c:5\n"
    check("interceptor frames skipped -> site is parse_string",
          "parse_string" in crash_signature(rep) and "memcpy" not in crash_signature(rep),
          crash_signature(rep))
    # no app frames -> SUMMARY site used, not a blanket '?'
    nofr = b"ERROR: AddressSanitizer: SEGV on unknown address\nSUMMARY: AddressSanitizer: SEGV parson.c:99 in parse_number\n"
    check("no-frames report falls back to SUMMARY site",
          crash_signature(nofr).endswith("parse_number"), crash_signature(nofr))


def main() -> int:
    _sig_normalization_checks()
    tbin = BUILD / "triage_test"
    subprocess.run([os.environ.get("CC","clang"), *SAN, str(ROOT / "harness" / "triage_test.c"),
                    "-o", str(tbin)], check=True)

    # multiple variants per bug + one non-crashing input
    inputs = [
        b"OVERFLOW", b"xxOVERFLOWyy", b"OVERFLOW-12345",     # bug A (x3)
        b"USEAFTER", b"pad-USEAFTER-pad",                     # bug B (x2)
        b"NULLDEREF",                                         # bug C (x1)
        b'{"ok":true}',                                       # not a crash
    ]

    out = Path(tempfile.mkdtemp(prefix="triage_"))
    try:
        result = triage(inputs, tbin, out_dir=out)

        check("3 distinct signatures (bugs split correctly)", len(result) == 3,
              f"got {len(result)}: {list(result)}")

        # find each group by its bug class
        by_class = {sig.split("|")[0]: meta for sig, meta in result.items()}
        check("has heap-buffer-overflow group", any("overflow" in k for k in by_class))
        check("has heap-use-after-free group", any("use-after-free" in k for k in by_class))
        check("has SEGV group", any("SEGV" in k for k in by_class))

        # dedup: the 3 OVERFLOW variants collapsed to ONE group with count 3
        ov = next(m for s, m in result.items() if "overflow" in s)
        check("overflow variants deduped to count=3", ov["count"] == 3, str(ov))

        # non-crashing input excluded
        total = sum(m["count"] for m in result.values())
        check("non-crashing input excluded (total==6)", total == 6, f"total={total}")

        # every reproducer verified
        check("all minimized reproducers verified", all(m["verified"] for m in result.values()))

        # minimization actually reduced to the marker
        ov_dir = out / ov["sig_id"]
        mini = (ov_dir / "minimized_input").read_bytes()
        check("overflow minimized to just b'OVERFLOW'", mini == b"OVERFLOW", repr(mini))
        check("repro.sh written + executable", (ov_dir / "repro.sh").stat().st_mode & 0o100)

        # signature stability: two different overflow inputs -> same signature
        s1 = make_record(b"OVERFLOW", tbin).signature
        s2 = make_record(b"zzzOVERFLOWzzz", tbin).signature
        check("same bug -> identical signature regardless of input", s1 == s2)
    finally:
        shutil.rmtree(out, ignore_errors=True)

    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    raise SystemExit(main())
