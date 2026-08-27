# iter-1 provenance — which strategy actually ran

Iteration 1's first model output (`strategy.py`, `response.md`) used a
hallucinated Hypothesis API — `st.booleans(probability=...)`, which does not
exist — so it **raised on every draw** and did not run. The acceptance gate
caught this and issued one in-iteration repair re-prompt; the model's repaired
output is `strategy_fix.py` (`response_fix.md`), and **that is the strategy this
iteration actually ran** (recorded acceptance ≈ 0.85 in `stats.json`). The
refinement chain is coherent: iteration 2's prompt (`runs/iter-2/prompt.md`)
embeds the *repaired* iter-1 code, not the broken original.

Two artifacts are kept deliberately:

- `strategy.py` — the original, broken model output (evidence of the failure
  mode the gate is designed to catch; see the report's appendix on LLM-as-author
  failure modes).
- `strategy_fix.py` — the repaired, effective strategy that produced this
  iteration's results.

`agent.py --replay runs/iter-1` detects that `strategy.py` does not run and
falls back to `strategy_fix.py` automatically. For runs produced after this
note, the writer keeps `strategy.py` canonical (= the code that ran) and
preserves any pre-repair output as `strategy_orig.py`, so no fallback is needed.
