# Crash captures & triage

**Results live in `FINDINGS.md`** (including the documented "none found so far"
explanation). This file describes the triage *methodology*.

One folder per **unique crash signature**, each containing: `input` (the raw
crashing bytes), `minimized_input` (reduced — Hypothesis's shrinker runs first
during the `@given` hunt, then a signature-preserving ddmin pass),
`sanitizer_report` (full stderr), `signature.txt`, `meta.json` (exit code,
signal, counts, `verified`, `low_confidence`), and a runnable `repro.sh`.

## Signature normalization — informed by a confirmed platform fact

**Do NOT dedup on the signal number.** Under the real ASan+UBSan build, ASan
intercepts *every* memory fault (null deref, heap overflow, stack overflow, …)
and, with `abort_on_error=1`, ends the process with **SIGABRT (exit 134)** — not
a raw SIGSEGV. Verified directly: a null deref under the real flags prints
`ERROR: AddressSanitizer: SEGV on unknown address … SUMMARY: AddressSanitizer:
SEGV … ==ABORTING` and exits 134, whereas the same code with no sanitizers exits
139 (raw SIGSEGV).

Consequence for Step 5: the signal is nearly constant (SIGABRT) across distinct
bugs, so the **crash signature must be built from the sanitizer report**, not the
signal. Plan:

1. Parse the sanitizer report for the bug class (`heap-buffer-overflow`,
   `SEGV`, `stack-overflow`, UBSan `runtime error: <kind>`).
2. Extract the top 3 **app** stack frames' **function names** (from `#0 … in
   <fn>`), dropping addresses, offsets, and file:line noise, and **filtering
   system/interceptor frames** (dyld, sanitizer runtime, and bare libc
   interceptors like `memcpy`/`strlen`) so the signature reflects the real
   application call site, not the interceptor.
3. Signature = `bug_class | frame1>frame2>frame3` (folder id = its sha1 prefix).

Refinements adopted after an independent triage-design review:

- **UBSan kind is canonicalized** — the operand data UBSan embeds in its message
  (`index 5 out of bounds for type 'char [3]'`) is stripped of digits, quoted
  types, and `[...]` bounds, so `index 5…` and `index 7…` fold to one signature
  instead of splitting one bug into many.
- **No-app-frames reports fall back to the SUMMARY site** (`file:line`/function)
  rather than collapsing every bug of a class onto a single `?` merge magnet.
- **Flakiness guard:** the representative is the shortest input that reproduces
  on ≥2 of 3 runs; if none is reliable the group is saved but flagged
  `low_confidence` and not minimized.
- **`repro.sh` exports the sanitizer env** (`ASAN/UBSAN_OPTIONS`) so a manual
  re-run produces the same report — UBSan needs `print_stacktrace=1` or it emits
  only a one-liner.

Timeouts (no report) get a fixed signature `timeout`, are triaged as a separate
DoS class, and are not minimized (each trial would wait the full timeout).
