# Agentic Fuzzing — parson (JSON)

Blackbox, grammar-driven fuzzing of the [parson](https://github.com/kgabis/parson)
C JSON library. An LLM turns a formal JSON grammar into a
[Hypothesis](https://hypothesis.readthedocs.io/) strategy, generated inputs are
run against parson built with AddressSanitizer + UndefinedBehaviorSanitizer, and
the strategy is refined across a small budget of iterations using a proxy signal
(there is no coverage instrumentation).

**Start with [`docs/report.md`](docs/report.md)** — the two-page write-up
(design, findings, challenges). [`DECISIONS.md`](DECISIONS.md) records every
design decision, the independent critique it received, and how it was resolved;
`docs/agentic-fuzzing-brief.html` is a plain-language overview of the task.

## Quick start (reproduce from a clean checkout)

```bash
./run.sh all      # setup venv + build harness + classification check + baseline
```

Individual steps: `./run.sh setup | build | check | baseline`.
Requires a C compiler with ASan/UBSan (Apple clang or clang/gcc on Linux),
Python 3.11+, and network access only for the one-time `pip install`.

## Layout

```
vendor/parson/     pinned parson source (1.5.3 @ ba29f4e) + LICENSE + PROVENANCE
harness/           harness.c (parse entry point) + build.sh (sanitizer build)
grammar/           JSON.g4 (ANTLR grammars-v4) + adaptation-notes.md
fuzz/
  runner.py        run one input, classify crash / reject / valid  (source of truth)
  baseline_strategy.py   Step 3: naive generator that proves the pipeline
  loop/agent.py    Step 4: agentic loop (seed -> run -> summarize -> refine -> stop)
  loop/llm.py      provider-agnostic chat client (DeepSeek default; MOCK offline)
  probes.py        single-feature probes for unconfounded acceptance attribution
  oracle.py        differential check vs strict json (finds parson leniency)
  triage/          Step 5: signature.py (dedup) + triage.py (minimize/verify/save)
runs/              per-iteration evidence (currently a MOCK run — see its banners)
crashes/           FINDINGS.md (results) + one folder per unique crash signature
docs/              report.md (the two-page write-up) + plain-language brief
DECISIONS.md       decision + critique log
```

## Running the agentic loop (Step 4)

```bash
# Offline, no key, no spend — proves the full loop shape end-to-end:
LLM_MOCK=1 .venv/bin/python fuzz/loop/agent.py --max-examples 40

# For real, against DeepSeek (OpenAI-compatible; any provider works via env):
export DEEPSEEK_API_KEY=sk-...        # never committed (.gitignore covers .env)
.venv/bin/python fuzz/loop/agent.py   # 5 iterations, 500 examples each

# Re-run a committed iteration's strategy offline (no API):
.venv/bin/python fuzz/loop/agent.py --replay runs/iter-3
```

Env knobs: `LLM_MODEL` (default `deepseek-chat`), `LLM_BASE_URL`
(`https://api.deepseek.com`), `LLM_PRICE_IN`/`LLM_PRICE_OUT` (for cost
accounting in `runs/cost.md`). The loop **executes model-generated strategy
code**: `screen_code` is an AST allowlist (footgun-guard, not a sandbox) — run
it in a throwaway environment and review `runs/iter-N/strategy.py`.

## Outcome contract (how crash vs. valid vs. rejection is decided)

The harness exits **0** = valid parse, **2** = well-formed rejection (NOT a bug),
**10/11** = SKIP (embedded NUL / oversized — a *harness limitation*, kept in its
own bucket so "reject" always means the parser rejected malformed JSON).
`fuzz/runner.py` treats **anything else** — any other exit code, any terminating
signal, or a timeout > 5 s — as a **crash**. Timeouts count as crashes (a hang is
a DoS bug, per the assignment).

Every branch of this contract is exercised by a known input in
`fuzz/test_pipeline.py`, which also proves both sanitizers abort with symbolized
traces and that the positive-control build cannot be mistaken for the real
target. `./run.sh test` runs four suites (harness, proxy-signal, triage, loop).

## Status

- [x] Grammar + adaptation notes (Step 1)
- [x] Harness + sanitizer build, verified on samples (Step 2)
- [x] Baseline strategy + pipeline demonstration (Step 3)
- [x] Positive control proving the pipeline detects/captures crashes
- [x] Agentic loop wired to the LLM (Step 4) — DeepSeek/OpenAI-compatible + MOCK;
      proxy signal proven actionable (`fuzz/test_signal.py`). Remaining: a real
      keyed 5-iteration run (needs an API key + spend).
- [x] Crash triage: dedup + minimize + verify (Step 5) — `fuzz/triage/`, proven
      on real sanitizer crashes, wired into the loop (auto-triages to `crashes/<id>/`).
- [x] Two-page report (Step 6) — `docs/report.md`; findings/none-found in
      `crashes/FINDINGS.md`.
- [ ] **A keyed live run** — the loop has only been exercised in `LLM_MOCK=1`
      mode (see the MOCK banners in `runs/`). Running it against a real model
      needs an API key and a few cents of spend.

## Known limitations

- **macOS/arm64: LeakSanitizer is unsupported**, so memory *leaks* are not
  detected on this platform. The target is crashes/UB, not leaks; a Linux repro
  path is a planned grader-parity follow-up.
- The **assigned pinned commit is unconfirmed** — 1.5.3 is a documented
  placeholder (see `vendor/parson/PROVENANCE.md`).
