Here is the current Hypothesis strategy:

```python
from hypothesis import strategies as st
import json

def _leaf():
    return st.one_of(
        st.integers(), st.booleans(), st.none(),
        st.text(max_size=6),
        st.floats(allow_nan=False, allow_infinity=False),
    )

_json = st.recursive(
    _leaf(),
    lambda kids: st.one_of(
        st.lists(kids, max_size=4),
        st.dictionaries(st.text(min_size=1, max_size=4), kids, max_size=4),
    ),
    max_leaves=12,
)

strategy = _json.map(lambda o: json.dumps(o))
```

Here is the summary of its last run against the parser (no coverage data is available):
{
  "outcomes": {
    "valid": 394,
    "reject": 16
  },
  "acceptance_rate": 0.961,
  "max_nesting_depth": 8,
  "productions_accepted": [
    "array",
    "false",
    "null",
    "number",
    "object",
    "string",
    "true"
  ],
  "productions_gap": [],
  "cap_distance_mass": 0.0,
  "novelty": 72,
  "divergences": 16,
  "divergence_examples": [
    "b'{\"\\\\u00b2>R\\\\u00c8\": [], \"\\\\u00bb\": {\"\\\\udb77\\\\udc49\": {\"\\\\u00e9\\\\u0089\": {\"\\\\u00ed[%\\\\u0'",
    "b'{\"\\\\u00b2>R\\\\u00c8\": [], \"\\\\u00bb\": {\"\\\\udb77\\\\udc49\": {\"\\\\u00e9\\\\u0089\": {\"\\\\u00ed[%\\\\u0'",
    "b'{\"\\\\u00b2>R\\\\u00c8\": [], \"\\\\u00bb\": {\"\\\\udb77\\\\udc49\": {\"\\\\u00e9\\\\u0089\": {\"\\\\u00ed[%\\\\u0'"
  ],
  "unique_crash_signatures": []
}

Make EXACTLY ONE change to the strategy: **generate_nesting_toward_cap** — target depth 1500-2048.
Change nothing else. Keep it a valid Hypothesis strategy producing `str`. Return the full revised module as one ```python code block.