# Findings

**Status: one confirmed UBSan finding in json-parser (`8ac4477`), triaged below.
No memory-safety (ASan) crash found beyond it.**

`crashes/README.md` describes the triage *methodology*; this file records the
*result*. The one unique signature is committed under `crashes/<sig_id>/` with
its input, minimized reproducer, sanitizer report, `meta.json`, and a runnable
`repro.sh`.

## Finding #1 — UB: NULL-pointer arithmetic in the first-pass object measurement

- **Signature:** `ubsan:applying-non-zero-offset-to-null-pointer|json_parse_ex>main`
- **Site:** `vendor/json-parser/json.c:437` (and the sibling line `:447`).
- **Minimal reproducer:** `{""` (3 bytes, found by Hypothesis shrinking from
  `{"":0}`). Any object with **at least one member** triggers it; empty objects,
  arrays, strings, numbers, and booleans do **not**.
- **Class:** undefined behavior, **UBSan-only** — an ASan-only build of the same
  input exits 0, so this is a standards violation (pointer arithmetic on a NULL
  pointer), not a memory-corruption crash. The assignment's build mandates
  `-fsanitize=undefined` and Step 5.1 counts a `runtime error:` as a crash, so
  it is in scope as a finding.
- **Deterministic:** yes (exit 134 / SIGABRT every run); `crashes/<id>/repro.sh`
  reproduces it against the pinned build.

**Mechanism (read from the source, not guessed).** json-parser parses in two
passes: a first pass that *measures* how much memory the value needs, then a
second that fills it. During the first pass the object-member code reuses the
still-`NULL` `top->u.object.values` pointer field as an integer accumulator:

```c
/* json.c:437, state.first_pass, case json_object */
json_char **chars = (json_char **) &top->u.object.values;
chars[0] += string_length + 1;          /* NULL + n  ->  UB */
```

`values` has not been allocated yet in the first pass, so this is `NULL + n`
pointer arithmetic — the classic "use the pointer field as a counter" idiom.
It is harmless on normal hardware (nothing is dereferenced), which is why the
library ships with it, but it is undefined per the C standard and UBSan flags
it. It is **not** a memory-safety exploit; it is a real, reachable UB.

## Hunting past finding #1 — what else is (not) there

Because finding #1 fires on essentially every object, it masks the rest of the
object path under the full-sanitizer build. Two builds reach past it. The `hunt`
build disables **only** the `pointer-overflow` check globally (documented judgment
call in `harness/build.sh` and `DECISIONS.md`). The **`unmask`** build is strictly
better: `harness/apply_unmask.py` rewrites *only* finding #1's two `NULL + n` sites
to integer arithmetic on the same pointer-sized storage — value-identical, so
behaviour is unchanged — and then compiles with the **full** sanitizer set, so
`pointer-overflow` stays **live everywhere else** and a *different* object-path
pointer-overflow would still be caught (the pinned `vendor/` source is never
modified; `{""` still traps on `default`, confirming fidelity). Against these
builds:

- **The unmask build (pointer-overflow live): no second signature.** The evolved
  generator over 800 examples, a curated object-path adversarial set, and a
  first-pass memory-measurement stress (objects nested 100 000 deep, 500 000
  members wide, 200 000 duplicate keys) all parse cleanly — no ASan/UBSan abort,
  no timeout. This closes the `hunt` build's one blind spot.

- **The agentic loop** (5 iterations, `runs/`) produced a working generator that
  ran hundreds of valid/malformed inputs over the committed run — **no new crash
  signature** (the per-iteration score trajectory is within measurement noise;
  see report §2, but the crash-hunt null result does not depend on it).
- **Direct adversarial probing** (numbers with huge magnitude/exponent and
  thousands of digits; embedded NUL and control bytes; lone / leading-`+` /
  leading-dot number forms; lone and paired unicode surrogates; single and double
  trailing commas; deep nesting) — **no crash**.
- **Deep nesting is not a vector.** json-parser is a two-pass *iterative* parser,
  not recursive-descent, so `[` × 100000 parses without stack overflow — unlike
  a typical recursive JSON parser.
- **LeakSanitizer (Linux only): no leaks.** macOS/arm64 cannot run LSan, so the
  `Dockerfile` runs it on Linux: `eval/leakcheck.py` pushed 300 evolved-generator
  inputs through the hunt build with `detect_leaks=1` and found **no memory
  leaks**. The same container also confirms finding #1 reproduces on Linux (same
  `json.c:437` UB), so it is not a macOS-clang artifact.

So finding #1 is, as far as this budget reached, the one sanitizer-detectable
issue reachable from a parse-only harness on this commit.

## Why the "nothing-else" result is interpretable

A null result is only worth something if the pipeline can detect a crash when one
exists. Three things establish that:

1. **Positive control.** A build with one injected heap overflow
   (`-DPOSITIVE_CONTROL`) is detected, captured, deduplicated, minimized, and
   verified — proven by `fuzz/test_triage.py` and `fuzz/test_pipeline.py`.
2. **The triage pipeline was exercised on a REAL bug, not just the control.**
   Finding #1 was deduped (4 distinct object inputs → 1 signature), shrunk to
   `{""`, and verified through the same code path.
3. **Both sanitizers are proven live** under the project's real build flags, with
   symbolized stack traces (`fuzz/test_pipeline.py`).

## Non-crash findings (accepted-format deviations)

json-parser's real accepted language differs from RFC 8259 in several ways
(documented with reproducers in `grammar/adaptation-notes.md`): it **accepts** a
single trailing comma (`[1,2,]`, `{"a":1,}`), duplicate object keys, non-finite
numbers, and a lone `-`; it **rejects** trailing content after a value. The
differential oracle flagged dozens of these leniencies automatically during the
loop run (`runs/iter-5/stats.json`, `divergences: 24`). These are **not** bugs
and are deliberately not reported as such — none trips a sanitizer.

## What I'd try next with more time

- **Done — surgical unmask of the object path.** The `unmask` build above already
  does this (a line-precise, value-preserving rewrite rather than a whole-check
  suppression, since `json_parse_ex` is monolithic so a function-scoped
  `no_sanitize` would be no narrower than `hunt`). Result: no second signature.
  The remaining lever here is simply more budget/inputs against that clean build.
- json-parser with `json_enable_comments` on — a second accepted-format surface
  our harness currently leaves off.
- A stronger model for the loop: `deepseek-chat` frequently emitted
  Hypothesis-API-invalid strategies (see `runs/` and the report §3), so much of
  the budget went to rollback/repair rather than deep generation.
