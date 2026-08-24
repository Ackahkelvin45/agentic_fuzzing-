/*
 * harness.c — fuzzing driver for json-parser's parse entry point.
 *
 * Exit-code contract (the whole interface the Python runner relies on):
 *
 *   exit 0   VALID   : input accepted as JSON        (json_parse_ex != NULL)
 *   exit 2   REJECT  : well-formed rejection of malformed JSON (NOT a bug);
 *                      the parser's own error string is written to stderr so
 *                      the loop finally has a "why was this rejected" signal.
 *   exit 11  SKIP    : input exceeded MAX_INPUT_BYTES; skipped as "not
 *                      faithfully testable here", logged distinctly.
 *   exit 3   ENVFAIL : allocation failure in the harness itself.
 *   crash            : sanitizer abort or fatal signal. The runner treats ANY
 *                      outcome not in {0,2,11,3} — plus timeouts — as a crash.
 *
 * Why no embedded-NUL SKIP (unlike the parson harness): json-parser's
 * `json_parse_ex(settings, json, LENGTH, error)` takes an explicit length, so a
 * buffer containing NUL bytes is parsed faithfully rather than truncated at the
 * first NUL. NUL handling is a classic crash source, so we now TEST it instead
 * of skipping it.
 *
 * json-parser is parse-only (no serializer), so there is no roundtrip mode.
 *
 * Build modes (see build.sh) are compiled in and reportable at runtime via
 * `--mode`, so the runner can VERIFY (not assume) which binary it is talking to
 * and refuse to treat a positive-control crash as a real finding.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "json.h"

#ifndef HARNESS_MODE
#define HARNESS_MODE "default"
#endif

/* Reject anything larger than this as SKIP (exit 11). */
#define MAX_INPUT_BYTES (64u * 1024u * 1024u)

int main(int argc, char **argv) {
    /* Machine-checkable build identity: `harness --mode` prints its mode and
     * exits 0. Lets the runner enforce that real findings only come from the
     * default build, never the positive-control build. */
    if (argc > 1 && strcmp(argv[1], "--mode") == 0) {
        puts(HARNESS_MODE);
        return 0;
    }

    size_t cap = 1u << 16;
    size_t len = 0;
    char *buf = (char *)malloc(cap);
    if (!buf) return 3;

    int ch;
    while ((ch = getchar()) != EOF) {
        if (len + 1 >= cap) {
            if (cap >= MAX_INPUT_BYTES) { free(buf); return 11; } /* oversized -> SKIP */
            cap *= 2;
            char *nbuf = (char *)realloc(buf, cap);
            if (!nbuf) { free(buf); return 3; }
            buf = nbuf;
        }
        buf[len++] = (char)ch;
    }
    buf[len] = '\0';  /* harmless; json_parse_ex uses `len`, not NUL-termination */

#ifdef POSITIVE_CONTROL
    /* Synthetic bug, compiled in ONLY under -DPOSITIVE_CONTROL. The default
     * build physically cannot execute this. Triggered by a marker input so the
     * full pipeline (build -> run -> detect -> capture) can be proven to catch
     * a crash on this exact target binary. Not a json-parser bug. */
    if (strstr(buf, "__CRASH__") != NULL) {
        char *small = (char *)malloc(4);
        memset(small, 'A', 64); /* 64 bytes into a 4-byte buffer */
        volatile char sink = small[0];
        (void)sink;
        free(small);
    }
#endif

    json_settings settings;
    memset(&settings, 0, sizeof settings);  /* defaults: malloc/free, no comments */
    char error[json_error_max];
    error[0] = '\0';

    json_value *v = json_parse_ex(&settings, buf, len, error);
    free(buf);

    if (v == NULL) {
        /* well-formed rejection: surface the parser's reason (parson gave none) */
        fprintf(stderr, "reject: %s\n", error);
        return 2;
    }

    json_value_free(v);
    return 0; /* accepted */
}
