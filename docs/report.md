# Agentic Fuzzing of a C JSON Parser

**Target:** `json-parser` (udp/json-parser, single-file C, commit `8ac4477`) · **Grammar:** ANTLR `grammars-v4` JSON
**Generator:** LLM-authored Hypothesis strategies, refined in a feedback loop

---

## 1. Design

### The problem

Find inputs that crash a C library parsing a structured text format. Random
bytes mostly die in the lexer/rejection paths, so the deep value-construction
code where bugs live is rarely reached — a random `st.text()` baseline covers
**42%** of `json.c` versus **83%** for the evolved grammar-seeded generator
(2.0×; `eval/coverage.py`). The approach: hand a **language model the format's
formal grammar**, have it write a
[Hypothesis](https://hypothesis.readthedocs.io/) strategy generating inputs *in*
that language, and refine it across a few feedback-driven iterations.

*(Target choice: parson's latest release is hardened and yielded nothing; with
the instructor's confirmation that any listed library at a recent commit was
acceptable, the target is json-parser. See DECISIONS.md D10.)*

### Grammar and its adaptations

The starting point is ANTLR `grammars-v4`'s JSON grammar, used verbatim
(`grammar/JSON.g4`). The interesting engineering is the **gap between the formal
grammar and what json-parser actually accepts** — generating to the spec alone
wastes budget on inputs the library treats differently. Confirmed empirically
(`grammar/adaptation-notes.md`):

| Deviation | Direction |
|---|---|
| A single trailing comma is accepted (`[1,2,]`, `{"a":1,}`) | **more lenient** than RFC 8259 |
| Duplicate object keys are accepted (`{"a":1,"a":2}`) | **more lenient** |
| Non-finite numbers are accepted (`1e309` → `inf`) | **more lenient** |
| A lone `-` parses as a number | **more lenient** |
| Trailing content after a value is rejected (`1abc`) | RFC-conformant / **stricter** than lenient readers |

Two properties shape generation: json-parser is a **two-pass iterative** parser
(not recursive-descent), so it has **no nesting-depth cap** and deep input does
not stack-overflow; and its `json_parse_ex` takes an explicit **length**, so
embedded NUL bytes are testable rather than truncating the input — NUL is a
classic bug source, so the generator is told to emit it.

**These are deviations, not defects.** Distinguishing "more permissive than the
spec" from "a memory-safety bug" is a core judgment the pipeline is built to
make; none of the above trips a sanitizer.

### Harness: crash vs. rejection vs. valid parse

A ~90-line C driver (`harness/harness.c`) reads stdin, calls
`json_parse_ex(&settings, buf, len, error)`, frees the result, and signals the
outcome purely through its exit code: **0** = valid; **2** = well-formed
rejection (**not a bug**; json-parser's error string goes to stderr, a "why
rejected" signal); **11** = SKIP (oversized — its own bucket, so "reject" always
means the *parser* rejected, and unlike the parson harness there is **no NUL
skip**: the length API tests NUL faithfully); anything else, any fatal signal, or
a >5 s timeout = **crash**. `fuzz/runner.py` decides this in one place and treats
*anything outside the known set* as a crash, so no sanitizer exit convention is
misread as a clean parse. Timeouts count as crashes (a hang is a DoS bug).

The build (`harness/build.sh`) uses `-fsanitize=address,undefined
-fno-sanitize-recover=all -fno-omit-frame-pointer -g -O0`. Two flags are
load-bearing: **`-fno-sanitize-recover=all`** (UBSan is otherwise *recoverable* —
it prints `runtime error:` and continues, so a genuine UB input would exit 0 and
log as valid; this exact flag surfaced the finding below), and **`-O0`** (so the
optimizer cannot fold away the UB we are trying to observe).

### The feedback loop, and the signal that steers it

The assignment forbids coverage instrumentation, which removes the compass real
fuzzers steer by. Choosing a **proxy signal** was the hardest design decision. A
first design — bucketing acceptance rate by the features present in each input —
was abandoned as statistically confounded: a rejected input with deep nesting
*and* unicode *and* big numbers cannot be attributed to one feature. The design
that replaced it separates four roles, each mapped to exactly **one** nameable
edit, applied **one per iteration** so the next measurement stays interpretable:

| Role | Signal | Refinement action |
|---|---|---|
| Primary steer | production coverage gap | *add/upweight production P* |
| Primary steer | nesting-depth mass in a deep band | *generate deeper nesting* |
| Objective | new crash signature | *mutate structurally near the crash* |
| Repair | single-feature **probe** acceptance | *fix that feature's encoding* |
| Guardrail | accepted-structure novelty | *revert the last edit* |

The **probes** (`fuzz/probes.py`) each generate a baseline-valid document
perturbed by exactly *one* feature, so a low acceptance rate unambiguously
indicts that feature — a controlled experiment replacing the parser feedback we
lack. The **guardrail** catches the way this loop fails quietly: a model can
raise acceptance by generating *blander* JSON, retreating from edge cases;
acceptance rising while accepted-structure diversity falls is that fingerprint,
and it triggers a rollback (guarded by a noise-tolerance margin, since diversity
has run-to-run variance — see §3). A **differential oracle** (`fuzz/oracle.py`)
runs every iteration as a *findings* channel: inputs json-parser accepts that a
strict RFC parser rejects are candidate leniencies — it flagged 24 divergences in
the committed final iteration, surfacing the **trailing-comma** leniency (and
unpaired-surrogate handling). Two care points: the reference is now forced
RFC-strict on finiteness, since Python's `json` otherwise accepts
`NaN`/`Infinity`/`1e309` — with that fix the oracle also catches the
**non-finite-number** (`1e309`→inf) leniency, which the committed run's lenient
oracle missed; and **duplicate keys come from the probes, not the oracle**, since
Python accepts them too.

**Was the blind signal any good?** A post-hoc audit (`eval/proxy_validation.py`,
measurement-only, n=5) correlates each proxy component with the coverage the loop
could not see: accepted-structure **novelty tracks branch coverage** (Spearman
ρ≈+0.9) and raw **acceptance is negatively correlated** (ρ≈−0.7) — the novelty
guardrail steers by the right quantity, and acceptance alone would mislead,
exactly as designed. Honestly, **deep-nesting mass does *not* buy coverage**
(ρ≈−0.5): re-entering the same object/array code adds no new branches, a real
limit of that steer.

## 2. Findings

### One real bug: undefined behavior on any object

**json-parser executes undefined behavior on almost every object input.** The
minimal reproducer is **`{""`** (3 bytes). At `json.c:437`, the first
(memory-measuring) pass reuses the still-`NULL` `object.values` pointer field as
an integer counter — `NULL + n` pointer arithmetic — which UBSan flags as
`applying non-zero offset to null pointer`. It is **UBSan-only** (an ASan-only
build of the same input exits 0), so it is a standards-violating UB, not memory
corruption; but the assignment mandates `-fsanitize=undefined` and counts
`runtime error:` as a crash, so it is a valid finding. It was carried through the
full triage pipeline — deduped (4 object inputs → 1 signature), shrunk by
Hypothesis from `{"":0}` to `{""`, and verified — and is committed under
`crashes/` with a runnable `repro.sh`. Full mechanism in `crashes/FINDINGS.md`.

### How the strategy evolved, and an honest note on variance

Because finding #1 aborts on *every* object, it masks the rest of the object path
under the full-sanitizer build. To let the loop reach deeper, a `hunt` build
disables **only** the `pointer-overflow` check (a documented triage trade-off;
§3) and the loop runs against it. The committed 5-iteration run (`runs/`,
deepseek-chat, $0.024) is a **clean evolution**: score 51 → 60 → 72 → 73 → 72 as
the loop closed a production-coverage gap, then pushed nesting into the deep band
(cap-mass 0.0 → 0.36 exactly when that steer fired), then broadened diversity
(novelty 16 → 34); the differential oracle flagged 24 leniency divergences, and
the refine prompts show json-parser's own reject reasons ("Unexpected EOF in
string", …) being fed back.

**Honest note on variance — this is a favorable draw.** Across the live runs I
observed under this setup, the number of iterations that produced a *working*
strategy ranged from **0 to 5**: one run rolled back four of five iterations on
model-authored Hypothesis-API bugs (`st.tuples` output into `",".join`, a
strategy compared to an int), and another produced *zero* working iterations. The
committed run is one of the good ones, kept because it best exhibits the intended
evolution — but the spread is the real result: `deepseek-chat` is a high-variance
strategy author, and it is the loop's rollback/repair *machinery*, not any single
run's luck, that is dependable. (A one-line prompt note about the tuple pitfall
reduced but did not remove the failures.)

