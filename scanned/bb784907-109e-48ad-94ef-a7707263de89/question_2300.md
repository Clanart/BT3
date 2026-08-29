# Q2300: get-cached-indexes via liquidate-multi: satisfy the freshness gate with a timestamp the gate was n

## Question
Does `liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593) let an unprivileged attacker who controls how many entries share one price snapshot (price-feeds is passed as none) reach `get-cached-indexes` (mainnet/contracts/market/v0-4-market.clar:944) in a state where it satisfy the freshness gate with a timestamp the gate was never meant to accept? Given that it reads the per-block cache entry that `resolve-ztoken` and the debt conversions depend on, the invariant that a position that holds value can always be priced, and therefore always closed breaks and the result is protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:944` -> `get-cached-indexes`
- Entrypoint: `liquidate-multi` (`mainnet/contracts/market/v0-4-market.clar:1593`), unprivileged and publicly callable
- Attacker controls: how many entries share one price snapshot (price-feeds is passed as none)
- Exploit idea: `get-cached-indexes` reads the per-block cache entry that `resolve-ztoken` and the debt conversions depend on. Reach it through `liquidate-multi` and satisfy the freshness gate with a timestamp the gate was never meant to accept.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `liquidate-multi` twice with how many entries share one price snapshot (price-feeds is passed as none) varied, and assert that the value `get-cached-indexes` returns is identical in both runs; a divergence confirms the finding.
