#!/usr/bin/env bash
#
# build.sh — compile json-parser + harness with AddressSanitizer + UBSan.
#
# Design notes (why these exact flags):
#   -fsanitize=address,undefined  ASan catches memory-safety bugs (overflow,
#                                 use-after-free); UBSan catches undefined
#                                 behavior (signed overflow, bad shifts, ...).
#   -fno-sanitize-recover=all     REQUIRED. Without it UBSan only prints
#                                 "runtime error:" and CONTINUES, so a real UB
#                                 input would exit 0 and be misread as a valid
#                                 parse. This makes the first error abort.
#   -fno-omit-frame-pointer -g    readable stack traces for crash dedup.
#   -O0                           no optimization, so the compiler can't fold
#                                 away the very UB we want to observe, and
#                                 stack frames aren't inlined together.
#
# Modes (set MODE env var): default | control | hunt
#   default    parse -> free              (the faithful "parse entry point")
#   control    injects a synthetic crash  (positive control for the pipeline)
#   hunt       like default but with -fno-sanitize=pointer-overflow, which
#              suppresses ONLY json-parser's known first-pass NULL-offset idiom
#              (json.c:437/447, reported separately as finding #1). Every other
#              ASan/UBSan check stays live, so the loop can parse objects and
#              reach DEEPER bugs instead of aborting on the shallow known one.
#              A documented triage trade-off: it also hides any *other*
#              pointer-overflow, so real findings are re-confirmed on `default`.
#   unmask     STRICTLY BETTER hunt: a line-precise, value-preserving patch
#              (harness/apply_unmask.py) rewrites ONLY finding #1's two NULL+n
#              sites to integer arithmetic on the same storage, then compiles with
#              the FULL sanitizer set — so pointer-overflow stays LIVE everywhere
#              else, unlike `hunt`. Used to hunt a SECOND object-path signature
#              without the hunt build's blind spot. Pinned vendor/ source is never
#              touched (patched copy lives in build/_patched/).
# (json-parser is parse-only, so there is no serializer/roundtrip mode.)
#
# macOS note: LeakSanitizer is NOT supported on macOS/arm64, so memory *leaks*
# are not detected on this platform. The assignment targets crashes/UB, not
# leaks, so this is documented rather than worked around. See README.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
OUT="$ROOT/build"
mkdir -p "$OUT"

MODE="${MODE:-default}"
CC="${CC:-clang}"

# Source + include default to the pinned vendor tree; `unmask` overrides them with
# the value-preserving patched copy (pinned source is never modified).
JSON_SRC="$ROOT/vendor/json-parser/json.c"
JSON_INC="$ROOT/vendor/json-parser"

case "$MODE" in
  default)   EXTRA=(-DHARNESS_MODE='"default"');                     BIN="$OUT/harness" ;;
  control)   EXTRA=(-DPOSITIVE_CONTROL -DHARNESS_MODE='"control"');  BIN="$OUT/harness_control" ;;
  hunt)      EXTRA=(-fno-sanitize=pointer-overflow -DHARNESS_MODE='"hunt"'); BIN="$OUT/harness_hunt" ;;
  unmask)    "${PYTHON:-python3}" "$HERE/apply_unmask.py"
             EXTRA=(-DHARNESS_MODE='"unmask"')
             JSON_SRC="$OUT/_patched/json.c"; JSON_INC="$OUT/_patched"
             BIN="$OUT/harness_unmask" ;;
  *) echo "unknown MODE '$MODE' (use: default|control|hunt|unmask)" >&2; exit 1 ;;
esac

COMMON_FLAGS=(
  -fsanitize=address,undefined
  -fno-sanitize-recover=all
  -fno-omit-frame-pointer
  -g -O0
  -I "$JSON_INC"
)

echo "[build.sh] MODE=$MODE  ->  $BIN"
# ${EXTRA[@]+...} guard: expands to nothing when EXTRA is empty, which is
# required under `set -u` on macOS's bash 3.2 (empty-array expansion errors).
"$CC" "${COMMON_FLAGS[@]}" ${EXTRA[@]+"${EXTRA[@]}"} \
  "$JSON_SRC" \
  "$HERE/harness.c" \
  -o "$BIN"
echo "[build.sh] ok: $BIN"
