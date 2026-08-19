# Agentic Fuzzing of a C JSON Parser

**Target:** `parson` (single-file C JSON library) · **Grammar:** ANTLR `grammars-v4` JSON
**Generator:** LLM-authored Hypothesis strategies, refined in a feedback loop

---

## 1. Design

### The problem

Given a C library that parses a structured text format, find inputs that crash it.
Random bytes are near-useless against a parser: it rejects them at the first
character, so the deep code where bugs live is never reached. The approach here
is to hand a **language model the format's formal grammar** and have it write a
[Hypothesis](https://hypothesis.readthedocs.io/) strategy that generates inputs
*in* that language, then improve that strategy across a small number of
iterations using feedback from actual runs.

### Grammar and its adaptations

The starting point is the JSON grammar from ANTLR's `grammars-v4` repository,
used verbatim (`grammar/JSON.g4`). The interesting engineering is the **gap
between the formal grammar and what parson actually accepts**, since generating
to the spec alone would waste budget on inputs the library handles differently.
Three deviations were confirmed empirically and traced to their mechanism in the
source (`grammar/adaptation-notes.md`):

| Deviation | Direction | Mechanism |
|---|---|---|
| Trailing content after a complete value is ignored (`1abc` parses as `1`) | parson **more lenient** than RFC 8259 | `json_parse_string` returns `parse_value(...)` without requiring the input be consumed (`parson.c:1390`) |
| A lone `-` parses as the number `0` | parson **more lenient** | `strtod("-")` consumes nothing; `is_decimal(s, 0)`'s `while (length--)` never executes, so a zero-length "number" is accepted (`parson.c:404`) |
| Duplicate object keys are rejected | parson **stricter** | `json_object_add` returns `JSONFailure` when the key is already present, failing the whole parse (`parson.c:599`) |

Two further constraints shape generation: parson caps nesting at
`MAX_NESTING = 2048` (deeper input is *cleanly rejected*, not a crash), and it
has no length-taking parse API, so an embedded NUL byte silently truncates input.

**These are deviations, not defects.** Distinguishing "the library is more
permissive than the spec" from "the library has a memory-safety bug" is a core
judgment the pipeline is built to make, and none of the three produces a
sanitizer report.

### Harness: crash vs. rejection vs. valid parse

A ~90-line C driver (`harness/harness.c`) reads stdin, calls `json_parse_string`,
frees the result, and communicates the outcome purely through its exit code:

- **0** — input accepted as valid JSON
- **2** — well-formed rejection of malformed input (**not a bug**)
- **10 / 11** — SKIP: the harness *could not test the input faithfully* (embedded
  NUL, oversized). This is a **harness limitation, kept in its own bucket** so
  that "rejection" in the logs always means the parser rejected something.
- anything else, any fatal signal, or a >5 s timeout — **crash**

`fuzz/runner.py` is the single place this is decided, and it deliberately treats
*anything outside the known set* as a crash, so a future sanitizer exit
convention cannot be silently misread as a clean parse. Timeouts count as
crashes: a parser that can be frozen by crafted input is a denial-of-service bug.

The build (`harness/build.sh`) uses `-fsanitize=address,undefined
-fno-sanitize-recover=all -fno-omit-frame-pointer -g -O0`. Two flags are
load-bearing: **`-fno-sanitize-recover=all`**, because UBSan is otherwise
*recoverable* — it prints `runtime error:` and continues, so a genuine
undefined-behaviour input would exit 0 and be recorded as a valid parse; and
**`-O0`**, so the optimizer cannot fold away the very UB we are trying to
observe.

### The feedback loop, and the signal that steers it

The assignment forbids coverage instrumentation, which removes the compass real
fuzzers steer by. A **proxy signal** had to be chosen, and this was the hardest
design decision in the project.

A further constraint made it harder: **parson returns `NULL` with no error
message.** So acceptance rate can tell us *that* 60 % of inputs were rejected but
never *which* production the parser choked on. There is no parser-side "why".

The first design — bucketing acceptance rate by the features present in each
input — was **abandoned as statistically confounded**: a rejected input
containing deep nesting *and* unicode escapes *and* big numbers cannot be
attributed to one feature, so a single broken feature poisons several buckets and
sends the model to "fix" generators that were fine.

The design that replaced it separates four roles, each mapped to exactly **one**
nameable edit, applied **one per iteration** so the next measurement stays
interpretable:

| Role | Signal | Refinement action |
|---|---|---|
| Primary steer | production coverage gap | *add/upweight production P* |
| Primary steer | nesting-depth mass vs. the 2048 cap | *generate nesting 1500–2048* |
| Objective | new crash signature | *mutate structurally near the crash* |
| Repair | single-feature **probe** acceptance | *fix that feature's encoding* |
| Guardrail | accepted-structure novelty | *revert the last edit* |

The **probes** (`fuzz/probes.py`) are the answer to the missing error messages:
each generates a baseline-valid document perturbed by exactly *one* feature, so a
low acceptance rate unambiguously indicts that feature — a controlled experiment
replacing the parser feedback we do not have.

The **guardrail** addresses the way this loop fails quietly: a model can raise
acceptance rate by generating *blander* JSON, retreating from the edge cases that
find bugs. Acceptance rising while the diversity of accepted structures falls is
that fingerprint, and it triggers a rollback to the best-scoring strategy.

A **differential oracle** (`fuzz/oracle.py`) runs every iteration but is
deliberately a *findings* channel, not a steering one: inputs parson accepts that
a strict parser rejects are candidate leniency deviations. The two
parson-is-lenient deviations were found this way; the third (duplicate keys,
where parson is *stricter*) came from the single-feature probes, whose low
acceptance the oracle then disambiguated as a library deviation rather than a
broken generator.

---

## 2. Findings

### Crashes: none found so far — and why that is interpretable

**No memory-safety crash in parson has been found at the time of writing.** That
statement is only worth anything if the pipeline can be shown to *detect* a crash
when one exists, which is why the project carries a **positive control**: a build
with one deliberately injected heap overflow (`-DPOSITIVE_CONTROL`). The pipeline
detects it, captures the sanitizer report, deduplicates it, minimizes the input,
and verifies the reproducer. The control build reports its own identity, and the
live loop refuses to gather findings from anything but the real target — so a
synthetic crash can never be reported as a parson bug.

The most likely explanation for the null result is the **target version**: the
build is pinned to parson 1.5.3, the *latest release*, which sits after a chain
of memory-safety fixes. Its two historical overflow bugs (#133, #204) are in the
**serialization** path and require multi-gigabyte inputs, so a parse-only harness
cannot reach them. Deep nesting — the classic recursive-descent crash — is
cleanly guarded at 2048.

The assignment states that each candidate is given a specific pinned commit;
**that commit has not yet been provided**, so 1.5.3 is a documented placeholder
(`vendor/parson/PROVENANCE.md`). Re-pinning to the assigned commit is a one-line
change.

### What was found instead

Three confirmed behavioural deviations between parson and RFC 8259, each traced
to its mechanism in the source (§1). These are genuine characterisation results
about the library's real accepted language — the deliverable the assignment asks
for in Step 1 — and the duplicate-key finding in particular was surfaced by the
probe design rather than by reading the source.

### Under-tested areas

Honestly assessed, the parts of the grammar least exercised are: the
**serializer** (a round-trip harness mode exists but the loop does not use it —
this is where parson's known historical bugs actually live), **unicode escape
handling** at the surrogate-pair boundary, and **numeric edge cases** near
`strtod`'s limits. With more time these are where I would aim next, alongside
running against an older, less-hardened commit.

---

## 3. Challenges

**The steering signal was the hard part, and it was wrong twice.** The first
version was statistically confounded (above). The second version — after the
confound was fixed — contained a subtler and more instructive bug: the
coverage/novelty metric was computed by parsing each accepted input with Python's
`json` module, which *recurses*. Any input deeper than ~1000 raised
`RecursionError`, was silently swallowed, and contributed **zero** productions.
Since the loop deliberately steers toward depth 1500–2048, the signal was
actively penalising the very inputs it was asking for, then rolling back the edit
it had just requested. The loop could report "progress" while going in circles.
This is exactly the quiet failure the assignment warns about, and it was found by
adversarial review rather than by any test passing or failing.

**Crash deduplication forced a judgment call with a platform surprise.** The
plan was to key crash signatures on the fatal signal plus stack frames. Measuring
it showed that under ASan *every* memory fault — null dereference, heap overflow,
stack overflow — is intercepted and turned into `SIGABRT` (exit 134), never the
raw `SIGSEGV` (139) it would otherwise be. The signal is therefore nearly
constant across genuinely different bugs and useless for dedup. Signatures are
built from the **sanitizer report** instead: bug class plus the top three
*application* frames, with addresses, offsets and line numbers stripped (they
shift between builds) and libc/sanitizer interceptor frames filtered (they would
otherwise mask the real call site). UBSan messages needed extra normalization
because they embed operand values — `index 5 out of bounds` and `index 7 out of
bounds` are one bug, and without canonicalization they would be reported as two.

**Distinguishing a harness limitation from a parser rejection.** Inputs
containing a NUL byte cannot be faithfully tested through parson's
NUL-terminated API. Folding them into "rejected" would have quietly corrupted
the acceptance-rate signal with cases the parser never actually saw, so they get
their own outcome bucket.

**What I would do differently with more time or coverage feedback.** With
coverage available, the entire proxy-signal apparatus — probes, novelty
guardrail, structural fingerprints — collapses into "did this input reach new
code", and the loop would converge in fewer iterations with far less machinery.
Without it, I would next add the serializer round-trip to the loop, and run the
same pipeline against a deliberately older parson commit to measure the loop's
bug-finding ability against a *known* ground truth rather than an open question.

---

*Artifacts: `README.md` (reproduce from a clean checkout), `DECISIONS.md` (every
design decision, the independent critique it received, and how it was resolved),
`grammar/adaptation-notes.md`, `crashes/FINDINGS.md`, `runs/`.*
