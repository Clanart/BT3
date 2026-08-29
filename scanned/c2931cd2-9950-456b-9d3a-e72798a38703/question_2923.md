# Q2923: resolve-or-create via borrow: satisfy the freshness gate with a timestamp the gate was n

## Question
`resolve-or-create` (mainnet/contracts/market/v0-market-vault.clar:143) allocates a user id through `increment` for whatever principal the market names. Can an unprivileged caller of `borrow` (mainnet/contracts/market/v0-4-market.clar:1238), by choosing the `ft` trait principal, use that to satisfy the freshness gate with a timestamp the gate was never meant to accept, violating the invariant that every price is attached to the asset it was resolved for, and each asset enters the totals exactly once and producing direct theft of user funds at rest or in motion?

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:143` -> `resolve-or-create`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: the `ft` trait principal
- Exploit idea: `resolve-or-create` allocates a user id through `increment` for whatever principal the market names. Reach it through `borrow` and satisfy the freshness gate with a timestamp the gate was never meant to accept.
- Invariant to test: every price is attached to the asset it was resolved for, and each asset enters the totals exactly once
- Expected Immunefi impact: Critical - direct theft of user funds at rest or in motion
- Fast validation: In `local-testing/tests` on a local fork, drive `borrow` with the `ft` trait principal, then read `resolve-or-create` state before and after in the same block and assert the two sides of the invariant are equal.
