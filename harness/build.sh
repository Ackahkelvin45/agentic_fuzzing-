#!/usr/bin/env bash
#
# build.sh — compile parson + harness with AddressSanitizer + UBSan.
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
# Modes (set MODE env var): default | roundtrip | control
#   default    parse -> free              (the faithful "parse entry point")
#   roundtrip  parse -> serialize -> free (adds serializer bug surface)
#   control    injects a synthetic crash  (positive control for the pipeline)
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
  -I "$ROOT/vendor/parson"
)

case "$MODE" in
  default)   EXTRA=(-DHARNESS_MODE='"default"');                     BIN="$OUT/harness" ;;
  roundtrip) EXTRA=(-DROUNDTRIP -DHARNESS_MODE='"roundtrip"');       BIN="$OUT/harness_roundtrip" ;;
  control)   EXTRA=(-DPOSITIVE_CONTROL -DHARNESS_MODE='"control"');  BIN="$OUT/harness_control" ;;
  *) echo "unknown MODE '$MODE' (use: default|roundtrip|control)" >&2; exit 1 ;;
esac

echo "[build.sh] MODE=$MODE  ->  $BIN"
# ${EXTRA[@]+...} guard: expands to nothing when EXTRA is empty, which is
# required under `set -u` on macOS's bash 3.2 (empty-array expansion errors).
"$CC" "${COMMON_FLAGS[@]}" ${EXTRA[@]+"${EXTRA[@]}"} \
  "$ROOT/vendor/parson/parson.c" \
  "$HERE/harness.c" \
  -o "$BIN"
echo "[build.sh] ok: $BIN"
