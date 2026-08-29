# Q5872: linear-interpolate via deposit: satisfy the freshness gate with a timestamp the gate was n

## Question
Does `deposit` (mainnet/contracts/vault/v0-vault-stx.clar:763) let an unprivileged attacker who controls `min-out` reach `linear-interpolate` (mainnet/contracts/vault/v0-vault-stx.clar:221) in a state where it satisfy the freshness gate with a timestamp the gate was never meant to accept? Given that it interpolates between two points, dividing by `(- x2 x1)`, the invariant that every price is attached to the asset it was resolved for, and each asset enters the totals exactly once breaks and the result is direct theft of another user's collateral.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:221` -> `linear-interpolate`
- Entrypoint: `deposit` (`mainnet/contracts/vault/v0-vault-stx.clar:763`), unprivileged and publicly callable
- Attacker controls: `min-out`
- Exploit idea: `linear-interpolate` interpolates between two points, dividing by `(- x2 x1)`. Reach it through `deposit` and satisfy the freshness gate with a timestamp the gate was never meant to accept.
- Invariant to test: every price is attached to the asset it was resolved for, and each asset enters the totals exactly once
- Expected Immunefi impact: Critical - direct theft of another user's collateral
- Fast validation: Set up the position in simnet, call `deposit` with `min-out`, and assert on the printed event plus the post-state that collateral, debt and share totals still reconcile.
