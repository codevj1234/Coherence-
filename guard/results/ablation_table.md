| model | condition | half-life (steps) | runs with regression | regressing steps | broken invariants at step 100 | intent completed | rejected | retries/step | tokens in/step (est.) |
|---|---|---|---|---|---|---|---|---|---|
| sim-small | C0 open loop | 8 (95% CI 6-14) | 20/20 | 4.8% | 5.70 | 88.4% | 0.0% | 0.00 | 371 |
| sim-small | C0 open loop + oracle reset | 8 (95% CI 6-14) | 20/20 | 11.0% | 0.00 | 79.3% | 0.0% | 0.00 | 392 |
| sim-small | C1 reactive | >100 | 1/20 | 0.1% | 0.05 | 99.7% | 0.0% | 0.26 | 485 |
| sim-small | C3 full guard | >100 | 0/20 | 0.0% | 0.00 | 99.7% | 0.4% | 0.26 | 1853 |
| sim-large | C0 open loop | 10 (95% CI 6-30) | 20/20 | 3.1% | 3.80 | 94.4% | 0.0% | 0.00 | 386 |
| sim-large | C0 open loop + oracle reset | 10 (95% CI 6-30) | 20/20 | 4.1% | 0.00 | 92.7% | 0.0% | 0.00 | 392 |
| sim-large | C1 reactive | >100 | 0/20 | 0.0% | 0.00 | 100.0% | 0.0% | 0.08 | 419 |
| sim-large | C3 full guard | >100 | 0/20 | 0.0% | 0.00 | 100.0% | 0.0% | 0.08 | 1590 |
