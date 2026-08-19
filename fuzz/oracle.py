"""oracle.py — differential check against a reference JSON parser.

Kept wired into the loop (NOT a one-time baseline validation). The interesting
quadrant is **parson ACCEPTS but the oracle REJECTS**: that is exactly where
parson's leniency lives, and it is how the two known deltas (trailing garbage
ignored; lone `-` -> 0) were found. Every such divergence is a candidate
"accepted-format deviation" for the grammar-adaptation deliverable.

We use Python's stdlib `json` as the RFC 8259 reference oracle. It is strict
(no trailing garbage, requires a digit in numbers), so it is a good foil for
parson's leniency. This is a *correctness* differential, orthogonal to the
crash/reject/valid safety classification in runner.py.

    Outcome pairs (parson_outcome, oracle_accepts):
      (VALID , False)  -> DIVERGENCE: parson lenient  <-- watch this
      (REJECT, True )  -> DIVERGENCE: parson stricter (rare; also worth a look)
      (VALID , True ), (REJECT, False) -> agree
"""
from __future__ import annotations

import json
from dataclasses import dataclass


def oracle_accepts(data: bytes) -> bool:
    """True iff the strict reference parser accepts `data` as JSON.

    RecursionError is caught deliberately: CPython's json decoder recurses, so a
    deeply-nested input (exactly what the cap-distance signal steers toward)
    raises it. Letting it escape made the loop misattribute an oracle limitation
    to the model's generator and roll back a perfectly good strategy.
    """
    try:
        json.loads(data.decode("utf-8"))
        return True
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError):
        return False


@dataclass
class Divergence:
    kind: str          # "parson-lenient" | "parson-stricter"
    input_repr: str


def classify_divergence(parson_valid: bool, data: bytes) -> Divergence | None:
    """Return a Divergence when parson and the oracle disagree, else None.

    Only call this for inputs parson classified as VALID or REJECT (never for
    SKIP/CRASH — those are safety outcomes, not correctness comparisons).
    """
    oracle = oracle_accepts(data)
    if parson_valid and not oracle:
        return Divergence("parson-lenient", repr(data[:80]))
    if (not parson_valid) and oracle:
        return Divergence("parson-stricter", repr(data[:80]))
    return None
