# Q3168: get-asset-value via collateral-remove-redeem: apply a transform after the gate that was supposed to boun

## Question
Does `collateral-remove-redeem` (mainnet/contracts/market/v0-4-market.clar:1211) let an unprivileged attacker who controls `amount` used for BOTH the collateral removal and the share redemption reach `get-asset-value` (mainnet/contracts/market/v0-4-market.clar:679) in a state where it apply a transform after the gate that was supposed to bound its output? Given that it resolves a fresh price for a single asset and normalizes with a caller-supplied rounding direction, the invariant that a resolved price passed the confidence and staleness gates in the form the gates were designed for, after every transform breaks and the result is temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:679` -> `get-asset-value`
- Entrypoint: `collateral-remove-redeem` (`mainnet/contracts/market/v0-4-market.clar:1211`), unprivileged and publicly callable
- Attacker controls: `amount` used for BOTH the collateral removal and the share redemption
- Exploit idea: `get-asset-value` resolves a fresh price for a single asset and normalizes with a caller-supplied rounding direction. Reach it through `collateral-remove-redeem` and apply a transform after the gate that was supposed to bound its output.
- Invariant to test: a resolved price passed the confidence and staleness gates in the form the gates were designed for, after every transform
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz `amount` used for BOTH the collateral removal and the share redemption across its boundary values through `collateral-remove-redeem` in simnet and assert `get-asset-value` never returns a value that breaks the invariant.