### Crashes beyond finding #1: none found, and why that's interpretable

Neither the loop nor direct adversarial probing (huge/edge numbers, NUL and
control bytes, unpaired surrogates, single/double trailing commas, deep nesting)
surfaced a second signature. Deep nesting in particular is **not** a vector here:
json-parser is iterative, so `[`×100000 parses without stack overflow. The null
result is interpretable because the pipeline is shown to detect crashes when they
exist — a positive control (injected heap overflow) is caught end-to-end, and the
triage pipeline was exercised on the *real* finding above, not only the control.
Development was on macOS/arm64, where LeakSanitizer is unsupported, so a
`Dockerfile` reproduces everything on Linux: there the 85 tests still pass,
finding #1 still fires (not a macOS-clang artifact), and **LeakSanitizer over 300
evolved-generator inputs reports no leaks** — closing the one sanitizer surface
the mac build could not check.

### Under-tested areas

The **serializer** is out of scope (json-parser has none). The biggest gaps are
the **object path behind finding #1** (a surgical `no_sanitize` on just that
function would unmask it) and **comment mode** (`json_enable_comments`, which our
harness leaves off) — both named in `crashes/FINDINGS.md`.

## 3. Challenges

**The steering signal was the hard part, and it was wrong more than once.** The
first version was statistically confounded (§1). A second, subtler bug: the
coverage/novelty metric parsed each accepted input with Python's `json`, which
*recurses*, so any input past ~1000 deep raised `RecursionError`, was swallowed,
and scored zero — the loop was penalising the very deep inputs it steered toward.
Both were found by adversarial review, not by a passing test.

