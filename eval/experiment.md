# Controlled experiment: does grammar-seeding, then refinement, help?

**Design.** Three fixed generators, each measured over **K=12 independent
PRNG seeds** at **N=150 examples/seed**. Per (seed, condition) one pass over
the coverage build yields both `json.c` coverage AND the acceptance / novelty /
cap-mass proxy metrics on the *same* inputs. Values are **mean ± 95% CI**
(t-interval) over the K seeds; comparisons use a two-sided **permutation test**
(20000 relabelings, no scipy). This replaces the single committed run's
non-reproducible 51→72 trajectory (max-of-noise; DECISIONS.md D9) with a claim
that rests on a distribution.

## Results (mean ± 95% CI over seeds)

| Condition | region% | line% | branch% | acceptance | novelty | cap_mass |
|---|---|---|---|---|---|---|
| A. baseline (random) | 29.7±1.8 | 33.8±2.5 | 23.4±1.5 | 0.01±0.00 | 1.0±0.5 | 0.00±0.00 |
| B. seed (iter-1, no refine) | 64.2±0.9 | 74.8±1.0 | 61.2±0.9 | 0.75±0.02 | 15.0±1.4 | 0.00±0.00 |
| C. evolved (iter-5) | 66.9±1.6 | 77.3±1.9 | 63.6±1.6 | 0.61±0.03 | 18.4±1.4 | 0.19±0.06 |

## Significance (paired sign-flip permutation test, exact)
### Grammar-seed (B) vs random baseline (A) — does seeding help?
- region: Δ=+34.5 (95% CI [+32.3, +36.7])  paired p=0.0005, **significant**
- branch: Δ=+37.8 (95% CI [+36.1, +39.5])  paired p=0.0005, **significant**
- novelty: Δ=+14.0 (95% CI [+12.6, +15.4])  paired p=0.0005, **significant**
### Evolved (C) vs grammar-seed (B) — does REFINEMENT help beyond seeding?
- region: Δ=+2.6 (95% CI [+0.8, +4.4])  paired p=0.0127, **significant**
- branch: Δ=+2.5 (95% CI [+0.7, +4.2])  paired p=0.0122, **significant**
- novelty: Δ=+3.4 (95% CI [+1.9, +4.9])  paired p=0.0020, **significant**

## Reading it

A comparison is meaningful only if its CIs separate AND the permutation p is
small. **B vs A** tests the project's core premise (grammar-seeding reaches the
value-construction code random bytes miss). **C vs B** is the honest, harder
test the single run could not make: does the feedback loop's refinement add
anything *beyond* the initial grammar-seed, once seed-noise is averaged out? The
numbers above answer both from a distribution rather than one lucky draw —
including the case where refinement's effect is within noise, which is stated
plainly rather than hidden.
