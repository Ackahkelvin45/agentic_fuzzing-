/*
 * harness.c — fuzzing driver for parson's JSON parse entry point.
 *
 * Exit-code contract (the whole interface the Python runner relies on):
 *
 *   exit 0   VALID   : input accepted as JSON     (json_parse_string != NULL)
 *   exit 2   REJECT  : well-formed rejection of malformed JSON (NOT a bug)
 *   exit 10  SKIP    : harness could not test this input faithfully because it
 *                      contains an embedded NUL (parson has no length-taking
 *                      parse API, so the input would be silently truncated).
 *                      This is a HARNESS LIMITATION, not a parser rejection, so
 *                      it gets its own code and its own bucket in the logs.
 *   exit 11  SKIP    : input exceeded MAX_INPUT_BYTES; skipped for the same
 *                      "not faithfully testable here" reason, logged distinctly.
 *   exit 3   ENVFAIL : allocation failure in the harness itself.
 *   crash            : sanitizer abort or fatal signal. The runner treats ANY
 *                      outcome not in {0,2,10,11,3} — plus timeouts — as a crash.
 *
 * Build modes (see build.sh) are compiled in and reportable at runtime via
 * `--mode`, so the runner can VERIFY (not assume) which binary it is talking to
 * and refuse to treat a positive-control crash as a real finding.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "parson.h"

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
    int saw_nul = 0;
    while ((ch = getchar()) != EOF) {
        if (ch == 0) saw_nul = 1;
        if (len + 1 >= cap) {
            if (cap >= MAX_INPUT_BYTES) { free(buf); return 11; } /* oversized -> SKIP */
            cap *= 2;
            char *nbuf = (char *)realloc(buf, cap);
            if (!nbuf) { free(buf); return 3; }
            buf = nbuf;
        }
        buf[len++] = (char)ch;
    }
    buf[len] = '\0';

    if (saw_nul) { free(buf); return 10; } /* embedded NUL -> SKIP (harness limit) */

#ifdef POSITIVE_CONTROL
    /* Synthetic bug, compiled in ONLY under -DPOSITIVE_CONTROL. The default
     * build physically cannot execute this. Triggered by a marker input so the
     * full pipeline (build -> run -> detect -> capture) can be proven to catch
     * a crash on this exact target binary. Not a parson bug. */
    if (strstr(buf, "__CRASH__") != NULL) {
        char *small = (char *)malloc(4);
        memset(small, 'A', 64); /* 64 bytes into a 4-byte buffer */
        volatile char sink = small[0];
        (void)sink;
        free(small);
    }
#endif

    JSON_Value *v = json_parse_string(buf);
    free(buf);

    if (v == NULL) {
        return 2; /* clean, well-formed rejection */
    }

#ifdef ROUNDTRIP
    char *out = json_serialize_to_string(v);
    if (out) json_free_serialized_string(out);
#endif

    json_value_free(v);
    return 0; /* accepted */
}
