# Coverage evaluation (measurement only — not used to steer the loop)

Line coverage of `vendor/json-parser/json.c` over 400 seeded examples each, on a separate non-sanitizer `-fcoverage-mapping` build:

| Generator | json.c line coverage |
|---|---|
| naive baseline (`st.text()`, Step 3) | 42.2% |
| LLM grammar-seeded (evolved, iter-5) | 83.1% |

The grammar-seeded generator reaches **2.0x** the line coverage of random text. Random text still exercises the lexer and
rejection/error paths (hence the non-trivial baseline), but it rarely forms a
parseable value, so it misses the value/number/string/object *construction*
paths that only (near-)valid JSON reaches — exactly what the grammar-seeded
generator adds. This *measures* an assertion the design rests on; it is not a
steering signal.
