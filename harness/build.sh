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

COMMON_FLAGS=(
  -fsanitize=address,undefined
  -fno-sanitize-recover=all
  -fno-omit-frame-pointer
  -g -O0
  -I "$ROOT/vendor/json-parser"
)

case "$MODE" in
  default)   EXTRA=(-DHARNESS_MODE='"default"');                     BIN="$OUT/harness" ;;
  control)   EXTRA=(-DPOSITIVE_CONTROL -DHARNESS_MODE='"control"');  BIN="$OUT/harness_control" ;;
  hunt)      EXTRA=(-fno-sanitize=pointer-overflow -DHARNESS_MODE='"hunt"'); BIN="$OUT/harness_hunt" ;;
  *) echo "unknown MODE '$MODE' (use: default|control|hunt)" >&2; exit 1 ;;
esac

echo "[build.sh] MODE=$MODE  ->  $BIN"
# ${EXTRA[@]+...} guard: expands to nothing when EXTRA is empty, which is
# required under `set -u` on macOS's bash 3.2 (empty-array expansion errors).
"$CC" "${COMMON_FLAGS[@]}" ${EXTRA[@]+"${EXTRA[@]}"} \
  "$ROOT/vendor/json-parser/json.c" \
  "$HERE/harness.c" \
  -o "$BIN"
echo "[build.sh] ok: $BIN"
