# Agentic Fuzzing of a C JSON Parser

**Target:** `json-parser` (udp/json-parser, single-file C, commit `8ac4477`) · **Grammar:** ANTLR `grammars-v4` JSON
**Generator:** LLM-authored Hypothesis strategies, refined in a feedback loop

*(Body is the two-page write-up; mechanics live in Appendices A–C, which the
assignment excludes from the limit.)*

---

## 1. Design

**The problem.** Find inputs that crash a C library parsing a structured text
format. Random bytes mostly die in the lexer/rejection paths, so the deep
value-construction code where bugs live is rarely reached — a random `st.text()`
baseline covers **42%** of `json.c` versus **83%** for the evolved grammar-seeded
generator (2.0×; `eval/coverage.py`). The approach: hand a **language model the
format's formal grammar**, have it write a
[Hypothesis](https://hypothesis.readthedocs.io/) strategy generating inputs *in*
that language, and refine it across a few feedback-driven iterations. *(Target
choice: parson's latest release is hardened and yielded nothing; with the
instructor's confirmation that any listed library at a recent commit was
acceptable, the target is json-parser — see DECISIONS.md D10.)*

**Grammar and its adaptations.** The starting point is ANTLR `grammars-v4`'s JSON
grammar, used verbatim (`grammar/JSON.g4`). The interesting engineering is the
**gap between the formal grammar and what json-parser actually accepts** —
generating to the spec alone wastes budget on inputs the library treats
differently. Confirmed empirically (`grammar/adaptation-notes.md`):

| Deviation | Direction |
|---|---|
| A single trailing comma is accepted (`[1,2,]`, `{"a":1,}`) | **more lenient** than RFC 8259 |
| Duplicate object keys are accepted (`{"a":1,"a":2}`) | **more lenient** |
| Non-finite numbers are accepted (`1e309` → `inf`) | **more lenient** |
| A lone `-` parses as a number | **more lenient** |
| Trailing content after a value is rejected (`1abc`) | RFC-conformant / **stricter** |

Two properties shape generation: json-parser is a **two-pass iterative** parser,
so it has **no nesting-depth cap** and deep input does not stack-overflow; and its
`json_parse_ex` takes an explicit **length**, so embedded NUL bytes are testable
rather than truncating the input — the generator is told to emit them.
**These are deviations, not defects**: telling "more permissive than the spec"
apart from "a memory-safety bug" is a core judgment the pipeline is built to make,
and none of the above trips a sanitizer.

**Harness: crash vs. rejection vs. valid parse.** A ~90-line C driver
(`harness/harness.c`) reads stdin, calls `json_parse_ex`, and signals the outcome
by exit code — **0** valid, **2** well-formed rejection (*not* a bug; the parser's
error string is surfaced), **11** SKIP (oversized). `fuzz/runner.py` decides this
in one place and treats **anything outside the known set** — any other code, any
fatal signal, or a >5 s timeout — as a **crash**, so no sanitizer exit convention
is misread as a clean parse, and timeouts count as crashes (a hang is a DoS bug).
The build compiles library + harness with `-fsanitize=address,undefined
-fno-sanitize-recover=all -O0`; both flags are load-bearing. (Full exit contract
and flag rationale: **Appendix A**.)

**The feedback loop, and the signal that steers it.** With coverage
instrumentation forbidden, choosing a **proxy signal** was the hardest decision. A
first design — bucketing acceptance rate by the features present in each input —
was abandoned as statistically confounded (one broken feature poisons every
co-occurring bucket). The replacement separates roles, each mapped to exactly
**one** nameable edit applied **one per iteration** so the next measurement stays
interpretable:

| Role | Signal | Refinement action |
|---|---|---|
| Primary steer | production coverage gap | *add/upweight production P* |
| Primary steer | nesting-depth mass in a deep band | *generate deeper nesting* |
| Objective | new crash signature | *mutate structurally near the crash* |
| Repair | single-feature **probe** acceptance | *fix that feature's encoding* |
| Guardrail | accepted-structure novelty | *revert the last edit* |

The **probes** replace missing parser feedback with a controlled experiment
(perturb a valid baseline by exactly one feature); the **guardrail** catches the
loop's quiet failure mode (acceptance rising while accepted-structure diversity
falls = the model retreating to blander JSON); and a **differential oracle** runs
each iteration as a *findings* channel, flagging inputs json-parser accepts that a
strict RFC parser rejects — 24 such divergences in the committed run, surfacing the
trailing-comma leniency. (One correctness note: the oracle is forced RFC-strict on
finiteness, so it now also catches the `1e309`→inf leniency; **duplicate keys come
from the probes, not the oracle**, since Python accepts them too. Mechanics:
**Appendix B**.)

