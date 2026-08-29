# Q4207: scale-debt-for-liquidation via liquidate: apply a transform after the gate that was supposed to boun

## Question
`scale-debt-for-liquidation` (mainnet/contracts/market/v0-4-market.clar:858) re-scales collateral by `scaled-to-remove / scaled-debt` after the debt was already capped. Can an unprivileged caller of `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382), by choosing which collateral and debt asset pair is targeted, use that to apply a transform after the gate that was supposed to bound its output, violating the invariant that the LTV a position is judged against belongs to the exact asset set it will hold after the call and producing direct theft of another user's collateral?

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:858` -> `scale-debt-for-liquidation`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: which collateral and debt asset pair is targeted
- Exploit idea: `scale-debt-for-liquidation` re-scales collateral by `scaled-to-remove / scaled-debt` after the debt was already capped. Reach it through `liquidate` and apply a transform after the gate that was supposed to bound its output.
- Invariant to test: the LTV a position is judged against belongs to the exact asset set it will hold after the call
- Expected Immunefi impact: Critical - direct theft of another user's collateral
- Fast validation: In `local-testing/tests` on a local fork, drive `liquidate` with which collateral and debt asset pair is targeted, then read `scale-debt-for-liquidation` state before and after in the same block and assert the two sides of the invariant are equal.
