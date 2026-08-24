"""oracle.py — differential check against a reference JSON parser.

Kept wired into the loop (NOT a one-time baseline validation). The interesting
quadrant is **json-parser ACCEPTS but the oracle REJECTS**: that is exactly where
json-parser's leniency lives, and it is how the known deltas (duplicate keys
accepted; a single trailing comma accepted; non-finite numbers accepted; lone
`-` -> a number) surface. Every such divergence is a candidate
"accepted-format deviation" for the grammar-adaptation deliverable.

We use Python's stdlib `json` as the RFC 8259 reference oracle. It is strict
(no trailing garbage, requires a digit in numbers), so it is a good foil for
json-parser's leniency. This is a *correctness* differential, orthogonal to the
crash/reject/valid safety classification in runner.py.

    Outcome pairs (jsonparser_outcome, oracle_accepts):
      (VALID , False)  -> DIVERGENCE: json-parser lenient  <-- watch this
      (REJECT, True )  -> DIVERGENCE: json-parser stricter (rare; also worth a look)
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
    kind: str          # "jsonparser-lenient" | "jsonparser-stricter"
    input_repr: str


def classify_divergence(jsonparser_valid: bool, data: bytes) -> Divergence | None:
    """Return a Divergence when json-parser and the oracle disagree, else None.

    Only call this for inputs json-parser classified as VALID or REJECT (never for
    SKIP/CRASH — those are safety outcomes, not correctness comparisons).
    """
    oracle = oracle_accepts(data)
    if jsonparser_valid and not oracle:
        return Divergence("jsonparser-lenient", repr(data[:80]))
    if (not jsonparser_valid) and oracle:
        return Divergence("jsonparser-stricter", repr(data[:80]))
    return None
