# Q2281: is-liquidation-paused via liquidate-multi: judge a position against an LTV belonging to a different a

## Question
Can an unprivileged attacker entering through `liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593), controlling which borrowers are placed early versus late in the batch, drive `is-liquidation-paused` (mainnet/contracts/market/v0-4-market.clar:691) — which returns true if the manual pause, the GLOBAL grace entry, OR the per-asset grace entry is live — to judge a position against an LTV belonging to a different asset set, breaking the invariant that every price is attached to the asset it was resolved for, and each asset enters the totals exactly once, and cause direct theft of another user's collateral?

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:691` -> `is-liquidation-paused`
- Entrypoint: `liquidate-multi` (`mainnet/contracts/market/v0-4-market.clar:1593`), unprivileged and publicly callable
- Attacker controls: which borrowers are placed early versus late in the batch
- Exploit idea: `is-liquidation-paused` returns true if the manual pause, the GLOBAL grace entry, OR the per-asset grace entry is live. Reach it through `liquidate-multi` and judge a position against an LTV belonging to a different asset set.
- Invariant to test: every price is attached to the asset it was resolved for, and each asset enters the totals exactly once
- Expected Immunefi impact: Critical - direct theft of another user's collateral
- Fast validation: In `local-testing/tests` on a local fork, drive `liquidate-multi` with which borrowers are placed early versus late in the batch, then read `is-liquidation-paused` state before and after in the same block and assert the two sides of the invariant are equal.
