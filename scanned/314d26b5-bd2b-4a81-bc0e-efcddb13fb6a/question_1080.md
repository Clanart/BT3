# Q1080: find-asset via liquidate-redeem: attach a price resolved for one asset to a different asset

## Question
Does `liquidate-redeem` (mainnet/contracts/market/v0-4-market.clar:1604) let an unprivileged attacker who controls the seized zToken amount that is immediately redeemed reach `find-asset` (mainnet/contracts/market/v0-4-market.clar:584) in a state where it attach a price resolved for one asset to a different asset in the position? Given that it returns `none` when the id is absent, and several callers resolve that with `unwrap-panic`, the invariant that the LTV a position is judged against belongs to the exact asset set it will hold after the call breaks and the result is temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:584` -> `find-asset`
- Entrypoint: `liquidate-redeem` (`mainnet/contracts/market/v0-4-market.clar:1604`), unprivileged and publicly callable
- Attacker controls: the seized zToken amount that is immediately redeemed
- Exploit idea: `find-asset` returns `none` when the id is absent, and several callers resolve that with `unwrap-panic`. Reach it through `liquidate-redeem` and attach a price resolved for one asset to a different asset in the position.
- Invariant to test: the LTV a position is judged against belongs to the exact asset set it will hold after the call
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz the seized zToken amount that is immediately redeemed across its boundary values through `liquidate-redeem` in simnet and assert `find-asset` never returns a value that breaks the invariant.
