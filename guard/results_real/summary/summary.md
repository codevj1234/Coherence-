| model | bed | cond | seqs | half-life (steps) | runs w/ regression | broken at end | intent done | rejected | parse fail | retries/step | tokens in/step |
|---|---|---|---|---|---|---|---|---|---|---|---|
| qwen2.5-coder-7b | abilene | C0 | 3 | 5 | 3/3 | 2.67 | 55.6% | 0.0% | 20.0% | 0.00 | 920 |
| qwen2.5-coder-7b | abilene | C1 | 3 | 10 | 3/3 | 2.67 | 68.9% | 0.0% | 15.6% | 0.98 | 2176 |
| qwen2.5-coder-7b | abilene | C2 | 3 | 10 | 2/3 | 1.67 | 64.4% | 0.0% | 20.0% | 1.22 | 2959 |
| qwen2.5-coder-7b | abilene | C3 | 3 | over 15 | 0/3 | 0.00 | 64.4% | 35.6% | 31.1% | 1.13 | 4435 |

Paired bootstrap over sequences (difference = second condition minus first; 5000 resamples):

| model | bed | comparison | metric | mean diff | 95% CI | p |
|---|---|---|---|---|---|---|
| qwen2.5-coder-7b | abilene | C0 vs C1 | final_violated | +0.000 | [-2.000, +2.000] | 1.000 |
| qwen2.5-coder-7b | abilene | C0 vs C1 | first_regression_step | +3.000 | [+0.000, +9.000] | 0.608 |
| qwen2.5-coder-7b | abilene | C0 vs C1 | completion | +0.133 | [+0.000, +0.333] | 0.076 |
| qwen2.5-coder-7b | abilene | C0 vs C1 | tokens_in | +1255.467 | [+651.667, +1578.133] | 0.000 |
| qwen2.5-coder-7b | abilene | C1 vs C2 | final_violated | -1.000 | [-4.000, +1.000] | 0.598 |
| qwen2.5-coder-7b | abilene | C1 vs C2 | first_regression_step | +1.333 | [-7.000, +11.000] | 0.816 |
| qwen2.5-coder-7b | abilene | C1 vs C2 | completion | -0.044 | [-0.200, +0.067] | 0.608 |
| qwen2.5-coder-7b | abilene | C1 vs C2 | tokens_in | +782.756 | [+542.933, +1136.867] | 0.000 |
| qwen2.5-coder-7b | abilene | C1 vs C3 | final_violated | -2.667 | [-4.000, -1.000] | 0.000 |
| qwen2.5-coder-7b | abilene | C1 vs C3 | first_regression_step | +7.667 | [+6.000, +11.000] | 0.000 |
| qwen2.5-coder-7b | abilene | C1 vs C3 | completion | -0.044 | [-0.133, +0.000] | 0.589 |
| qwen2.5-coder-7b | abilene | C1 vs C3 | tokens_in | +2259.644 | [+1966.400, +2460.933] | 0.000 |
| qwen2.5-coder-7b | abilene | C0 vs C3 | final_violated | -2.667 | [-4.000, -1.000] | 0.000 |
| qwen2.5-coder-7b | abilene | C0 vs C3 | first_regression_step | +10.667 | [+6.000, +15.000] | 0.000 |
| qwen2.5-coder-7b | abilene | C0 vs C3 | completion | +0.089 | [-0.067, +0.333] | 0.608 |
| qwen2.5-coder-7b | abilene | C0 vs C3 | tokens_in | +3515.111 | [+3003.267, +3997.533] | 0.000 |
