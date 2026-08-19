/*
 * triage_test.c — a synthetic multi-bug harness used ONLY to test the triage
 * pipeline (signature dedup / minimize / verify) on REAL sanitizer reports.
 * Different marker substrings crash in different named functions, producing
 * distinct crash signatures. Not parson; never a real finding.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int overflow_bug(void) {
    char *b = (char *)malloc(4);
    memset(b, 'A', 64);          /* heap-buffer-overflow */
    int r = b[0];
    free(b);
    return r;
}

static int uaf_bug(void) {
    char *b = (char *)malloc(16);
    free(b);
    return b[0];                 /* heap-use-after-free */
}

static int null_bug(void) {
    volatile int *p = 0;
    return *p;                   /* SEGV */
}

int main(void) {
    size_t cap = 1u << 16, len = 0;
    char *buf = (char *)malloc(cap);
    int c;
    while ((c = getchar()) != EOF) {
        if (len + 1 >= cap) { cap *= 2; buf = (char *)realloc(buf, cap); }
        buf[len++] = (char)c;
    }
    buf[len] = '\0';
    if (strstr(buf, "OVERFLOW"))  return overflow_bug();
    if (strstr(buf, "USEAFTER"))  return uaf_bug();
    if (strstr(buf, "NULLDEREF")) return null_bug();
    return 2;                    /* no marker -> a clean 'reject' */
}
