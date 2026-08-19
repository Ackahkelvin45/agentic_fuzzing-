"""triage.py — Step 5: detect -> capture -> dedupe -> minimize -> verify.

Minimization strategy (and how it relates to Hypothesis):
- The PRIMARY minimizer is Hypothesis's own shrinker, which runs automatically
  when the @given hunt in agent.py finds a crash — so crashes discovered by the
  loop arrive already shrunk within the strategy's space.
- This module adds a deterministic **delta-debug (ddmin)** pass that is (a)
  signature-preserving (a reduction only counts if it still crashes with the
  SAME normalized signature), and (b) able to minimize ANY externally-supplied
  reproducer (e.g. a saved crash input), which a from-scratch Hypothesis search
  cannot reliably reproduce (a marker/needle input is not rediscovered by random
  search). ddmin further reduces the Hypothesis-shrunk input and de-risks triage.

Every unique signature is saved to crashes/<id>/ with input, minimized_input,
sanitizer_report, meta.json, signature.txt, and a runnable repro.sh — then the
minimized input is re-verified standalone (per the assignment).
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "fuzz"))
sys.path.insert(0, str(ROOT / "fuzz" / "triage"))
from runner import run_once, SANITIZER_ENV  # noqa: E402
from signature import crash_signature, sig_id  # noqa: E402

CRASHES = ROOT / "crashes"


@dataclass
class CrashRecord:
    data: bytes
    stderr: bytes
    signature: str
    timed_out: bool
    exit_code: int | None
    term_signal: int | None


def make_record(data: bytes, harness: Path) -> CrashRecord | None:
    """Run one input; return a CrashRecord iff it crashed, else None."""
    r = run_once(harness, data)
    if not r.is_crash:
        return None
    sig = crash_signature(r.stderr, r.timed_out)
    return CrashRecord(data, r.stderr, sig, r.timed_out, r.exit_code, r.term_signal)


def _same_crash(data: bytes, harness: Path, signature: str,
                timeout_s: int) -> bool:
    r = run_once(harness, data, timeout_s=timeout_s)
    return r.is_crash and crash_signature(r.stderr, r.timed_out) == signature


def ddmin(data: bytes, harness: Path, signature: str, timeout_s: int = 5) -> bytes:
    """Classic delta-debugging: shrink `data` while it still crashes with the
    same signature. Deterministic and signature-preserving."""
    if not _same_crash(data, harness, signature, timeout_s):
        return data  # doesn't reproduce; don't touch it
    buf = data
    n = 2
    while len(buf) >= 2:
        chunk = max(1, len(buf) // n)
        pieces = [buf[i:i + chunk] for i in range(0, len(buf), chunk)]
        reduced = False
        for k in range(len(pieces)):
            complement = b"".join(pieces[:k] + pieces[k + 1:])
            if complement != buf and _same_crash(complement, harness, signature, timeout_s):
                buf = complement
                n = max(n - 1, 2)
                reduced = True
                break
        if not reduced:
            if n >= len(buf):
                break
            n = min(len(buf), n * 2)
    return buf


def verify(data: bytes, harness: Path, signature: str, timeout_s: int = 5) -> bool:
    """Re-run standalone; confirm it deterministically reproduces the signature."""
    return _same_crash(data, harness, signature, timeout_s)


def reproducible(data: bytes, harness: Path, signature: str,
                 trials: int = 3, need: int = 2, timeout_s: int = 5) -> bool:
    """Flakiness guard: does `data` crash with the SAME signature on at least
    `need` of `trials` runs? Prevents choosing a flaky representative."""
    hits = 0
    for i in range(trials):
        if _same_crash(data, harness, signature, timeout_s):
            hits += 1
        if hits >= need:
            return True
        if i - hits + 1 > trials - need:  # can't reach `need` anymore
            break
    return False


def triage(inputs: list[bytes], harness: Path, out_dir: Path = CRASHES,
           minimize: bool = True) -> dict:
    """Dedupe crashing inputs by signature, minimize + verify one per group,
    and save artifacts. Returns {signature: {...}} summary."""
    # 1. capture only genuine crashers
    records = [rec for d in inputs if (rec := make_record(d, harness))]

    # 2. group by normalized signature
    groups: dict[str, list[CrashRecord]] = {}
    for rec in records:
        groups.setdefault(rec.signature, []).append(rec)

    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, dict] = {}
    for sig, recs in groups.items():
        # representative = shortest input that RELIABLY reproduces (flakiness
        # guard). If none is reliable, fall back to shortest and flag the group.
        by_len = sorted(recs, key=lambda r: len(r.data))
        rep = next((r for r in by_len
                    if not r.timed_out and reproducible(r.data, harness, sig)), None)
        low_confidence = rep is None
        if rep is None:
            rep = by_len[0]
        # timeouts / flaky reps are expensive or unsafe to minimize; skip.
        do_min = minimize and not rep.timed_out and not low_confidence
        minimized = ddmin(rep.data, harness, sig) if do_min else rep.data
        ok = verify(minimized, harness, sig)

        sid = sig_id(sig)
        d = out_dir / sid
        d.mkdir(parents=True, exist_ok=True)
        (d / "input").write_bytes(rep.data)
        (d / "minimized_input").write_bytes(minimized)
        (d / "sanitizer_report").write_bytes(rep.stderr)
        (d / "signature.txt").write_text(sig + "\n")
        (d / "meta.json").write_text(json.dumps({
            "signature": sig,
            "sig_id": sid,
            "count": len(recs),
            "raw_len": len(rep.data),
            "minimized_len": len(minimized),
            "verified": ok,
            "low_confidence": low_confidence,  # rep reproduced flakily
            "timed_out": rep.timed_out,
            "exit_code": rep.exit_code,
            "term_signal": rep.term_signal,
        }, indent=2))
        # a runnable, self-contained reproducer — exports the SAME sanitizer env
        # the triage used, so the manual report matches the saved signature
        # (UBSan needs print_stacktrace=1 or it emits only a one-liner).
        env_lines = "\n".join(f'export {k}="{v}"' for k, v in SANITIZER_ENV.items())
        (d / "repro.sh").write_text(
            "#!/usr/bin/env bash\n"
            "# reproduce this crash against the pinned build\n"
            f"{env_lines}\n"
            'HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
            f'"{harness}" < "$HERE/minimized_input"\n')
        (d / "repro.sh").chmod(0o755)

        result[sig] = {"sig_id": sid, "count": len(recs),
                       "minimized_len": len(minimized), "verified": ok,
                       "low_confidence": low_confidence}
    return result


if __name__ == "__main__":
    # CLI: triage a directory of raw crash inputs.
    #   python triage.py <harness> <input-file> [<input-file> ...]
    harness = Path(sys.argv[1])
    payloads = [Path(p).read_bytes() for p in sys.argv[2:]]
    print(json.dumps(triage(payloads, harness), indent=2))
