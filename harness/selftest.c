/*
 * selftest.c — deliberately trigger ONE sanitizer, to prove each is active
 * under the project's real build flags and that reports carry a stack trace.
 *
 *   selftest asan   -> heap-buffer-overflow  (AddressSanitizer must abort)
 *   selftest ubsan  -> signed int overflow    (UBSan must abort, not just warn)
 *
 * Built by fuzz/test_pipeline.py with the SAME flags as the harness.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>

static int asan_trigger(void) {
    char *p = (char *)malloc(4);
    memset(p, 'A', 64);          /* write past a 4-byte heap buffer */
    int r = p[0];
    free(p);
    return r;
}

static int ubsan_trigger(void) {
    volatile int x = INT_MAX;
    return (int)(x + 1);         /* signed overflow: undefined behavior */
}

int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "asan") == 0) return asan_trigger();
    if (argc > 1 && strcmp(argv[1], "ubsan") == 0) return ubsan_trigger();
    fprintf(stderr, "usage: selftest asan|ubsan\n");
    return 99;
}
