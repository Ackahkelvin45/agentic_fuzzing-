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
    "valid": 403,
    "reject": 7
  },
  "acceptance_rate": 0.983,
  "max_nesting_depth": 4,
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
  "divergences": 7,
  "divergence_examples": [
    "b'[[true, false, -18598, \"\"], {\"g\\\\u0000\\\\u00d1\": [], \"\\\\u009d\\\\u0013\": [-9.4391823951'",
    "b'[[], [null], [{}], {\"J\\\\ud809\\\\udfb2\": null, \"6\": \"\\\\u008f\\\\ud9d0\\\\udfb5\\\\ud8f7\\\\uddc0T'",
    "b'[[], [null], [{}], {\"\\\\\"~\\\\ud96e\\\\udf52\\\\udaab\\\\udcb8\": null, \"6\": \"\\\\u008f\\\\ud9d0\\\\udfb'"
  ],
  "unique_crash_signatures": []
}

Make EXACTLY ONE change to the strategy: **generate_nesting_toward_cap** — target depth 1500-2048.
Change nothing else. Keep it a valid Hypothesis strategy producing `str`. Return the full revised module as one ```python code block.