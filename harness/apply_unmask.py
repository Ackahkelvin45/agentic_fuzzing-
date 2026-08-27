"""apply_unmask.py — produce a triage-only 'unmask' copy of json.c.

Finding #1 aborts on EVERY object under the full sanitizer build, masking the
rest of the object path. The `hunt` build unmasks by disabling the whole
`pointer-overflow` check globally — but json_parse_ex is MONOLITHIC (the entire
parser is one function), so no function-scoped suppression is narrower than that,
leaving a real blind spot: any *other* pointer-overflow in the parser is hidden too.

This does the truly surgical thing instead. Finding #1 is the "use the pointer
field as a byte counter" idiom: `chars[0] += n` where `chars` aliases a still-NULL
pointer field, i.e. `NULL + n` pointer arithmetic (UBSan pointer-overflow). We
rewrite ONLY those two sites to accumulate the SAME byte count as INTEGER
arithmetic on the same pointer-sized storage (`size_t` alias). The stored bytes
are bit-identical, so behaviour is unchanged — but the arithmetic is now
well-defined, so the object path runs clean under the FULL sanitizer set with
`pointer-overflow` still LIVE everywhere. Any crash the unmask build then surfaces
is a REAL, previously-masked json-parser bug, not a patch artifact.

The pinned `vendor/json-parser/json.c` is NEVER modified; this writes a separate
`build/_patched/json.c`. See crashes/FINDINGS.md.
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "vendor" / "json-parser" / "json.c"
OUT = ROOT / "build" / "_patched"

# Each (old, new): old MUST occur exactly once (asserted), so a source change that
# moved these sites fails loudly instead of silently patching the wrong thing.
REPLACEMENTS = [
    (
        "json_char **chars = (json_char **) &top->u.object.values;",
        "/* unmask (triage): see harness/apply_unmask.py — integer accumulate */",
    ),
    (
        "chars[0] += string_length + 1;",
        "*((size_t *) &top->u.object.values) += string_length + 1;",
    ),
    (
        "(*(json_char **) &top->_reserved.object_mem) += string_length + 1;",
        "*((size_t *) &top->_reserved.object_mem) += string_length + 1;",
    ),
]


def main() -> int:
    src = SRC.read_text()
    for old, new in REPLACEMENTS:
        n = src.count(old)
        assert n == 1, f"expected exactly ONE occurrence of {old!r}, found {n}"
        src = src.replace(old, new)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "json.c").write_text(src)
    (OUT / "json.h").write_text((SRC.parent / "json.h").read_text())
    print(f"[unmask] wrote {OUT / 'json.c'} (2 UB sites -> integer arithmetic)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
