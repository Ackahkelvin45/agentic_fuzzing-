# Design decisions & critique log

Every major setup decision was proposed with reasoning, then challenged by an
independent critic before being accepted. This log records each decision, the
strongest challenge, and how it was resolved — so the *why* behind the project
is auditable.

## D1 — Target library & pinned version

- **Proposed:** parson `1.5.3 @ ba29f4e` (latest release) as the target.
- **Critique:** the latest release is the most *hardened* commit; a "0 crashes"
  result there is uninterpretable — it can't distinguish a working pipeline from
  a broken one. Need a **positive control**: a target known to crash.
- **Resolved:** keep 1.5.3 as the honest exploratory target, **and** add a
  positive control. Investigation showed parson's historical overflow bugs
  (#133, #204) are in the *serialization* path and need multi-GB inputs — not
  reachable by a small-input parse-only harness — so instead of pinning an old
  commit (which also risks overfitting the fuzzer to one known bug), the
  positive control is a **synthetic macro-guarded injected bug**
  (`-DPOSITIVE_CONTROL`). Email Prof. D'Amorim for the real assigned commit.

## D2 — Harness design

- **Proposed:** read stdin → NUL-terminate → `json_parse_string` → free;
  exit 0 = valid, 2 = reject; sanitizer aborts = crash; parse-only.
- **Critique:** (a) `json_parse_file` does NOT dodge embedded-NUL truncation —
  both entry points walk a NUL-terminated `char*`, so reject NUL inputs
  explicitly. (b) UBSan does not abort by default — MUST build with
  `-fno-sanitize-recover=all` or a UB input exits 0 and is misread as valid.
  (c) Skipping serialization is an acceptable scope cut but is real lost surface.
- **Resolved:** adopted all three. Harness rejects embedded-NUL (exit 2) and
  caps input size; `-fno-sanitize-recover=all` is in the build; added an
  optional `-DROUNDTRIP` mode (parse→serialize→free) to reach serializer surface.

## D3 — Build & sanitizer configuration

- **Proposed:** clang `-fsanitize=address,undefined -fno-sanitize-recover=all
  -O1`; vendor parson by copying source + PROVENANCE.
- **Critique:** prefer **`-O0`** so the optimizer can't fold away the UB we want
  to observe; add `UBSAN_OPTIONS=print_stacktrace=1` (UBSan gives no trace
  otherwise) and `ASAN_OPTIONS` extras; **LSan is unsupported on macOS/arm64**
  (leaks won't be caught) — document it; parson is MIT so copy `LICENSE`
  verbatim. Vendoring-by-copy endorsed over submodule.
- **Resolved:** adopted `-O0`, the richer sanitizer options (in `runner.py`),
  LICENSE copy, and the documented LSan limitation. A Linux repro path is noted
  as a grader-parity follow-up.

## D4 — Project layout & tooling

- **Proposed:** `vendor/ harness/ grammar/ fuzz/ crashes/ docs/`, Python +
  Hypothesis via `requirements.txt`.
- **Critique:** missing a home for the committed **per-iteration LLM
  artifacts** (prompts/responses/evolved strategies/stats) and the token/$
  accounting; no top-level README or single entrypoint; unpinned Hypothesis is a
  reproducibility liability; the loop must support an **offline `--replay`** so a
  grader reproduces findings without spending API money.
- **Resolved:** added `runs/` (committed iteration evidence + `evolution.md` +
  `cost.md`), `README.md`, one `run.sh` entrypoint, **pinned** `hypothesis`,
  `.gitignore` for secrets, and a `--replay` mode + env-only API key in
  `fuzz/loop/agent.py`. `crashes/<sig>/` holds input, minimized input,
  sanitizer report, exit code, and `repro.sh` per unique signature.

## D5 — Proxy signal for the agentic loop (no coverage instrumentation)

- **Proposed:** three co-equal steering signals — acceptance rate bucketed by
  generated structure, structural coverage + depth, crash signatures — with the
  differential oracle as a findings channel. Carried the hard constraint that
  parson returns NULL with no error message, so "why was it rejected" has no
  parser-side source.
- **Critique:** (a) **acceptance-bucketed-by-structure is statistically
  confounded** — multi-feature inputs can't attribute a rejection to one feature
  without parser feedback, so one broken feature poisons several buckets;
  (b) that signal churns against structural-coverage (both say "fix a feature");
  (c) **5 iterations can't act on three signals in parallel** — sequence and
  optimize one first (coverage/depth); (d) the loop "fails quietly" (acceptance
  rises while diversity narrows) with no guardrail; (e) add cap-distance to 2048.
- **Resolved (with one nuance).** Replaced confounded bucketing with
  **single-feature probes** (`fuzz/probes.py`) for unambiguous attribution;
  made coverage/depth/cap-distance the **primary** steer and probes a **repair**
  signal; added a **novelty guardrail** (acceptance↑ + distinct-accepted↓ →
  revert). `decide_refinement()` emits exactly ONE nameable edit per iteration
  with a fixed priority, proven actionable offline in `fuzz/test_signal.py`
  (each signal → a distinct action; guardrail reverts only on quiet failure).
  Nuance kept over the critic: probes stay for repair, not cut entirely.
- **Bonus finding:** the probe design surfaced a THIRD parson deviation
  (duplicate keys rejected); the **oracle disambiguation** in `classify_probes`
  correctly labels it a *finding*, not a generator bug — vindicating keeping the
  oracle wired in.

## D6 — Live loop + LLM provider (DeepSeek)

- **Proposed:** provider-agnostic `call_llm` (DeepSeek default via an
  OpenAI-compatible endpoint, stdlib-only, MOCK mode for offline runs),
  `seed_prompt`/`refine_prompt` from the grammar + adaptation notes, safe
  extraction of model code, and a real `run_live` committing per-iteration
  artifacts. DeepSeek chosen because the assignment is provider-agnostic and it
  fits the small/mid-tier ~$5 budget.
- **Critique (live-loop critic):** (F1) `run_strategy` caught only
  `AssertionError`, so any draw-time error killed the whole loop; (F2) a
  type-checking-but-garbage strategy burned a full 500-example iteration —
  the n=40 acceptance check only *warned*; (F3) `revert_last_edit` was a label
  with **no rollback**, and a 'both-down' regression wasn't caught at all;
  (F4) the $5 cap can't bind (runs cost cents) and there was **no wall-clock
  guard**, so one deep-nesting iteration could blow the 10-min cap; (F5) the
  `screen_code` denylist was trivially bypassable and `_productions_in` used
  substring matching, so `null`/`true` inside any text emptied `productions_gap`
  and silently defeated the primary steering signal.
- **Resolved (all must-fixes adopted):** broad exception handling ->
  `GeneratorError` so a bad generator fails one iteration, not the loop (F1);
  acceptance is now a **gate** with one in-iteration repair re-prompt before
  spending the full run (F2); **real rollback** to the best-scoring strategy via
  a `_score`, with a tolerance band against jitter (F3); **per-run (10-min) and
  overall (40-min) wall-clock caps** added to the run and `StopController` (F4);
  `screen_code` is now an **AST import-allowlist** (blocks `from os import
  system`, `getattr`, dunder access) and productions are detected
  **structurally via `json.loads`** (added the missing `number` production) (F5).
  Nice-to-have (full process isolation of model code) deferred but documented:
  the AST screen is a footgun-guard, not a sandbox — run the loop in a throwaway
  environment.

## D7 — Crash triage & dedup normalization (Step 5)

- **Proposed:** signature = `bug_class | top-3 function-name frames` from the
  sanitizer report (not the signal, since ASan collapses all faults to SIGABRT);
  signature-preserving delta-debug (ddmin) minimizer complementing Hypothesis's
  shrinker; verify by standalone re-run; save one folder per signature. Proven
  on a synthetic multi-bug harness (dedup groups variants, splits distinct bugs,
  minimizes to the marker, verifies).
- **Critique (triage-design critic):** architecture sound, but (1) **UBSan
  over-splits** — `split(":")[0]` leaves operand data (`index 5…` vs `index 7…`)
  in the signature, splitting one bug into many (verified live); (2)
  **under-splitting** — bare libc interceptor frames (`memcpy`) weren't filtered
  so the site could be an interceptor, and a `?`-frame fallback merged all bugs
  of a class; (3) **no flakiness guard** — the representative was the shortest
  raw input even if it reproduced flakily; (4) `repro.sh` set no sanitizer env,
  so a manual UBSan re-run wouldn't match the saved signature.
- **Resolved (all must/should-fixes adopted):** UBSan kind is **canonicalized**
  (strip digits/quoted types/`[...]`); interceptor frames filtered and a
  **SUMMARY-site fallback** replaces the `?` merge magnet; a **flakiness guard**
  picks the shortest input reproducing ≥2/3 (else `low_confidence`, unminimized);
  `repro.sh` exports the sanitizer env. Locked with 4 regression tests in
  `fuzz/test_triage.py` (now 14 assertions).

## D8 — Full adversarial audit (assignment · plan · code · tests · safety)

A five-critic review audited the whole project against the assignment, the
recorded decisions, the code, the tests, and a grader/security walkthrough.
It found real defects that all previous suites had passed over. Adopted:

**Assignment compliance**
- **Two-page report was missing entirely** — the largest graded artifact. Added
  `docs/report.md` (design / findings / challenges).
- **No crash deliverable** — the assignment requires findings *or* a documented
  "none found + why + what next". Added `crashes/FINDINGS.md`.
- **500-examples-per-iteration was exceeded** (~660–700: main run + gate +
  probes). The budget is now split across everything an iteration sends to the
  harness and measured at exactly 500.
- `runs/` held a MOCK run that read like a real one. Mock artifacts are now
  self-labelling in three places (`stats.json:mode`, a `MOCK_RUN.md` per
  iteration, a banner in `evolution.md`).

**Correctness (each verified by execution, each now regression-tested)**
- `decide_refinement` raised `KeyError` when a gated iteration passed `{}` as the
  previous summary — killing the run *before* `cost.md` was written.
- A failed first iteration left `current_code = None`, so the model was sent the
  literal text `None` as "the current strategy", silently burning every later
  iteration. The loop now re-seeds whenever it has no usable strategy.
- `revert_last_edit` was a **no-op**: `best_code` was overwritten one line before
  the revert used it. `best` also ratcheted *down* (57→54→51…) because every
  non-regressed iteration overwrote it. Now the edit is decided before `best` is
  mutated, and `best` only ever improves.
- A crash returned `_score = 1e6`, making the tolerance band ~1e5 so every later
  iteration counted as a regression — the loop froze on the first crash. Crash
  dominance is now a separate flag, not a term in the scalar.
- **The primary steering signal was self-defeating**: production detection used
  recursive `json.loads`, which raised `RecursionError` on inputs deeper than
  ~1000 — exactly what `cap_distance` steers toward. Those inputs contributed
  zero productions and collapsed into one novelty bucket, so the loop penalised
  the very edit it had just requested. Walk is now iterative with a raised parse
  limit; deep inputs score *higher*, as intended.
- `FlakyFailure` (a crash that doesn't reproduce identically during shrinking)
  was being converted into "broken generator" and discarded. Timeouts are the
  flakiest crash class and the assignment counts them as crashes — now kept.
- `extract_strategy` took the *last* fence, so a trailing ```bash block won over
  the real code. Now prefers the longest python-tagged block and strips the tag.
- `oracle_accepts` let `RecursionError` escape on deep input; the loop then
  blamed the model for the harness's own limitation.

**Safety**
- `screen_code`'s allowlist was **bypassed** by
  `hypothesis.internal.escalation.os.system(...)` (demonstrated executing during
  review). Blocked submodules and dangerous attribute names are now rejected too.
  It remains a footgun-guard, not a sandbox — the honest framing is kept.
- The API key (loaded from `.env` into the environment) was passed to every one
  of thousands of harness subprocesses. `run_once` now passes a minimal env
  allowlist.

**Portability / hygiene**
- `assert_real_target` was documented as "Enforced" but called only from a test —
  the live loop could have triaged the positive control's synthetic bug as a real
  parson finding. `run_live` now calls it.
- Tests hardcoded `clang`; they honor `CC` now (the grader is likely on Linux),
  and Linux runtime frame names were added to the triage frame filter.
- `run.sh` gained a venv guard, and stale assertion counts were removed from the
  docs rather than restated.

**Not adopted:** a package refactor to remove `sys.path` manipulation (invasive
for the benefit; the monotonic-growth bug was fixed instead), and splitting
`agent.py` (the one defensible seam, the signal block, already has its own test
file and clean boundary — revisit only if it grows).

Regression coverage for all of the above: `fuzz/test_loop.py` (36 assertions).

### D8 addendum — post-refinement audit (Phase 7)

An independent auditor re-checked the refined state against the assignment and
re-tested every D8 claim. It confirmed 10 of 11 fixes and found three things the
first pass missed, all now fixed:

- **A NEW `screen_code` bypass.** The `ImportFrom` handler validated only the
  *module*, not the imported *names*, so `from hypothesis import internal` and
  `from hypothesis.database import DirectoryBasedExampleDatabase` passed — the
  latter writes files at import time (demonstrated executing). Imported names are
  now screened, and `hypothesis.database` is a blocked submodule. Six regression
  tests were added, plus three asserting legitimate strategies (`st.composite`,
  `st.recursive`) still pass, so the guard cannot be tightened into uselessness.
- **A false claim in our own `crashes/FINDINGS.md`** — it said the mock loop ran
  "5 iterations × 500 examples" when the committed evidence showed far fewer
  (the README's example command uses a small `--max-examples`, and the budget
  split floors the main run). Rewritten to state exactly what was run, and to
  say plainly that a null result from a small non-adversarial sample is **not**
  evidence that parson is crash-free.
- **An internal contradiction in `docs/report.md`** — it credited the
  differential oracle with all three parson deviations, but the duplicate-key
  one (parson *stricter*) came from the probes; the oracle only disambiguated it.

Two assignment requirements the first pass had left partial were also completed:
**per-input logging** (Step 4.3 — `runs/iter-N/per_input_log.jsonl` records
outcome, exit code, signal, and sanitizer output per input) and **crash
signatures in the LLM summary** (Step 4.4 — accumulated across iterations so the
model can steer toward known crash regions instead of rediscovering them).

**Standing limitation, recorded honestly rather than papered over:** the loop has
never been run against a live model. Every artifact in `runs/` is a MOCK run with
a fixed canned generator, so *strategy evolution itself is undemonstrated*. This
is the single largest remaining gap and it needs an API key and a few cents of
spend, not more engineering.
