# Q4541: mask-to-list-iter via borrow: judge a position against an LTV belonging to a different a

## Question
Can an unprivileged attacker entering through `borrow` (mainnet/contracts/market/v0-4-market.clar:1238), controlling the `ft` trait principal, drive `mask-to-list-iter` (mainnet/contracts/market/v0-4-market.clar:440) — which appends under `(unwrap-panic (as-max-len? ... u64))`, aborting if the position exceeds the bound — to judge a position against an LTV belonging to a different asset set, breaking the invariant that the LTV a position is judged against belongs to the exact asset set it will hold after the call, and cause protocol insolvency through uncollateralised debt?

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:440` -> `mask-to-list-iter`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: the `ft` trait principal
- Exploit idea: `mask-to-list-iter` appends under `(unwrap-panic (as-max-len? ... u64))`, aborting if the position exceeds the bound. Reach it through `borrow` and judge a position against an LTV belonging to a different asset set.
- Invariant to test: the LTV a position is judged against belongs to the exact asset set it will hold after the call
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Run the baseline `borrow` call, then the attacker-shaped one with the `ft` trait principal, and assert the attacker's net token balance change is zero or negative.
