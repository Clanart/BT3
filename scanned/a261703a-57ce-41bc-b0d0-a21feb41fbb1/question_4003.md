# Q4003: calc-liq-factor-exp via liquidate-redeem: produce a price that passes `oracle-price-legal` while bei

## Question
`calc-liq-factor-exp` (mainnet/contracts/market/v0-4-market.clar:708) uses `(/ exp BPS)` as an integer exponent for `pow` and falls back to `sqrti` below BPS. Can an unprivileged caller of `liquidate-redeem` (mainnet/contracts/market/v0-4-market.clar:1604), by choosing the vault whose share price the redemption moves, use that to produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude, violating the invariant that the LTV a position is judged against belongs to the exact asset set it will hold after the call and producing direct theft of user funds at rest or in motion?

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:708` -> `calc-liq-factor-exp`
- Entrypoint: `liquidate-redeem` (`mainnet/contracts/market/v0-4-market.clar:1604`), unprivileged and publicly callable
- Attacker controls: the vault whose share price the redemption moves
- Exploit idea: `calc-liq-factor-exp` uses `(/ exp BPS)` as an integer exponent for `pow` and falls back to `sqrti` below BPS. Reach it through `liquidate-redeem` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: the LTV a position is judged against belongs to the exact asset set it will hold after the call
- Expected Immunefi impact: Critical - direct theft of user funds at rest or in motion
- Fast validation: In `local-testing/tests` on a local fork, drive `liquidate-redeem` with the vault whose share price the redemption moves, then read `calc-liq-factor-exp` state before and after in the same block and assert the two sides of the invariant are equal.