**The signal looked reproducible but wasn't — an adversarial critic caught it.**
Per-iteration scores told a tidy improvement story until the *identical*
committed strategies were re-run: unseeded, with a live Hypothesis example
database, the numbers carried ±7–11 points of variance, so a "best score" and a
guardrail revert were artifacts of one lucky draw. The fix was reproducible
measurement (`run_seed` + `database=None`) and reporting distributions, plus
noise-tolerance on the revert and a multi-seed acceptance gate. The lesson: a
proxy signal can look like it works while dominated by noise, and only
re-measurement — not a green test — exposes it.

**A pervasive shallow bug forced a triage judgment.** Finding #1 aborts on every
object, masking the object path. Rather than stop, the `hunt` build suppresses
*only* the one `pointer-overflow` check that this known idiom trips, keeping every
other ASan/UBSan check live, so the loop can hunt for deeper bugs. The trade-off
— it would also hide a *different* real pointer-overflow — is accepted explicitly
and re-confirmed on the full-sanitizer build.

**Crash deduplication met a platform surprise.** Under ASan every fault becomes
`SIGABRT` (exit 134), never a distinguishing `SIGSEGV`, so the signal is useless
for dedup. Signatures are built from the **sanitizer report** instead — bug class
plus the top application frames, with addresses/offsets stripped and
libc/interceptor frames filtered; UBSan messages are canonicalized (so `index 5`
and `index 7 out of bounds` are one bug).

**What I'd do differently with coverage feedback.** The entire proxy-signal
apparatus — probes, novelty guardrail, structural fingerprints — collapses into
"did this input reach new code", and the loop would converge in far fewer
iterations. Without it, the highest-value next steps are a stronger strategy-
authoring model (deepseek-chat spent most of the budget on rollback/repair) and a
surgical unmasking of the object path behind finding #1.

---

## Appendix: Observed failure modes of the LLM as strategy author

*(Appendix — not counted against the two-page limit.)* A recurring, somewhat
novel observation across the live runs is **how** `deepseek-chat` fails when asked
to author a Hypothesis strategy. These are not JSON mistakes — they are
property-based-testing–API mistakes, and they recur across independent runs. The
value for this project is that **every one is contained by a loop guard** (a
build-time rejection, a mid-run rollback, or the in-iteration repair re-prompt),
so a broken generation costs one iteration rather than the run or a false finding.

| Failure mode | Example (verbatim) | Seen | Caught by |
|---|---|---|---|
| **Hallucinated API** — a strategy/kwarg that doesn't exist | `module 'hypothesis.strategies' has no attribute 'json_strings'`; `booleans() got an unexpected keyword argument 'probability'` | 4 iters / 3 runs | `screen_code` import → build-reject → re-seed with the error |
| **Unresolved strategy used as a value** — a `SearchStrategy` object where a *drawn* value is expected | `'<' not supported between instances of 'LazyStrategy' and 'int'` (also `…and 'float'`) | 5 iters / 2 runs | mid-run `GeneratorError` → rollback / repair |
| **Wrong drawn shape** — `st.tuples(...)` output fed to `",".join`, or a rendered string consumed as a `(k,v)` tuple | `sequence item 0: expected str instance, tuple found`; `too many values to unpack (expected 2)` | 4 iters / 2 runs | mid-run `GeneratorError` → rollback / repair |
| **Module-scope ordering** — a name referenced before it is defined | `NameError: name 'json_value' is not defined` | 1 iter / 1 run | import error → rollback |

Two implications worth stating. First, the dominant failures are *unresolved
strategy vs. drawn value* and *wrong drawn shape* — the model reasons about the
JSON it wants but loses track of Hypothesis's two-level (strategy → example)
semantics. A one-line prompt note about the `st.tuples` pitfall reduced but did
not eliminate these. Second, this is why the loop's **guard/repair machinery, not
the model's per-run reliability, is the durable contribution**: run-to-run the
count of clean iterations ranged 0–5 (§2), yet no failure ever escaped a guard.
