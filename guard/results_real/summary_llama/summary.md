| model | bed | cond | seqs | half-life (steps) | runs w/ regression | broken at end | intent done | rejected | parse fail | retries/step | tokens in/step |
|---|---|---|---|---|---|---|---|---|---|---|---|
| llama3.1-8b | abilene | C0 | 3 | 7 | 3/3 | 4.33 | 35.6% | 0.0% | 37.8% | 0.00 | 837 |
| llama3.1-8b | abilene | C3 | 3 | over 15 | 0/3 | 0.00 | 17.8% | 82.2% | 73.3% | 2.62 | 5765 |

Paired bootstrap over sequences (difference = second condition minus first; 5000 resamples):

| model | bed | comparison | metric | mean diff | 95% CI | p |
|---|---|---|---|---|---|---|
| llama3.1-8b | abilene | C0 vs C3 | final_violated | -4.333 | [-7.000, -2.000] | 0.000 |
| llama3.1-8b | abilene | C0 vs C3 | first_regression_step | +10.000 | [+6.000, +15.000] | 0.000 |
| llama3.1-8b | abilene | C0 vs C3 | completion | -0.178 | [-0.267, -0.067] | 0.000 |
| llama3.1-8b | abilene | C0 vs C3 | tokens_in | +4928.333 | [+4328.800, +5325.867] | 0.000 |
