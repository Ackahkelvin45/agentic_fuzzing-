# Vendored dependency: parson

- **Upstream:** https://github.com/kgabis/parson
- **Pinned commit:** `ba29f4eda9ea7703a9f6a9cf2b0532a2605723c3`
- **Release:** 1.5.3 (latest release at time of retrieval)
- **Retrieved:** 2026-08-14
- **License:** MIT (see `LICENSE`, copied verbatim from upstream; the MIT header
  is also retained at the top of `parson.c` / `parson.h`).

## Why vendored (copied) instead of a git submodule

Copying the two source files plus the license gives a fully **offline,
reproducible** build: a grader clones the repo and builds with no network
access and no `git submodule update --recursive` step. parson is a single
translation unit (`parson.c` + `parson.h`), so there is nothing to gain from a
submodule at this scale.

## Which version to target — open question

The assignment states each candidate is assigned a **specific pinned commit**.
That commit has **not yet been provided** by Prof. D'Amorim. Until it is:

- **Exploratory target (reported honestly):** `1.5.3 @ ba29f4e`, the latest
  release. This is the most *hardened* commit in the tree — it sits after a
  chain of memory-safety fixes (#133 integer overflow, #157 null-byte memleak,
  #204 arithmetic overflow). A "0 crashes" result here is a legitimate finding,
  not a failure, **provided** the pipeline is independently proven to detect
  crashes (see the positive control in `harness/`).

- **Action item:** email Prof. D'Amorim to confirm the assigned commit, then
  re-pin here. All findings are reported against the exact SHA above regardless.

## Note on parson's historical bugs

The known pre-1.5.3 overflow fixes (#133, #204) are both in the **serialization**
path, not the parse path, and require multi-gigabyte inputs to trigger — so a
parse-only harness cannot rediscover them from small inputs. This is *why* the
positive control is a synthetic injected bug rather than an old parson commit
(see `harness/harness.c`, `POSITIVE_CONTROL`).
