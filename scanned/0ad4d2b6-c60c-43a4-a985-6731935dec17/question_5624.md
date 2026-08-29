# Q5624: normalize-pyth via call-ststx-ratio: apply a transform after the gate that was supposed to boun

## Question
Does `call-ststx-ratio` (mainnet/contracts/market/v0-4-market.clar:1015) let an unprivileged attacker who controls the block and transaction position at which the external ratio is fetched reach `normalize-pyth` (mainnet/contracts/market/v0-4-market.clar:297) in a state where it apply a transform after the gate that was supposed to bound its output? Given that it computes `adj` as `(+ expo 8)`, uses an `asserts!` as an early return when `adj` is zero, and converts a signed `int` price with `to-uint`, the invariant that every price is attached to the asset it was resolved for, and each asset enters the totals exactly once breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:297` -> `normalize-pyth`
- Entrypoint: `call-ststx-ratio` (`mainnet/contracts/market/v0-4-market.clar:1015`), unprivileged and publicly callable
- Attacker controls: the block and transaction position at which the external ratio is fetched
- Exploit idea: `normalize-pyth` computes `adj` as `(+ expo 8)`, uses an `asserts!` as an early return when `adj` is zero, and converts a signed `int` price with `to-uint`. Reach it through `call-ststx-ratio` and apply a transform after the gate that was supposed to bound its output.
- Invariant to test: every price is attached to the asset it was resolved for, and each asset enters the totals exactly once
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `call-ststx-ratio` twice with the block and transaction position at which the external ratio is fetched varied, and assert that the value `normalize-pyth` returns is identical in both runs; a divergence confirms the finding.