**Was the blind signal any good?** A post-hoc audit (`eval/proxy_validation.py`,
measurement-only, n=5) correlates each proxy component with the coverage the loop
could not see: accepted-structure **novelty tracks branch coverage** (Spearman
ρ≈+0.9) and raw **acceptance is negatively correlated** (ρ≈−0.7) — the guardrail
steers by the right quantity, and acceptance alone would mislead, exactly as
designed. Honestly, **deep-nesting mass does *not* buy coverage** (ρ≈−0.5):
re-entering the same object/array code adds no new branches, a real limit of that
steer.

## 2. Findings

**One real bug: UB on any object.** json-parser executes undefined behavior on
almost every object input; the minimal reproducer is **`{""`** (3 bytes). At
`json.c:437`, the first (memory-measuring) pass reuses the still-`NULL`
`object.values` pointer field as an integer counter — `NULL + n` pointer
arithmetic — which UBSan flags as `applying non-zero offset to null pointer`. It
is **UBSan-only** (an ASan-only build of the same input exits 0), so it is a
standards-violating UB, not memory corruption; but the assignment mandates
`-fsanitize=undefined` and counts `runtime error:` as a crash, so it is a valid
finding. It was carried through the full triage pipeline — deduped (4 object
inputs → 1 signature), shrunk by Hypothesis from `{"":0}` to `{""`, and verified —
and is committed under `crashes/` with a runnable `repro.sh` (mechanism in
`crashes/FINDINGS.md`).

**How the strategy evolved, and an honest note on variance.** Because finding #1
aborts on *every* object, a `hunt` build disables **only** the `pointer-overflow`
check (a documented triage trade-off; §3) so the loop can reach deeper. The
committed 5-iteration run (`runs/`, deepseek-chat, $0.024) reads as a clean
evolution — score 51 → 60 → 72 → 73 → 72 as it closed a production-coverage gap,
pushed nesting into the deep band (cap-mass 0.0 → 0.36 exactly when that steer
fired), and broadened diversity (novelty 16 → 34), with json-parser's own reject
reasons fed back into the refine prompts. **But this is a favorable draw.** Across
the live runs I observed, the count of iterations producing a *working* strategy
ranged from **0 to 5** (one run rolled back four of five on model-authored
Hypothesis-API bugs; another produced zero). Replaying the identical committed
strategies unseeded spreads the per-iteration scores by ±7–11 points, so I do
**not** claim any specific trajectory or measurable per-iteration *improvement* —
the spread is the real result. What *is* dependable is the loop's rollback/repair
**machinery**, not `deepseek-chat`'s per-run luck.

**Crashes beyond finding #1: none found, and why that's interpretable.** Neither
the loop nor direct adversarial probing (huge/edge numbers, NUL and control bytes,
unpaired surrogates, trailing commas, deep nesting) surfaced a second signature.
Deep nesting is **not** a vector: json-parser is iterative, so `[`×100000 parses
without stack overflow. The null result is interpretable because the pipeline
detects crashes when they exist — a positive control (injected heap overflow) is
caught end-to-end, and the triage pipeline was exercised on the *real* finding,
not only the control. Development was on macOS/arm64 (no LeakSanitizer), so a
`Dockerfile` reproduces everything on Linux: there the 85 tests pass, finding #1
still fires, and **LeakSanitizer over 300 evolved-generator inputs reports no
leaks** — closing the one sanitizer surface the mac build could not check.

**Under-tested areas.** The **serializer** is out of scope (json-parser has none).
The biggest gaps are the **object path behind finding #1** (a surgical
`no_sanitize` on just that function would unmask it) and **comment mode**
(`json_enable_comments`, left off) — both named in `crashes/FINDINGS.md`.

## 3. Challenges

**The steering signal was wrong more than once, and only re-measurement caught
it.** Beyond the confounded first design (§1), a subtler bug: the novelty metric
parsed each accepted input with recursive `json.loads`, which raised
`RecursionError` past ~1000 deep, scored zero, and so *penalised the very deep
inputs it was steering toward*. Worse, the whole per-iteration improvement story
was **max-of-noise**: unseeded with a live example database, replaying the
identical strategies carried ±7–11 points of variance, so a "best score" and a
guardrail revert were artifacts of one lucky draw. The fixes were reproducible
measurement (`run_seed` + `database=None`), noise-tolerance on the revert, and a
multi-seed acceptance gate. The lesson: a proxy signal can look like it works
while dominated by noise, and only re-measurement — not a green test — exposes it.
Both classes of bug were found by adversarial review, not by a passing test.

**A pervasive shallow bug forced a triage judgment.** Finding #1 masks the object
path, so the `hunt` build suppresses *only* the one `pointer-overflow` check the
known idiom trips, keeping every other check live. The trade-off — it would also
hide a *different* real pointer-overflow — is accepted explicitly and re-confirmed
on the full-sanitizer build.

