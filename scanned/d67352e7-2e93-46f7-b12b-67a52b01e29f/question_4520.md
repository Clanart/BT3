# Q4520: find-asset via liquidate-multi: judge a position against an LTV belonging to a different a

## Question
Does `liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593) let an unprivileged attacker who controls the trait principals supplied per entry reach `find-asset` (mainnet/contracts/market/v0-4-market.clar:584) in a state where it judge a position against an LTV belonging to a different asset set? Given that it returns `none` when the id is absent, and several callers resolve that with `unwrap-panic`, the invariant that each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:584` -> `find-asset`
- Entrypoint: `liquidate-multi` (`mainnet/contracts/market/v0-4-market.clar:1593`), unprivileged and publicly callable
- Attacker controls: the trait principals supplied per entry
- Exploit idea: `find-asset` returns `none` when the id is absent, and several callers resolve that with `unwrap-panic`. Reach it through `liquidate-multi` and judge a position against an LTV belonging to a different asset set.
- Invariant to test: each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `liquidate-multi` twice with the trait principals supplied per entry varied, and assert that the value `find-asset` returns is identical in both runs; a divergence confirms the finding.
