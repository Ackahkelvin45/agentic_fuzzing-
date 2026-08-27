# Agentic Fuzzing — json-parser (JSON)

Blackbox, grammar-driven fuzzing of the [json-parser](https://github.com/udp/json-parser)
C JSON library. An LLM turns a formal JSON grammar into a
[Hypothesis](https://hypothesis.readthedocs.io/) strategy, generated inputs are
run against json-parser built with AddressSanitizer + UndefinedBehaviorSanitizer,
and the strategy is refined across a small budget of iterations using a proxy
signal (there is no coverage instrumentation).

**Start with [`docs/report.md`](docs/report.md)** — the two-page write-up
(design, findings, challenges). [`DECISIONS.md`](DECISIONS.md) records every
design decision, the independent critique it received, and how it was resolved.
The headline result is a confirmed UBSan finding reachable from any object input
— see [`crashes/FINDINGS.md`](crashes/FINDINGS.md).

## Quick start (reproduce from a clean checkout)

```bash
./run.sh all      # setup venv + build harness + classification check + baseline
```

Individual steps: `./run.sh setup | build | check | baseline | test`.
Requires a C compiler with ASan/UBSan (Apple clang or clang/gcc on Linux),
Python 3.11+, and network access only for the one-time `pip install`.

**Reproduce on Linux (grader parity, + LeakSanitizer):**

```bash
docker build -t agentic-fuzz .
docker run --rm agentic-fuzz                        # 85 assertions on Linux
docker run --rm --cap-add=SYS_PTRACE agentic-fuzz \
    .venv/bin/python eval/leakcheck.py              # LeakSanitizer leak hunt
```

Verified in-container: 85/85 tests pass on Linux, finding #1 reproduces (so it is
not a macOS-clang artifact), and LeakSanitizer reports **no leaks** over 300
inputs — a surface macOS/arm64 cannot check.

## Layout

```
vendor/json-parser/  pinned source (8ac4477) + LICENSE + PROVENANCE
harness/           harness.c (parse entry point) + build.sh (sanitizer build)
grammar/           JSON.g4 (ANTLR grammars-v4) + adaptation-notes.md
fuzz/
  runner.py        run one input, classify crash / reject / valid  (source of truth)
  baseline_strategy.py   Step 3: naive generator that proves the pipeline
  loop/agent.py    Step 4: agentic loop (seed -> run -> summarize -> refine -> stop)
  loop/llm.py      provider-agnostic chat client (DeepSeek default; MOCK offline)
  probes.py        single-feature probes for unconfounded acceptance attribution
  oracle.py        differential check vs strict json (finds json-parser leniency)
  triage/          Step 5: signature.py (dedup) + triage.py (minimize/verify/save)
runs/              per-iteration evidence from the committed live run (deepseek-chat)
crashes/           FINDINGS.md (results) + one folder per unique crash signature
eval/              coverage.py — MEASUREMENT-ONLY line coverage (random vs evolved)
  proxy_validation.py  post-hoc audit: does the blind proxy signal track coverage?
docs/              report.md (the two-page write-up)
DECISIONS.md       decision + critique log
```

## Build modes

`harness/build.sh` (via `./run.sh build`) produces three binaries:

- **`build/harness`** (`default`) — full ASan+UBSan; the faithful target. Any
  object-with-member trips the confirmed UB (finding #1), so it aborts on objects.
- **`build/harness_control`** (`control`) — injects a synthetic crash; the
  positive control. `run_live` refuses to log findings from it.
- **`build/harness_hunt`** (`hunt`) — full sanitizers **minus** the one
  `pointer-overflow` check that finding #1 trips, so the loop can parse objects
  and hunt for *deeper* bugs. Documented triage trade-off (see `DECISIONS.md`).

## Running the agentic loop (Step 4)

```bash
# For real, against DeepSeek (OpenAI-compatible; any provider works via env):
export DEEPSEEK_API_KEY=sk-...        # never committed (.gitignore covers .env)
.venv/bin/python fuzz/loop/agent.py --harness build/harness_hunt   # 5 iters

# Offline, no key, no spend — proves the full loop shape end-to-end:
LLM_MOCK=1 .venv/bin/python fuzz/loop/agent.py --max-examples 40

# Re-run a committed iteration's strategy offline (no API):
.venv/bin/python fuzz/loop/agent.py --replay runs/iter-5
```

Env knobs: `LLM_MODEL` (default `deepseek-chat`), `LLM_BASE_URL`, `LLM_PRICE_IN`/
`LLM_PRICE_OUT` (cost accounting in `runs/cost.md`). The loop **executes
model-generated strategy code**: `screen_code` is an AST allowlist (footgun-guard,
not a sandbox) — run it in a throwaway environment and review
`runs/iter-N/strategy.py`.

## Outcome contract (how crash vs. valid vs. rejection is decided)

The harness exits **0** = valid parse, **2** = well-formed rejection (NOT a bug),
**11** = SKIP (oversized — a *harness limitation*, kept in its own bucket so
"reject" always means the parser rejected malformed JSON). `fuzz/runner.py` treats
**anything else** — any other exit code, any terminating signal, or a timeout
> 5 s — as a **crash**. Timeouts count as crashes (a hang is a DoS bug, per the
assignment). Unlike a NUL-terminated API, json-parser's length-taking
`json_parse_ex` tests embedded NUL bytes faithfully (no NUL skip).

Every branch is exercised by a known input in `fuzz/test_pipeline.py`, which also
proves both sanitizers abort with symbolized traces and that the positive-control
build cannot be mistaken for the real target. `./run.sh test` runs four suites
(harness, proxy-signal, triage, loop) — 85 assertions.

## Status

- [x] Grammar + adaptation notes (Step 1)
- [x] Harness + sanitizer build, verified on samples (Step 2)
- [x] Baseline strategy + pipeline demonstration (Step 3)
- [x] Positive control proving the pipeline detects/captures crashes
- [x] Agentic loop, **run live** against DeepSeek (Step 4) — `runs/` is a real
      keyed run (`mode: deepseek-chat`, non-zero spend in `runs/cost.md`).
- [x] Crash triage: dedup + minimize + verify (Step 5) — exercised on the **real**
      finding #1, not only the control; auto-triages to `crashes/<id>/`.
- [x] Two-page report (Step 6) — `docs/report.md`; findings in `crashes/FINDINGS.md`.
- [x] **A confirmed finding** — UBSan UB on any object, minimized to `{""`.

## Known limitations

- **macOS/arm64: LeakSanitizer is unsupported** locally — but this gap is closed
  by the `Dockerfile`: on Linux, `eval/leakcheck.py` runs LSan over the evolved
  generator's inputs and found **no leaks** (300 inputs, hunt build).
- The committed loop run reflects `deepseek-chat`'s **high variance** at authoring
  Hypothesis strategies (most iterations rolled back model bugs); the loop's
  guards contain this, but a stronger model would use the budget better (report §2).
