#!/usr/bin/env bash
# reproduce this crash against the pinned build
export ASAN_OPTIONS="abort_on_error=1:detect_stack_use_after_return=1:allocator_may_return_null=0:detect_leaks=0"
export UBSAN_OPTIONS="print_stacktrace=1:halt_on_error=1:abort_on_error=1"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"build/harness" < "$HERE/minimized_input"
