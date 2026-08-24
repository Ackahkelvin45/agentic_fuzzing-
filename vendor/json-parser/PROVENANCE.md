# Vendored target: json-parser

- **Upstream:** <https://github.com/udp/json-parser>
- **Pinned commit:** `8ac4477ad3e63dc107e17ad49484edaa17d18d35` (2025-05-09)
- **Files vendored:** `json.c`, `json.h`, `LICENSE` (the whole library — it is a
  single-file C parser, so nothing else is needed to build).
- **License:** BSD-2-Clause (see `LICENSE`).

## Why this commit

The assignment assigns each candidate a library pinned to a specific commit.
For this take-home the instructor confirmed by email that **using a recent
commit is fine**, and that the target library was not fixed to parson. We
therefore target json-parser (one of the listed libraries, format: JSON) at its
latest commit at retrieval time (`8ac4477`).

The switch from the initial exploratory target (parson 1.5.3) is recorded in
`DECISIONS.md`; in short, parson's latest release is heavily hardened and
produced no findings, whereas json-parser exposes a real UBSan-detectable bug
from almost any object input (see `crashes/FINDINGS.md`).

## Entry point exercised

`json_parse_ex(&settings, buf, len, error)` with zero-initialized settings
(comments disabled). The length-taking API means embedded NUL bytes are tested
faithfully. json-parser is parse-only (no serializer), so there is no
round-trip harness mode.