**Crash deduplication met a platform surprise.** Under ASan every fault becomes
`SIGABRT` (exit 134), never a distinguishing `SIGSEGV`, so the signal is useless
for dedup. Signatures are built from the **sanitizer report** instead (bug class +
top application frames, addresses stripped, interceptor frames filtered, UBSan
operands canonicalized so `index 5` and `index 7` are one bug). Details in
Appendix B.

**What I'd do differently with coverage feedback.** The entire proxy apparatus —
probes, novelty guardrail, structural fingerprints — collapses into "did this
input reach new code," and the loop would converge in far fewer iterations.
Without it, the highest-value next steps are a stronger strategy-authoring model
(deepseek-chat spent most of the budget on rollback/repair) and a surgical
unmasking of the object path behind finding #1.

---

## Appendix A — Harness & build mechanics

*(Appendix — not counted against the two-page limit.)*

The harness (`harness/harness.c`) communicates outcome purely through its exit
code, and `fuzz/runner.py` is the single place classification is decided:

- **0** — accepted as valid JSON.
- **2** — well-formed rejection (**not a bug**); json-parser's own error string is
  written to stderr, a "why was this rejected" signal that steers refinement.
- **11** — SKIP: oversized input the harness declined to test — its own bucket, so
  "rejection" in the logs always means the *parser* rejected something. Unlike the
  parson harness there is **no NUL skip**: the length-taking `json_parse_ex` tests
  embedded NUL faithfully.
- **anything else, any fatal signal, or a >5 s timeout** — **crash**. The rule is
  "anything outside the known set," so a sanitizer's exit convention can never be
  silently misread as a clean parse. Timeouts count as crashes (a hang is a DoS
  bug per the assignment).

Build (`harness/build.sh`): `-fsanitize=address,undefined
-fno-sanitize-recover=all -fno-omit-frame-pointer -g -O0`. Two flags are
load-bearing: **`-fno-sanitize-recover=all`**, because UBSan is otherwise
*recoverable* — it prints `runtime error:` and continues, so a genuine UB input
would exit 0 and be logged as a valid parse (this exact flag surfaced finding #1);
and **`-O0`**, so the optimizer cannot fold away the very UB we are trying to
observe. Three build modes exist: `default` (faithful target), `control`
(injects a synthetic bug — the positive control, refused as a source of real
findings), and `hunt` (`default` minus the one `pointer-overflow` check).

## Appendix B — Proxy-signal & dedup mechanics

*(Appendix — not counted against the two-page limit.)*

**Probes (`fuzz/probes.py`) — the repair signal.** Each probe generates a
baseline-valid document perturbed by exactly *one* feature (unicode escapes,
extreme numbers, deep nesting, duplicate keys, empty containers, near-valid
malformed), so a low acceptance rate unambiguously indicts that one feature's
encoding — a controlled experiment standing in for the aggregate parser feedback
we lack. A probe measured against *both* json-parser and the oracle disambiguates
"generator emits malformed feature" (repair) from "json-parser deviation"
(finding); this is how the duplicate-key deviation surfaced.

**Guardrail — accepted-structure novelty.** A model can raise acceptance by
generating *blander* JSON, retreating from edge cases. Acceptance rising while the
count of distinct accepted structure fingerprints falls is that fingerprint, and
it triggers a rollback — guarded by a noise-tolerance margin (a real acceptance
rise *and* a >~2σ novelty drop), since diversity has run-to-run variance.

**Differential oracle (`fuzz/oracle.py`).** Python's stdlib `json` is the RFC
reference, but it is *not* strict by default — it accepts `NaN`/`Infinity`/`1e309`,
which RFC 8259 forbids. Left as-is that mislabels json-parser's *rejection* of
`NaN` as "stricter" and misses json-parser's genuine `1e309`→inf leniency.
`oracle_accepts` therefore forces finiteness-strictness via
`parse_constant`/`parse_float`, so the reference matches RFC and the non-finite
leniency surfaces. Duplicate keys are *not* an oracle finding (Python accepts them
too, so both agree) — that deviation comes from the probes.

**Dedup normalization (`fuzz/triage/signature.py`).** Under the real ASan+UBSan
build every fault aborts with `SIGABRT` (exit 134), so the signal is useless for
telling bugs apart; the signature is `bug_class | top-3 application frames` from
the sanitizer report, with addresses/offsets and file:line stripped (they shift
across builds), libc/interceptor frames filtered, and UBSan operand values
canonicalized so `index 5` and `index 7 out of bounds` collapse to one bug. A
SUMMARY-site fallback prevents distinct bugs of one class from merging onto a
single `?` when no app frame parses.

## Appendix C — Observed failure modes of the LLM as strategy author

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
