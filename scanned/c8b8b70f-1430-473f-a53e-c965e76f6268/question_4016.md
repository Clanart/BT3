# Q4016: filter-u128 via collateral-add: judge a position against an LTV belonging to a different a

## Question
Does `collateral-add` (mainnet/contracts/market/v0-4-market.clar:1020) let an unprivileged attacker who controls call ordering within the block reach `filter-u128` (mainnet/contracts/registry/v0-egroup.clar:97) in a state where it judge a position against an LTV belonging to a different asset set? Given that it filters a 128-entry bucket list, the invariant that each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/registry/v0-egroup.clar:97` -> `filter-u128`
- Entrypoint: `collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1020`), unprivileged and publicly callable
- Attacker controls: call ordering within the block
- Exploit idea: `filter-u128` filters a 128-entry bucket list. Reach it through `collateral-add` and judge a position against an LTV belonging to a different asset set.
- Invariant to test: each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `collateral-add` twice with call ordering within the block varied, and assert that the value `filter-u128` returns is identical in both runs; a divergence confirms the finding.
