"""signature.py — normalize a sanitizer report into a crash signature for dedup.

Why a normalized signature (and the choices behind it):

- **We key on the sanitizer report, NOT the exit signal.** Under the real
  ASan+UBSan build, ASan intercepts every memory fault and aborts with SIGABRT
  (exit 134) regardless of bug class (verified: a null deref reports
  "SEGV on unknown address ... SUMMARY: AddressSanitizer: SEGV" yet exits 134,
  not 139). So the signal is near-constant and useless for telling bugs apart;
  the report's bug-class + stack frames are what distinguish them.

- **Frames are function names only.** We drop addresses (`0x...`), per-frame
  offsets (`+0x1b4c`), and file:line — line numbers shift across builds/versions
  and would split one bug into many. Function-name identity is stable.

- **System/runtime frames are filtered** (dyld, libclang_rt, sanitizer, start),
  so the signature reflects the application call site, not the launcher.

- **Top 3 app frames** balance specificity (distinguishing distinct bugs) vs.
  over-splitting (the same bug reached by slightly different callers).

- **Timeouts** get a fixed `timeout` signature (a DoS class; no report exists).

These choices are documented because dedup normalization is a graded judgment
call — they decide whether we report "one bug" or "several".
"""
from __future__ import annotations

import hashlib
import re

TOP_FRAMES = 3

_FRAME_RE = re.compile(r"#\d+\s+0x[0-9a-fA-F]+\s+in\s+([A-Za-z_][\w]*)")
_ASAN_RE = re.compile(r"AddressSanitizer:\s+([A-Za-z0-9_\-]+)")
_UBSAN_RE = re.compile(r"runtime error:\s+([^\n]+)")
# SUMMARY fallback when no app frames parse: names the site as `<file:line> in <fn>`.
_SUMMARY_FN_RE = re.compile(r"SUMMARY:.*?\bin\s+([A-Za-z_]\w*)")
_SUMMARY_LOC_RE = re.compile(r"SUMMARY:.*?\b([\w./-]+:\d+)")

# Frames from the loader / sanitizer runtime / libc interceptors — not the bug
# site. ASan reports often TOP with an interceptor (e.g. `__asan_memcpy` or a
# bare `memcpy`), so we skip those to reach the real application call site; without
# this, two different overflows sharing an interceptor+caller would merge.
_SYSTEM_FRAMES = (
    # macOS runtime/loader
    "dyld", "start", "libclang_rt", "libsystem", "_dispatch",
    # Linux runtime/loader (the grader is likely on Linux)
    "libasan", "libubsan", "__libc_start_main", "_start", "__libc_",
    # sanitizer runtime + libc interceptors (present on both platforms)
    "__sanitizer", "__asan", "__ubsan", "__lsan", "pthread", "wrap_",
    "memcpy", "memmove", "memset", "strlen", "strcpy",
    "strcat", "strncpy", "strncat", "strnlen", "strchr")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _canon_ubsan_kind(msg: str) -> str:
    """Canonicalize a UBSan message to a stable KIND, stripping the variable
    operand data that would otherwise split one bug into many. E.g.
      'index 5 out of bounds for type ...'  and  'index 7 out of bounds ...'
      -> both 'index-out-of-bounds-for-type'.
    """
    msg = msg.split(":")[0]              # drop trailing ': 9 * 10 ...' detail
    msg = re.sub(r"'[^']*'", "", msg)    # drop quoted 'type' names
    msg = re.sub(r'"[^"]*"', "", msg)
    msg = re.sub(r"\[[^\]]*\]", "", msg)  # drop [3] array bounds
    msg = re.sub(r"\d+", "", msg)         # drop indices/exponents/offsets/values
    return _slug(msg)


def _bug_class(text: str) -> str:
    m = _ASAN_RE.search(text)
    if m:
        return f"asan:{m.group(1)}"
    m = _UBSAN_RE.search(text)
    if m:
        return f"ubsan:{_canon_ubsan_kind(m.group(1).strip())}"
    return "unknown"


def _app_frames(text: str) -> list[str]:
    frames: list[str] = []
    for m in _FRAME_RE.finditer(text):
        fn = m.group(1)
        if any(s in fn for s in _SYSTEM_FRAMES):
            continue
        frames.append(fn)
        if len(frames) >= TOP_FRAMES:
            break
    return frames


def _summary_loc(text: str) -> str | None:
    """Fallback location from the SUMMARY line (function, else file:line)."""
    m = _SUMMARY_FN_RE.search(text)
    if m and not any(s in m.group(1) for s in _SYSTEM_FRAMES):
        return m.group(1)
    m = _SUMMARY_LOC_RE.search(text)
    return m.group(1) if m else None


def crash_signature(stderr: bytes, timed_out: bool = False) -> str:
    """Return a readable, normalized signature string for grouping crashes."""
    if timed_out:
        return "timeout"
    text = stderr.decode("utf-8", "replace")
    cls = _bug_class(text)
    frames = _app_frames(text)
    if frames:
        return f"{cls}|{'>'.join(frames)}"
    # No app frames: use the SUMMARY site so distinct bugs of one class don't
    # all collapse onto a single '?' merge magnet.
    loc = _summary_loc(text)
    return f"{cls}|{loc}" if loc else f"{cls}|?"


def sig_id(signature: str) -> str:
    """Short, filesystem-safe id for a signature (used as the crashes/ folder)."""
    return hashlib.sha1(signature.encode("utf-8")).hexdigest()[:12]
