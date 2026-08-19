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