# MOCK run — not a real LLM run

These artifacts were produced with `LLM_MOCK=1` (no API key, no
spend). The strategy is a fixed canned generator, so it is
IDENTICAL across iterations and shows the loop's *shape*, not
real strategy evolution. Real runs record the model name in
`stats.json:mode` and a non-zero token count in `runs/cost.md`.
