# Q4399: call-liquidate via liquidate-redeem: satisfy the freshness gate with a timestamp the gate was n

## Question
`call-liquidate` (mainnet/contracts/market/v0-4-market.clar:907) invokes `liquidate` with `none` for price-feeds, so a whole batch shares one snapshot. Can an unprivileged caller of `liquidate-redeem` (mainnet/contracts/market/v0-4-market.clar:1604), by choosing the vault whose share price the redemption moves, use that to satisfy the freshness gate with a timestamp the gate was never meant to accept, violating the invariant that the LTV a position is judged against belongs to the exact asset set it will hold after the call and producing theft of unclaimed yield?

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:907` -> `call-liquidate`
- Entrypoint: `liquidate-redeem` (`mainnet/contracts/market/v0-4-market.clar:1604`), unprivileged and publicly callable
- Attacker controls: the vault whose share price the redemption moves
- Exploit idea: `call-liquidate` invokes `liquidate` with `none` for price-feeds, so a whole batch shares one snapshot. Reach it through `liquidate-redeem` and satisfy the freshness gate with a timestamp the gate was never meant to accept.
- Invariant to test: the LTV a position is judged against belongs to the exact asset set it will hold after the call
- Expected Immunefi impact: High - theft of unclaimed yield
- Fast validation: In `local-testing/tests` on a local fork, drive `liquidate-redeem` with the vault whose share price the redemption moves, then read `call-liquidate` state before and after in the same block and assert the two sides of the invariant are equal.
