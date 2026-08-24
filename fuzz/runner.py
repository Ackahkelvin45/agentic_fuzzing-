"""runner.py — run one candidate input through the compiled harness and classify.

This is the single source of truth for "crash vs. rejection vs. valid parse".
Everything else (baseline strategy, agentic loop, triage) calls run_once().

Classification rule (deliberately conservative, per the harness contract):
    exit 0                      -> VALID   (parser accepted the input)
    exit 2                      -> REJECT  (well-formed rejection; NOT a bug)
    exit 11                     -> SKIP    (oversized input the harness could not
                                            test faithfully — a HARNESS limitation,
                                            not a parser rejection. Unlike the
                                            parson harness there is NO embedded-NUL
                                            skip: json-parser's length-taking API
                                            tests NUL bytes faithfully.)
    timeout (> TIMEOUT_S)       -> CRASH   (a hang is a DoS bug per the spec)
    killed by a signal          -> CRASH   (SIGSEGV/SIGABRT/...)
    ANY other exit code         -> CRASH   (sanitizer aborts, unexpected codes)

SKIP is its own bucket so "reject" in the logs always means "the parser
rejected malformed JSON", never "the harness declined to test it". We never
hardcode "the sanitizer exit code": anything outside the known set is a crash.
"""
from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

TIMEOUT_S = 5  # per-input wall-clock cap (assignment constraint)

# Sanitizer runtime options. abort_on_error makes the process die with a signal
# so we see a crash rather than a clean exit; print_stacktrace gives UBSan a
# trace for dedup; halt_on_error makes the FIRST error fatal.
SANITIZER_ENV = {
    "ASAN_OPTIONS": "abort_on_error=1:detect_stack_use_after_return=1:"
                    "allocator_may_return_null=0:detect_leaks=0",
    "UBSAN_OPTIONS": "print_stacktrace=1:halt_on_error=1:abort_on_error=1",
}


class Outcome(str, Enum):
    VALID = "valid"
    REJECT = "reject"
    SKIP = "skip"     # harness could not test this input faithfully
    CRASH = "crash"


# Exit codes the harness uses for non-crash outcomes. Anything NOT in this map
# (and not exit 0/2) is treated as a crash.
_SKIP_CODES = {11: "oversized"}
_ENVFAIL_CODE = 3


@dataclass
class Result:
    outcome: Outcome
    exit_code: int | None      # process exit code, or None if killed/timed out
    term_signal: int | None    # terminating signal number, if any
    timed_out: bool
    stderr: bytes              # captured for crash triage (sanitizer report)
    detail: str = ""           # e.g. skip reason ("embedded-nul") for logging

    @property
    def is_crash(self) -> bool:
        return self.outcome is Outcome.CRASH

    def signature_source(self) -> str:
        """Human-readable one-line reason, handy for logs."""
        if self.timed_out:
            return f"timeout>{TIMEOUT_S}s"
        if self.term_signal is not None:
            return f"signal:{signal.Signals(self.term_signal).name}"
        if self.detail:
            return f"exit:{self.exit_code}({self.detail})"
        return f"exit:{self.exit_code}"


# The harness needs almost nothing from our environment. Passing the FULL
# environment handed the LLM API key (loaded from .env into os.environ) to every
# one of thousands of subprocess spawns; an allowlist keeps secrets out of them.
_ENV_PASSTHROUGH = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "DYLD_LIBRARY_PATH",
                    "LD_LIBRARY_PATH", "ASAN_SYMBOLIZER_PATH")


def run_once(binary: str | Path, data: bytes, timeout_s: int = TIMEOUT_S) -> Result:
    """Feed `data` to `binary` on stdin and classify the outcome."""
    env = {k: os.environ[k] for k in _ENV_PASSTHROUGH if k in os.environ}
    env.update(SANITIZER_ENV)
    try:
        proc = subprocess.run(
            [str(binary)],
            input=data,
            stdout=subprocess.DEVNULL,   # harness prints nothing on stdout by design
            stderr=subprocess.PIPE,      # sanitizer report lands here
            timeout=timeout_s,
            env=env,
        )
    except subprocess.TimeoutExpired as e:
        # A hang is a crash by the SAME rule as any non-{0,2} outcome: it is not
        # a clean parse/reject. timed_out only records WHY, it is not a separate
        # verdict path — the bucket is still CRASH.
        return Result(Outcome.CRASH, None, None, True, e.stderr or b"", "timeout")

    rc = proc.returncode
    if rc < 0:  # killed by signal -N
        return Result(Outcome.CRASH, None, -rc, False, proc.stderr)
    if rc == 0:
        return Result(Outcome.VALID, 0, None, False, proc.stderr)
    if rc == 2:
        return Result(Outcome.REJECT, 2, None, False, proc.stderr)
    if rc in _SKIP_CODES:
        return Result(Outcome.SKIP, rc, None, False, proc.stderr, _SKIP_CODES[rc])
    if rc == _ENVFAIL_CODE:
        return Result(Outcome.SKIP, rc, None, False, proc.stderr, "harness-envfail")
    return Result(Outcome.CRASH, rc, None, False, proc.stderr)


def harness_mode(binary: str | Path) -> str:
    """Ask the binary which build mode it is (default | roundtrip | control)."""
    out = subprocess.run([str(binary), "--mode"], stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, timeout=10)
    return out.stdout.decode().strip()


_REAL_TARGET_MODES = {"default", "hunt"}


def assert_real_target(binary: str | Path) -> None:
    """Guard: refuse to treat a SYNTHETIC build as a source of real findings.

    Enforced, not merely intended — the positive-control build reports mode
    'control' and is rejected here, so its injected crash can never be logged as
    a json-parser bug. The 'hunt' build IS allowed: it is the real parser with
    one specific UBSan check (pointer-overflow) disabled to get past json-parser's
    known first-pass NULL-offset idiom (finding #1), so any crash it surfaces is
    a genuine memory-safety/UB bug — re-confirmed on the 'default' build.
    """
    mode = harness_mode(binary)
    if mode not in _REAL_TARGET_MODES:
        raise RuntimeError(
            f"refusing to fuzz for real findings with a '{mode}'-mode binary "
            f"({binary}); real runs must use the default or hunt build."
        )


if __name__ == "__main__":
    # Manual smoke helper:  python runner.py <binary> [file]  (stdin if no file)
    import sys

    binary = sys.argv[1]
    if len(sys.argv) > 2:
        payload = Path(sys.argv[2]).read_bytes()
    else:
        payload = sys.stdin.buffer.read()
    r = run_once(binary, payload)
    print(f"{r.outcome.value:6}  {r.signature_source()}")
    if r.is_crash and r.stderr:
        sys.stderr.buffer.write(r.stderr)
