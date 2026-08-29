# Q4742: unwrap-status via borrow: satisfy the freshness gate with a timestamp the gate was n

## Question
Entering through `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) while controlling the order of accrual versus price resolution inside the let, can an unprivileged attacker make `unwrap-status` (mainnet/contracts/registry/v0-assets.clar:111) satisfy the freshness gate with a timestamp the gate was never meant to accept? `unwrap-status` resolves `status` with `unwrap-panic`, so the invariant that each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction would fail, yielding protocol insolvency.

## Target
- File/function: `mainnet/contracts/registry/v0-assets.clar:111` -> `unwrap-status`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: the order of accrual versus price resolution inside the let
- Exploit idea: `unwrap-status` resolves `status` with `unwrap-panic`. Reach it through `borrow` and satisfy the freshness gate with a timestamp the gate was never meant to accept.
- Invariant to test: each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `borrow` twice with the order of accrual versus price resolution inside the let varied, and assert that the value `unwrap-status` returns is identical in both runs; a divergence confirms the finding.
